# Sample images, edge cases and Hindi text

Generate the separate local pack:

```powershell
python scripts/prepare_demo.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Output: `artifacts/demo/coffee-v1/` (ignored by Git).

- `images/`: one retained test-split image per available class, named by stable class ID.
- `edge_cases/`: artificial controls and clearly marked test-image variants.
- `manifest.json`: file hashes, provenance, expected upload response and coverage gaps.
- `local_language.json`: six class display names and eleven UI messages in English/Hindi.

The generator checks the frozen manifest, inventory and review hashes, then checks
selected media hashes, dimensions and metadata. It never changes the catalog,
runtime database, review decisions or training inputs. Existing output is refused;
use `--output artifacts/demo/coffee-v2` for a new pack. The tests use temporary databases.

Current data can supply Rust, Cercospora, Phoma, Miner and PlantDoc unknown examples.
Healthy is missing because its groups are excluded pending review. Own phone photos
are still absent. Source labels are dataset labels, not independently verified diagnoses.
The manifest preserves dataset DOI and original path; use the source catalog's license
and citation records when sharing images. Common Voice is not included.

| Case | Expected API response |
| --- | --- |
| Retained PNG, EXIF JPEG, transparent, grayscale, tiny, blank | 201 with configured mock |
| Blurred, dark, rotated test-image derivatives | 201 with configured mock |
| Empty file | 413 / image_size_limit |
| Corrupt or truncated file | 400 / unreadable_image |
| BMP or animated PNG | 415 / unsupported_image |
| 4000 × 3001 pixels | 413 / image_pixel_limit |

Use any fixture's bytes as base64 in the existing `POST /predictions` request,
with fresh UUID4 prediction/device IDs and `language: "hi"`; see [API.md](API.md).
The mock returns unknown, an escalation and no advice for every valid input.
These fixtures test upload handling and UI behavior, not disease accuracy.
Tiny, blank, dark and blurred inputs have no automatic quality rejection yet.
Derived images belong to their source test group; never import this pack as new
training data or count variants as independent evaluation samples.

Hindi strings are UI text, marked as not human reviewed. They contain no treatment
instructions or synthesized speech. A Hindi speaker should review phrasing and the
Android UI should be checked for Devanagari rendering/wrapping; Python tests verify
UTF-8 round trips and API text equality, not visual rendering or translation quality.
Missing approved Hindi advice remains empty with a localized fallback.
