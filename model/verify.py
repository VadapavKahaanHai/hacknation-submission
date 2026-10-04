"""
verify.py: write Keras reference predictions for 2 images per class -> ref.json.
verify.mjs then runs the SAME images through the exported TF.js model and compares.
This tests conversion parity (same answers before/after export), not accuracy.
Run in Colab after `python train.py export`:   !python verify.py
"""
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

from train import DATA, read_manifest

CANONICAL = read_manifest()["labels"]

model = tf.keras.models.load_model("model.keras")
items = []
for cid in CANONICAL:
    for p in sorted((DATA / cid).glob("*.jpg"))[:2]:
        x = tf.keras.utils.img_to_array(tf.keras.utils.load_img(p, target_size=(224, 224)))  # float 0..255
        probs = model.predict(x[None], verbose=0)[0]
        items.append({"path": str(p), "label": cid, "keras": [round(float(v), 5) for v in probs]})
        print(f"{cid:28s} keras_top={CANONICAL[int(np.argmax(probs))]:28s} p={probs.max():.3f}")
Path("ref.json").write_text(json.dumps(items))
print(f"wrote ref.json with {len(items)} images")
