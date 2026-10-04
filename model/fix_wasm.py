"""
fix_wasm.py: make an exported TF.js model loadable by the WASM backend (fast on iPhone).

The TF.js converter folds the "+3" of MobileNetV3's hard-swish into some fused convolutions
as a SCALAR bias (rank 0). WebGL and CPU accept that, WASM refuses:
  "FusedConv2D only supports rank-1 bias but got rank 0"
This rewrites each such bias as a rank-1 vector (same value per output channel), which is
mathematically identical, and stores all weights as float32 in one shard.

Usage:  python3 fix_wasm.py <model_folder>      (folder with model.json + .bin)
Run after export (train.py export calls it automatically) and after dequant.py if used.
"""
import copy
import json
import math
import sys
from pathlib import Path

import numpy as np

SIZE = {"float32": 4, "int32": 4, "bool": 1}
NP = {"float32": "<f4", "int32": "<i4", "bool": "u1"}


def fix(folder):
    folder = Path(folder)
    m = json.loads((folder / "model.json").read_text())
    groups = m["weightsManifest"]
    data = b"".join((folder / p).read_bytes() for g in groups for p in g["paths"])
    specs = [w for g in groups for w in g["weights"]]
    if any(w.get("quantization") for w in specs):
        sys.exit("Model has quantized weights. Run dequant.py first, then fix_wasm.py.")

    # read every weight into memory
    arrays, off = {}, 0
    for w in specs:
        n = math.prod(w["shape"]) if w["shape"] else 1
        arrays[w["name"]] = np.frombuffer(data, dtype=NP[w["dtype"]], count=n, offset=off).reshape(w["shape"])
        off += n * SIZE[w["dtype"]]
    assert off == len(data), f"read {off} bytes, file has {len(data)}"

    fixed, new_nodes, replaced = 0, [], set()
    by_name = {n["name"]: n for n in m["modelTopology"]["node"]}
    for node in m["modelTopology"]["node"]:
        if node["op"] not in ("_FusedConv2D", "FusedDepthwiseConv2dNative", "_FusedMatMul"):
            continue
        ins = node.get("input", [])
        if len(ins) < 3:
            continue
        bias_name = ins[2].split(":")[0]
        if bias_name not in arrays or arrays[bias_name].ndim != 0:
            continue
        filt = arrays[ins[1].split(":")[0]]
        if node["op"] == "_FusedConv2D":
            out = filt.shape[3]
        elif node["op"] == "FusedDepthwiseConv2dNative":
            out = filt.shape[2] * filt.shape[3]
        else:
            tb = node.get("attr", {}).get("transpose_b", {}).get("b", False)
            out = filt.shape[0] if tb else filt.shape[1]
        new_name = f"{node['name']}/rank1_bias"
        arrays[new_name] = np.full((out,), float(arrays[bias_name]), dtype="<f4")
        specs.append({"name": new_name, "shape": [out], "dtype": "float32"})
        # every weight needs a matching Const node in the graph; copy the scalar one, fix its shape
        const = copy.deepcopy(by_name[bias_name])
        const["name"] = new_name
        const["attr"]["value"]["tensor"]["tensorShape"] = {"dim": [{"size": str(out)}]}
        new_nodes.append(const)
        ins[2] = new_name
        replaced.add(bias_name)
        fixed += 1

    if fixed == 0:
        print(f"fix_wasm: nothing to fix in {folder}")
        return 0
    m["modelTopology"]["node"].extend(new_nodes)
    # the replaced scalar constants are now unused; TF.js would treat them as extra outputs, so drop them
    used = {i.split(":")[0].lstrip("^") for n in m["modelTopology"]["node"] for i in n.get("input", [])}
    orphans = {b for b in replaced if b not in used}
    m["modelTopology"]["node"] = [n for n in m["modelTopology"]["node"] if n["name"] not in orphans]
    specs[:] = [w for w in specs if w["name"] not in orphans]
    blob = b"".join(arrays[w["name"]].astype(NP[w["dtype"]]).tobytes() for w in specs)
    for g in groups:
        for p in g["paths"]:
            (folder / p).unlink()
    (folder / "group1-shard1of1.bin").write_bytes(blob)
    m["weightsManifest"] = [{"paths": ["group1-shard1of1.bin"], "weights": specs}]
    (folder / "model.json").write_text(json.dumps(m))
    print(f"fix_wasm: rewrote {fixed} scalar biases as vectors in {folder} ({len(blob):,} bytes)")
    return fixed


if __name__ == "__main__":
    fix(sys.argv[1] if len(sys.argv) > 1 else ".")
