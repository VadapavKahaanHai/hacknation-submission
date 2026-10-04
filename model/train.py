"""
train.py: Sprint 2 model for crop set C (bean + coffee + maize).
Fine-tune MobileNetV3Small, export a TF.js graph model for offline browser inference.

Single source of truth: crop.db. This script has NO label mapping of its own.
  class list + order  <- model_classes (v0.2 full, v0.2b bean_maize)
  raw label mapping   <- label_aliases

Colab (T4), files in /content: train.py, crop.db, verify.py, verify.mjs
Data comes from Hugging Face (pip install datasets). TFDS is NOT used: its iBean URL returns 403.
  !python train.py smoke                         # export an untrained model first
  !python train.py prep full JMuBEN              # bean + coffee + maize, 13 classes (model v0.3)
  !python train.py prep bean_maize               # fallback if coffee fails (no coffee folder needed)
  !python train.py train
  !python train.py export
prep writes data/manifest.json; train and export read it, so they can never disagree with prep.
"""
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import tensorflow as tf

DB = "crop.db"
CROPSETS = {"full": "v0.3", "bean_maize": "v0.2b"}
IMG = 224
CAP = 400                 # max images per class
COFFEE_MIN = int(os.environ.get("COFFEE_MIN", 150))   # fewer distinct images than this = stop
DATA = Path("data")
MANIFEST = DATA / "manifest.json"
SEED = 42
IMG_EXT = {".jpg", ".jpeg", ".png"}


def norm(raw: str) -> str:
    """Same rule as cropdb.normalize_label."""
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def contract(version):
    con = sqlite3.connect(DB)
    labels = [r[0] for r in con.execute(
        "SELECT disease_id FROM model_classes WHERE model_version=? ORDER BY class_index", (version,))]
    if not labels:
        sys.exit(f"No model_classes for {version} in {DB}. Is crop.db the content 0.3.0 build?")
    aliases = {}
    for src, raw, did in con.execute("SELECT source, raw_label, disease_id FROM label_aliases"):
        aliases.setdefault(src, {})[norm(raw)] = did
    return labels, aliases


def read_manifest():
    if not MANIFEST.exists():
        sys.exit("data/manifest.json missing. Run prep first.")
    return json.loads(MANIFEST.read_text())


def save(img, cid, n):
    d = DATA / cid
    d.mkdir(parents=True, exist_ok=True)
    tf.keras.utils.save_img(str(d / f"{n:05d}.jpg"), tf.image.resize(img, (IMG, IMG)).numpy())


# ---------------------------------------------------------------- data prep

HF_BEANS = "AI-Lab-Makerere/beans"                              # MIT, same images as iBean
HF_PLANTVILLAGE = "BrandonFors/Plant-Diseases-PlantVillage-Dataset"  # 38 classes, images + ClassLabel


def hf_dataset(repo):
    """All splits of a Hugging Face image dataset, plus its ClassLabel column name and label names."""
    from datasets import ClassLabel, concatenate_datasets, load_dataset
    dd = load_dataset(repo)
    ds = concatenate_datasets([dd[s] for s in dd])
    col = next(c for c, f in ds.features.items() if isinstance(f, ClassLabel))
    return ds, col, ds.features[col].names


def take(ds, col, wanted, label_names):
    """wanted: {label_index: (cid, cap)}. Returns [(row_index, cid)] without decoding any image."""
    by_label = {}
    for i, lab in enumerate(ds[col]):          # reads the label column only, fast
        if lab in wanted:
            by_label.setdefault(lab, []).append(i)
    picked = []
    for lab, idxs in by_label.items():
        random.Random(SEED + lab).shuffle(idxs)
        cid, cap = wanted[lab]
        picked += [(i, cid) for i in idxs[:cap]]
        print(f"  {label_names[lab]:45s} -> {cid:28s} {min(len(idxs), cap)}/{len(idxs)}")
    return picked


def save_rows(ds, picked, counts):
    for i, cid in picked:
        if counts[cid] >= CAP:
            continue
        img = np.asarray(ds[i]["image"].convert("RGB"))
        save(img, cid, counts[cid]); counts[cid] += 1


