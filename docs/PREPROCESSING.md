# Coffee image preprocessing

`scripts/preprocess_coffee.py` prepares image data without modifying source files or
loading any speech files. Run it on the development computer, not the phone.
Completed cleaning outputs already exist under `processed/coffee-v1`. Reuse them
with `python scripts/pipeline.py`; automated tests use temporary synthetic fixtures.

## Run

From PowerShell in `D:\Hackathon`:

```powershell
python -m pip install -r requirements-preprocessing.txt
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/preprocess_coffee.py prepare --jmuben Data/JMuBEN --plantdoc Data/PlantDoc --output processed/coffee-v2
```

Dependencies are already installed in the current workspace. The input inventory
found 44,166 JMuBEN image-extension files and 2,573 PlantDoc image-extension files,
including its documentation illustration. These are discovery counts, **not**
decoded/validated image counts. Full preparation will determine actual valid counts.
The output must be a new directory and cannot overlap any input directory. Failed
runs leave their partial output for inspection; use a new output name to restart.

The script maps the actual folder spellings:

| Input class folder | Canonical class | Dataset |
|---|---|---|
| `Leaf rust` or `Rust` | Rust | JMuBEN, DOI `10.17632/t2r6rszp5c.1` |
| `Cerscospora`, `Cescospora`, or `Cercospora` | Cercospora | JMuBEN |
| `Phoma` | Phoma | JMuBEN |
| `Miner` | Miner | JMuBEN2, DOI `10.17632/tgv3zb82nd.1` |
| `Healthy` | Healthy | JMuBEN2 |

PlantDoc images are accepted only from its `train/<class>/` and `test/<class>/`
trees. Both become **OOD test only**. Its source class name is retained, while the
coffee-app label is `unknown`: this is a non-coffee rejection test, not a coffee
disease accuracy benchmark. The README illustration is excluded. Common Voice is
not scanned, copied, relabelled, or assigned image splits.

## Optional own photos

Place independently labelled photos under `Data/own_photos/<class>/`. Supported
classes are Rust, Cercospora, Phoma, Miner, Healthy, and unknown. Root-level photos
without a class folder are rejected instead of receiving guessed labels. Use
anonymous filenames. For several views of one physical leaf, provide a CSV:

```csv
source_path,leaf_id
Rust/leaf01_front.jpg,leaf-001
Rust/leaf01_back.jpg,leaf-001
Healthy/leaf02.jpg,leaf-002
```

Save it as `own_groups.csv`, then include:

```powershell
python scripts/preprocess_coffee.py prepare --jmuben Data/JMuBEN --plantdoc Data/PlantDoc --own-photos Data/own_photos --own-groups own_groups.csv --output processed/coffee-with-own-v1
```

Own photos always remain in test. Different views can be visually dissimilar, so
the capture-time leaf ID is stronger evidence than a perceptual hash.

## Review near matches, then split

1. Open `processed/coffee-v1/grouping_review.csv` in a CSV editor.
2. Compare the images in `path_a` and `path_b` (relative to the output directory).
3. Change only `decision` to `same_leaf`, `different_leaf`, or leave `pending`.
   Hash distance alone does **not** prove leaf identity. Do not remove rows or
   edit `near_candidates.csv`; it is the candidate audit used to detect missing
   review rows. Paths, hashes, and review files are development artifacts, not
   farmer-facing data.
4. Run:

```powershell
python scripts/preprocess_coffee.py split --output processed/coffee-v1 --seed 42
python scripts/preprocess_coffee.py verify --output processed/coffee-v1 --manifest processed/coffee-v1/splits/images_manifest.csv
```

Every group touching an unresolved candidate is excluded from the final manifest.
Groups with conflicting class labels are also excluded; correct those source
annotations and prepare a new version after human review. Contradictory review
decisions stop splitting. You can obtain a partial, explicitly filtered manifest
while review continues, but do not report it as the full dataset.

Confirmed links missed by pHash can be supplied using `--same-leaf more_groups.csv`:

```csv
image_a,image_b
<full image_id from inventory>,<full image_id from inventory>
```

For a later split run, choose a new subdirectory to preserve the previous manifest:

```powershell
python scripts/preprocess_coffee.py split --output processed/coffee-v1 --name splits-reviewed-v2 --seed 42
```

