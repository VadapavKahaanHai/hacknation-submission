"""
calibrate.py: measure what the confidence rules actually do on the validation set.
Run in Colab after training (needs model.keras, data/, crop.db, train.py in /content):
    !python calibrate.py

It replays the exact app logic per image:
  1. restrict probabilities to the picked crop + 'unknown', renormalize
  2. apply thresholds from crop.db meta (high / uncertain / unknown)
For 'unknown' images (leaves of other crops) it simulates the farmer picking each supported crop.

Output: how often each level fires, how accurate 'high' really is, a threshold sweep,
and calibration.json for the pitch. Validation is same-distribution, so these are optimistic.
"""
import json
import sqlite3

import numpy as np
import tensorflow as tf

from train import DATA, IMG, SEED, read_manifest

m = read_manifest()
labels = m["labels"]
con = sqlite3.connect("crop.db")
meta = dict(con.execute("SELECT key, value FROM meta"))
T_HIGH, T_UNK, T_MARGIN = (float(meta[k]) for k in ("threshold_high", "threshold_unknown", "threshold_margin"))
crop_of = dict(con.execute("SELECT disease_id, crop_id FROM diseases"))
crops = sorted({crop_of[l] for l in labels if crop_of.get(l)})
UNK = labels.index("unknown")

# Same split as training (same seed, same validation_split), so these are held-out images.
va = tf.keras.utils.image_dataset_from_directory(
    str(DATA), labels="inferred", label_mode="int", class_names=labels,
    image_size=(IMG, IMG), batch_size=32, seed=SEED, validation_split=0.15, subset="validation", shuffle=True)
model = tf.keras.models.load_model("model.keras")
P, Y = [], []
for xb, yb in va:
    P.append(model.predict(xb, verbose=0)); Y.append(yb.numpy())
P, Y = np.concatenate(P), np.concatenate(Y)
print(f"validation images: {len(Y)}")


def restrict(p, crop):
    keep = [i for i, l in enumerate(labels) if crop_of.get(l) == crop or i == UNK]
    q = np.zeros_like(p); q[keep] = p[keep]
    return q / q.sum()


def decide(q, t_high=T_HIGH, t_unk=T_UNK, t_margin=T_MARGIN):
    order = np.argsort(-q)
    top, p1, p2 = order[0], q[order[0]], q[order[1]]
    if top == UNK or p1 < t_unk:
        return "unknown", top
    if p1 >= t_high and p1 - p2 >= t_margin:
        return "high", top
    return "uncertain", top


def replay(t_high=T_HIGH, t_unk=T_UNK, t_margin=T_MARGIN):
    rows = []  # (true_label, level, predicted)
    for p, y in zip(P, Y):
        picks = crops if y == UNK else [crop_of[labels[y]]]
        for c in picks:
            lvl, pred = decide(restrict(p, c), t_high, t_unk, t_margin)
            rows.append((y, lvl, pred))
    return rows


def summarize(rows):
    n = len(rows)
    lv = {k: sum(r[1] == k for r in rows) for k in ("high", "uncertain", "unknown")}
    high = [r for r in rows if r[1] == "high"]
    high_acc = np.mean([r[0] == r[2] for r in high]) if high else float("nan")
    # dangerous = app says HIGH confidence and is wrong
    dangerous = sum(r[0] != r[2] for r in high)
    unk_rows = [r for r in rows if r[0] == UNK]
    unk_caught = np.mean([r[1] == "unknown" for r in unk_rows]) if unk_rows else float("nan")
    real = [r for r in rows if r[0] != UNK]
    real_rejected = np.mean([r[1] == "unknown" for r in real]) if real else float("nan")
    return {"n_decisions": n, "high": lv["high"], "uncertain": lv["uncertain"], "unknown": lv["unknown"],
            "high_share": round(lv["high"] / n, 3), "high_accuracy": round(float(high_acc), 3),
            "high_and_wrong": int(dangerous), "other_crop_leaves_caught_as_unknown": round(float(unk_caught), 3),
            "real_crop_leaves_wrongly_rejected": round(float(real_rejected), 3)}


cur = summarize(replay())
print(f"\nCURRENT thresholds high>={T_HIGH} margin>={T_MARGIN} unknown<{T_UNK}")
for k, v in cur.items():
    print(f"  {k:38s} {v}")

print("\nper class (current thresholds):")
per = {}
rows = replay()
for i, l in enumerate(labels):
    rr = [r for r in rows if r[0] == i]
    if not rr:
        continue
    per[l] = {"n": len(rr), "high": round(np.mean([r[1] == "high" for r in rr]), 2),
              "correct_when_high": round(float(np.mean([r[0] == r[2] for r in rr if r[1] == "high"])), 2)
              if any(r[1] == "high" for r in rr) else None}
    print(f"  {l:28s} n={len(rr):4d}  shown as high {per[l]['high']:.2f}  correct when high {per[l]['correct_when_high']}")

print("\nsweep of threshold_high (margin and unknown fixed):")
print(f"  {'t_high':>6} {'high share':>10} {'high acc':>9} {'high+wrong':>10}")
sweep = []
for t in (0.5, 0.6, 0.7, 0.8, 0.9):
    s = summarize(replay(t_high=t))
    sweep.append({"t_high": t, **s})
    print(f"  {t:6.2f} {s['high_share']:10.3f} {s['high_accuracy']:9.3f} {s['high_and_wrong']:10d}")

def clean(o):
    if isinstance(o, float) and o != o:
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


json.dump(clean({"model_version": m["model_version"], "thresholds": {"high": T_HIGH, "margin": T_MARGIN, "unknown": T_UNK},
           "current": cur, "per_class": per, "sweep_high": sweep,
           "note": "validation set, same distribution as training; field accuracy will be lower"}),
          open("calibration.json", "w"), indent=1)
print("\nwrote calibration.json")