def load_beans(aliases, labels, counts):
    ds, col, names = hf_dataset(HF_BEANS)
    print(f"beans labels: {names}")
    wanted = {}
    for li, raw in enumerate(names):
        if norm(raw) not in aliases["ibean"]:
            sys.exit(f"Unmapped iBean label '{raw}'. Add it to label_aliases.")
        cid = aliases["ibean"][norm(raw)]
        if cid in labels:
            wanted[li] = (cid, CAP)
    save_rows(ds, take(ds, col, wanted, names), counts)


def load_plantvillage(aliases, labels, counts):
    ds, col, names = hf_dataset(HF_PLANTVILLAGE)
    print(f"plantvillage labels ({len(names)}): {names}")
    pv = aliases["plantvillage"]
    corn = [n for n in names if n.lower().startswith("corn")]
    if len(corn) != 4:
        sys.exit(f"Expected 4 maize labels, found {corn}. Send these names to update label_aliases.")
    unmapped_corn = [n for n in corn if norm(n) not in pv]
    if unmapped_corn:
        sys.exit(f"PlantVillage maize labels not in label_aliases: {unmapped_corn}. Add them, then rerun.")
    if any(pv.get(norm(n)) == "unknown" for n in corn):
        sys.exit("A maize label maps to 'unknown'. Healthy maize must never train as unknown.")
    unk = [li for li, n in enumerate(names) if pv.get(norm(n)) == "unknown"]
    if len(unk) < 5:
        sys.exit(f"Only {len(unk)} PlantVillage labels matched 'unknown' aliases. Send the label list above.")
    per_unknown = CAP // len(unk) + 1
    wanted = {}
    for li, raw in enumerate(names):
        cid = pv.get(norm(raw))
        if cid and cid in labels:
            wanted[li] = (cid, per_unknown if cid == "unknown" else CAP)
    save_rows(ds, take(ds, col, wanted, names), counts)


D4_BITS_THRESHOLD = int(os.environ.get("D4_THRESHOLD", 15))   # of 256 bits; tune with d4_diag.py


def d4_variants(path):
    """8 average hashes (16x16 = 256 bits) of a grayscale thumbnail: every rotation and flip.
    Brightness changes do not matter because each bit is 'above or below this image's own mean'."""
    from PIL import Image
    try:
        g = np.asarray(Image.open(path).convert("L").resize((16, 16)), dtype=np.float32)
    except Exception:
        return None                 # corrupt or unreadable file
    out = []
    for k in range(4):
        r = np.rot90(g, k)
        for v in (r, np.fliplr(r)):
            out.append((v > v.mean()).ravel())
    return np.stack(out)            # (8, 256) bool


def load_coffee(root, aliases, labels, counts, source="jmuben"):
    """Folder loader for coffee (JMuBEN + JMuBEN2 class folders under one root).
    Class = nearest folder whose name is in label_aliases(source).
    Drops byte-identical files, then rotated/flipped/brightened copies of the same leaf (d4_variants, near match),
    so one physical leaf cannot land in both train and validation."""
    root = Path(root)
    if not root.exists():
        sys.exit(f"Coffee folder '{root}' not found. Unzip JMuBEN there, or use: prep bean_maize")
    cmap = aliases.get(source, {})
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMG_EXT and not p.name.startswith(".")
                   and "__MACOSX" not in p.parts)
    if not files:
        sys.exit(f"No images under {root}. Is the zip nested? Unzip inner zips, then rerun.")

    unmapped_dirs, by_class = set(), {}
    for f in files:
        parts = f.relative_to(root).parts
        cls = next((cmap[norm(p)] for p in reversed(parts[:-1]) if norm(p) in cmap), None)
        if cls is None:
            unmapped_dirs.add(str(f.parent.relative_to(root)))
            continue
        if cls in labels:
            by_class.setdefault(cls, []).append(f)
    if unmapped_dirs:
        sys.exit(f"Coffee folders not in label_aliases (source '{source}'). Send these names:\n  "
                 + "\n  ".join(sorted(unmapped_dirs)[:30]))

    for cls in [c for c in labels if c.startswith("coffee_")]:
        fl = by_class.get(cls, [])
        random.Random(SEED).shuffle(fl)
        seen_md5, kept_hashes, keep, exact, near, bad = set(), [], [], 0, 0, 0
        for f in fl:
            if len(keep) >= CAP:
                break
            h = hashlib.md5(f.read_bytes()).hexdigest()
            if h in seen_md5:
                exact += 1; continue
            seen_md5.add(h)
            v = d4_variants(f)
            if v is None:
                bad += 1; continue
            if kept_hashes:
                dist = (v[:, None, :] != np.stack(kept_hashes)[None, :, :]).sum(-1)   # (8, kept)
                if dist.min() <= D4_BITS_THRESHOLD:
                    near += 1; continue
            kept_hashes.append(v[0])
            keep.append(f)
        print(f"  {cls:22s} files {len(fl):6d}  exact dupes {exact:5d}  rotated/flipped dupes {near:5d}  "
              f"corrupt {bad:3d}  kept {len(keep)}")
        if len(keep) < COFFEE_MIN:
            sys.exit(f"Only {len(keep)} distinct images for {cls} (< {COFFEE_MIN}). "
                     "Use: prep bean_maize  and  cropdb.py set-cropset crop.db bean_maize")
        for f in keep:
            img = tf.io.decode_image(f.read_bytes(), channels=3, expand_animations=False)
            save(img, cls, counts[cls]); counts[cls] += 1


