"""
dequant.py: make a TF.js model safe for iPhone Safari by storing every weight as 4 bytes.

Why: the float16 export mixes 2-byte and 4-byte weights in one file, which leaves some
4-byte weights at addresses that are not a multiple of 4. Safari refuses to read those
("ArrayBuffer length minus the byteOffset is not a multiple of the element size").
This expands float16 weights back to float32 (same values, so same accuracy as float16)
and writes a new model.json + .bin. Every weight is then 4 bytes, so all are aligned.

Usage (on the Mac, inside repo/web/model):   python3 dequant.py
Keeps a backup of the originals in ./f16_backup/
"""
import json
import math
import shutil
from pathlib import Path

import numpy as np

src = Path(".")
m = json.loads((src / "model.json").read_text())
groups = m["weightsManifest"]
data = b"".join((src / p).read_bytes() for g in groups for p in g["paths"])

backup = src / "f16_backup"
backup.mkdir(exist_ok=True)
for f in ["model.json"] + [p for g in groups for p in g["paths"]]:
    shutil.copy2(src / f, backup / f)

out, specs, off = bytearray(), [], 0
for g in groups:
    for w in g["weights"]:
        n = math.prod(w["shape"]) if w["shape"] else 1
        q = (w.get("quantization") or {}).get("dtype")
        if q == "float16":
            arr = np.frombuffer(data, dtype="<f2", count=n, offset=off).astype("<f4")
            off += n * 2
        elif q == "uint8":
            qq = w["quantization"]
            raw = np.frombuffer(data, dtype=np.uint8, count=n, offset=off).astype("<f4")
            arr = (raw * qq["scale"] + qq["min"]).astype("<f4")
            off += n
        elif q is None:
            dt = {"float32": "<f4", "int32": "<i4", "bool": "u1"}[w["dtype"]]
            size = np.dtype(dt).itemsize
            arr = np.frombuffer(data, dtype=dt, count=n, offset=off)
            off += n * size
        else:
            raise SystemExit(f"unsupported quantization {q} for {w['name']}")
        spec = {k: v for k, v in w.items() if k != "quantization"}
        out += arr.tobytes()
        specs.append(spec)

assert off == len(data), f"read {off} bytes but file has {len(data)}"
for g in groups:
    for p in g["paths"]:
        (src / p).unlink()
(src / "group1-shard1of1.bin").write_bytes(bytes(out))
m["weightsManifest"] = [{"paths": ["group1-shard1of1.bin"], "weights": specs}]
(src / "model.json").write_text(json.dumps(m))

# alignment check: every weight must start at a multiple of 4
pos, bad = 0, 0
for w in specs:
    if pos % 4:
        bad += 1
    pos += (math.prod(w["shape"]) if w["shape"] else 1) * 4
print(f"done: {len(specs)} weights, {len(out):,} bytes, misaligned weights: {bad}")
print("originals saved in f16_backup/")