Targets are 70/15/15 **groups per class**, not files. Classes with fewer than three
eligible groups cannot support three partitions and are reported explicitly; their
eligible groups stay in train. If a group overlaps own/PlantDoc held-out data, the
entire group is test-only. Conflicting labels instead quarantine the group. Exact
duplicate aliases keep all original provenance in the inventory; the final file
row prefers held-out provenance. Seed, inventory hash, and review hash are recorded.
Adding/removing data or changing reviews can change assignments; freeze the final
version before experiments. Apply new augmentation only in the training loader.

## Outputs

| File | Purpose |
|---|---|
| `inventory.csv` | Every encountered file, source class/DOI, original and clean dimensions, hashes, flags, status |
| `inventory_counts.csv` | Source-file counts by dataset, canonical class, and status |
| `dimension_counts.csv` | Original decoded dimensions and their frequencies by dataset |
| `rejected_files.csv` | Unreadable, unsupported multi-frame, unmapped, or excluded documentation images |
| `media/<sha256>.png` | One clean RGB file per unique normalized SHA-256 |
| `duplicate_map.csv` | Exact normalized-file aliases, retaining source provenance |
| `near_candidates.csv` | Immutable-by-convention pHash candidate audit |
| `grouping_review.csv` | Editable decisions for candidate pairs |
| `prepare_summary.json` | Preparation completion marker and totals |
| `splits/images_manifest.csv` | Unique accepted image rows, global group IDs, split assignments |
| `splits/excluded_from_splits.csv` | Source records withheld for review or label conflict |
| `splits/counts.csv` | Retained file and group counts by dataset/class/split |
| `splits/split_summary.json` | Seed, ratios, excluded counts, review/inventory fingerprints |

Import or train from **`images_manifest.csv` only**, never glob the media directory:
media also contains excluded assets. All `file_path` values are relative to the
preparation output root. Counts only contain observed combinations; an absent
combination has zero files. `source_group_count` is a grouping estimate, not proof
of the number of distinct biological leaves in the original study.

## Image and grouping details

- Full decode rejects corrupt/truncated images. Multi-frame files are rejected.
  Excessive-size image warnings are treated as errors. Metadata removal uses
  orientation-aware decoding followed by a fresh RGB pixel image and fresh PNG
  encoding. Transparency is composited over white; dimensions are otherwise
  preserved. No crop, contrast adjustment, resizing, or new augmentation is saved.
- PNG is lossless for the normalized pixels and avoids another JPEG compression
  pass. It can need considerably more disk space than the source JPEG corpus.
  Source files remain intact; do not ship originals containing private metadata.
  SHA-256 describes final stored PNG bytes, not the downloaded JPEG bytes.
- Exact SHA duplicates share one stored file. Lossless 90-degree rotation/flip
  copies share an exact D4 pixel group while retaining their distinct stored files.
  Perceptual matching checks all eight rotations/reflections against indexed
  64-bit DCT pHashes. Default Hamming radius is 4; `--radius` accepts 0 through 8.
  This threshold is a candidate-generation setting, not a validated leaf classifier.
- Candidate lookup uses partitioned bit buckets, avoiding an unconditional
  all-pairs scan. Very homogeneous images can still produce many candidate pairs.
  Small hashes can collide, and crops, arbitrary-angle rotations, severe changes,
  or different leaf viewpoints can be missed. Audit representative groups and
  add known same-leaf links before claiming a leakage-free evaluation.
- `is_augmented` is **blank/unknown** for downloaded assets: numeric filenames do
  not prove which image was original. It is 0 for submitted original phone photos.
  Schema version 2 and the database importer preserve unknown augmentation as NULL.
  `is_field_photo=1` designates the own-phone field test; it is 0 for the other
  imported collections, regardless of how their remote original was captured.
  The supplied sources are treated as non-synthetic; no synthetic assets are created.
- `verify` checks every retained file's hash, full decode, RGB mode, absence of
  image metadata, dimensions, DOI, uniqueness, and group/split consistency.
  Passing it validates manifest invariants, not the scientific completeness of
  near-duplicate detection or correctness of human labels.

Implementation references: [Pillow orientation handling](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose),
[Pillow image loading and creation](https://pillow.readthedocs.io/en/stable/reference/Image.html),
and [SciPy DCT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.fft.dctn.html).
