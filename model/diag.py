"""
diag.py: why do Keras and TF.js disagree? Separates "image decoding" from "conversion bug".
Run in Colab after verify.py (needs ref.json, model.keras):   !python diag.py   then   !node diag.mjs web_model_f32

Writes ref_px.json: the exact PIL-decoded pixels Keras saw, so TF.js can be fed identical input.
Also prints, Python only, how much Keras flips when the SAME jpeg is decoded by TensorFlow instead of PIL.
"""
import json

import numpy as np
import tensorflow as tf

ref = json.load(open("ref.json"))
model = tf.keras.models.load_model("model.keras")

px, k_pil, k_tf, diffs = [], [], [], []
for r in ref:
    a = tf.keras.utils.img_to_array(tf.keras.utils.load_img(r["path"], target_size=(224, 224)))   # PIL, as verify.py
    b = tf.cast(tf.io.decode_jpeg(tf.io.read_file(r["path"]), channels=3), tf.float32)           # TF decoder, like tfjs-node
    b = tf.image.resize(b, (224, 224), method="bilinear").numpy()
    px.append(a.astype(np.uint8).ravel().tolist())
    k_pil.append(model.predict(a[None], verbose=0)[0])
    k_tf.append(model.predict(b[None], verbose=0)[0])
    diffs.append(np.abs(a - b))

k_pil, k_tf = np.array(k_pil), np.array(k_tf)
d = np.stack(diffs)
flips = int(np.sum(k_pil.argmax(1) != k_tf.argmax(1)))
print(f"pixel difference PIL vs TF decoder: mean {d.mean():.2f}, max {d.max():.0f} (0..255 scale)")
print(f"Keras on PIL pixels vs Keras on TF-decoded pixels: argmax flips {flips}/{len(ref)}, "
      f"max prob diff {np.abs(k_pil - k_tf).max():.4f}")
for r, p, q in zip(ref, k_pil, k_tf):
    if p.argmax() != q.argmax() or np.abs(p - q).max() > 0.05:
        print(f"  {r['label']:28s} PIL top {p.max():.2f}  TF-decoded top {q.max():.2f}  max diff {np.abs(p - q).max():.2f}")
json.dump({"px": px, "keras": k_pil.round(5).tolist(), "shape": [224, 224, 3]}, open("ref_px.json", "w"))
print("wrote ref_px.json")
