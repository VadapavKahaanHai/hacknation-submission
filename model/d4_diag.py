"""
d4_diag.py: how similar are the coffee images really? Picks the duplicate cut-off from data.
Run in Colab from /content:   !python d4_diag.py
For each class: unique files after exact dedup, and a histogram of each image's distance
(out of 256 bits) to its nearest other image, under any rotation/flip.
Copies of one leaf pile up near 0; genuinely different leaves sit further out.
"""
import hashlib
import random
import sys
from pathlib import Path

import numpy as np

sys.argv = ["x"]
from train import d4_variants, norm, contract, IMG_EXT  # noqa: E402

labels, aliases = contract("v0.3")
cmap = aliases["jmuben"]
root = Path("JMuBEN")
by_class = {}
for f in root.rglob("*"):
    if f.suffix.lower() not in IMG_EXT or f.name.startswith(".") or "__MACOSX" in f.parts:
        continue
    cls = next((cmap[norm(p)] for p in reversed(f.relative_to(root).parts[:-1]) if norm(p) in cmap), None)
    if cls:
        by_class.setdefault(cls, []).append(f)

bins = [0, 6, 11, 16, 21, 27, 41, 61, 257]
print("nearest-neighbour distance (bits of 256) per unique image")
print(f"{'class':20s} {'files':>6} {'unique':>6} | " + " ".join(f"{a}-{b - 1:>3}" for a, b in zip(bins, bins[1:])))
for cls in sorted(by_class):
    seen, uniq = set(), []
    for f in by_class[cls]:
        h = hashlib.md5(f.read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h); uniq.append(f)
    random.Random(0).shuffle(uniq)
    sample = uniq[:600]
    vs = [d4_variants(f) for f in sample]
    bad = sum(v is None for v in vs)
    V = np.stack([v for v in vs if v is not None])               # (n, 8, 256)
    sample = [f for f, v in zip(sample, vs) if v is not None]
    base = V[:, 0, :]                                            # (n, 256)
    nn = []
    for i in range(len(sample)):
        d = (V[i][:, None, :] != base[None, :, :]).sum(-1).min(0)  # (n,) min over 8 variants
        d[i] = 999
        nn.append(d.min())
    hist, _ = np.histogram(nn, bins=bins)
    print(f"{cls:20s} {len(by_class[cls]):6d} {len(uniq):6d} | " + " ".join(f"{h:6d}" for h in hist)
          + ("   (first 600 sampled)" if len(uniq) > 600 else "") + (f"  corrupt {bad}" if bad else ""))