def prep(cropset, coffee_dir=None):
    if cropset not in CROPSETS:
        sys.exit(f"cropset must be one of {list(CROPSETS)}")
    version = CROPSETS[cropset]
    labels, aliases = contract(version)
    shutil.rmtree(DATA, ignore_errors=True)
    counts = {c: 0 for c in labels}

    if any(c.startswith("coffee_") for c in labels):   # fail fast, before the long downloads
        load_coffee(coffee_dir or "JMuBEN", aliases, labels, counts)
        print("coffee done", {k: v for k, v in counts.items() if v})
    load_beans(aliases, labels, counts)
    print("beans done", {k: v for k, v in counts.items() if v})
    load_plantvillage(aliases, labels, counts)
    print("plant_village done")

    # Optional: add your own non-leaf phone photos to data/unknown/ now (soil, hands, walls).
    print("FINAL COUNTS", counts)
    low = [c for c, n in counts.items() if n < 50]
    if low:
        sys.exit(f"Too few images for {low}.")
    MANIFEST.write_text(json.dumps(
        {"cropset": cropset, "model_version": version, "labels": labels, "counts": counts}, indent=1))
    print(f"wrote {MANIFEST}: {cropset} / {version} / {len(labels)} classes")


# ---------------------------------------------------------------- model

def datasets(labels):
    kw = dict(directory=str(DATA), labels="inferred", label_mode="int",
              class_names=labels,  # forces index order = model_classes order
              image_size=(IMG, IMG), batch_size=32, seed=SEED, validation_split=0.15)
    train = tf.keras.utils.image_dataset_from_directory(subset="training", **kw)
    val = tf.keras.utils.image_dataset_from_directory(subset="validation", **kw)
    assert train.class_names == labels, train.class_names
    return train.prefetch(tf.data.AUTOTUNE), val.prefetch(tf.data.AUTOTUNE)


def build_model(n_classes, weights="imagenet"):
    # Input: float32 RGB [0,255], (None,224,224,3). MobileNetV3 rescales internally.
    aug = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal_and_vertical"),
        tf.keras.layers.RandomRotation(0.15),
        tf.keras.layers.RandomZoom(0.2),
        tf.keras.layers.RandomBrightness(0.25, value_range=(0, 255)),
        tf.keras.layers.RandomContrast(0.25),
    ], name="augment")
    base = tf.keras.applications.MobileNetV3Small(
        input_shape=(IMG, IMG, 3), include_top=False, weights=weights,
        include_preprocessing=True, pooling="avg", minimalistic=False)
    base.trainable = False
    inp = tf.keras.Input((IMG, IMG, 3), name="image")
    x = aug(inp)
    x = base(x, training=False)
    x = tf.keras.layers.Dropout(0.3)(x)
    out = tf.keras.layers.Dense(n_classes, activation="softmax", name="probs")(x)
    return tf.keras.Model(inp, out), base


def train():
    m = read_manifest()
    labels = m["labels"]
    tr, va = datasets(labels)
    model, base = build_model(len(labels))
    # rare classes (e.g. coffee_cercospora, ~73 distinct leaves) count more, so they are not ignored
    cnt = [max(m["counts"].get(c, 0), 1) for c in labels]
    cw = {i: sum(cnt) / (len(cnt) * n) for i, n in enumerate(cnt)}
    print("class weights:", {labels[i]: round(w, 2) for i, w in cw.items()})
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(tr, validation_data=va, epochs=5, class_weight=cw)
    base.trainable = True
    for layer in base.layers[:-30]:
        layer.trainable = False
    for layer in base.layers:
        if isinstance(layer, tf.keras.layers.BatchNormalization):
            layer.trainable = False
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-4), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(tr, validation_data=va, epochs=4, class_weight=cw,
              callbacks=[tf.keras.callbacks.EarlyStopping(patience=2, restore_best_weights=True)])
    model.save("model.keras")
    per_class_report(model, va, labels)


def per_class_report(model, va, labels):
    y, p = [], []
    for xb, yb in va:
        y.extend(yb.numpy()); p.extend(np.argmax(model.predict(xb, verbose=0), 1))
    y, p = np.array(y), np.array(p)
    print("\nper-class recall (validation, same distribution, optimistic; coffee inflated by near-duplicates):")
    for i, c in enumerate(labels):
        mask = y == i
        print(f"  {c:28s} {np.mean(p[mask] == i):.2f}  (n={mask.sum()})" if mask.any() else f"  {c:28s} n=0")


def export(model_path="model.keras", out="web_model", version=None, labels=None):
    if model_path:
        m = read_manifest()
        version, labels = m["model_version"], m["labels"]
        model = tf.keras.models.load_model(model_path)
    else:
        model = build_model(len(labels), weights=None)[0]
    shutil.rmtree("saved_model", ignore_errors=True); shutil.rmtree(out, ignore_errors=True)
    # Keras 3 model.export() produces a graph tensorflowjs_converter cannot freeze ("Identity is not
    # in graph"), and the augmentation layers' seed state blocks tf.saved_model.save. So export an
    # inference-only copy that reuses the trained backbone + head (Dropout is identity at inference).
    base = next(l for l in model.layers if l.name.lower().startswith("mobilenetv3"))
    head = model.get_layer("probs")
    inp = tf.keras.Input((IMG, IMG, 3), name="image")
    inf = tf.keras.Model(inp, head(base(inp, training=False)), name="inference")
    probe = np.random.uniform(0, 255, (2, IMG, IMG, 3)).astype("float32")
    assert np.allclose(model(probe, training=False), inf(probe), atol=1e-5), "inference copy differs"

    @tf.function(input_signature=[tf.TensorSpec([None, IMG, IMG, 3], tf.float32, name="image")])
    @tf.autograph.experimental.do_not_convert
    def serve(image):
        return {"probs": inf(image, training=False)}

    holder = tf.Module()
    holder.model, holder.serve = inf, serve
    tf.saved_model.save(holder, "saved_model", signatures={"serving_default": serve})
    subprocess.run([
        "tensorflowjs_converter",
        "--input_format=tf_saved_model",
        "--output_format=tfjs_graph_model",
        "--signature_name=serving_default",
        # no quantization: mixed 2 and 4 byte weights misalign on iPhone Safari. float32 ~3.7 MB, all aligned.
        "saved_model", out], check=True)
    Path(out, "labels.json").write_text(json.dumps(
        {"model_version": version, "input_size": IMG, "input_range": [0, 255], "labels": labels}, indent=1))
    size = sum(f.stat().st_size for f in Path(out).iterdir())
    print(f"exported {out}/ ({version}, {len(labels)} classes): {size/1e6:.2f} MB")


if __name__ == "__main__":
    random.seed(SEED); tf.random.set_seed(SEED)
    cmd = sys.argv[1]
    if cmd == "prep":
        prep(sys.argv[2] if len(sys.argv) > 2 else "full", sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "smoke":
        v = CROPSETS["full"]
        export(model_path=None, out="web_model_smoke", version=v, labels=contract(v)[0])
    elif cmd == "train":
        train()
    elif cmd == "export":
        export()
