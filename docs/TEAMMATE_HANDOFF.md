# Coffee Leaf Advisory App — Teammate Handoff

- **1. Organized the project**
  - `scripts/` — preprocessing, database, API and packaging scripts.
  - `sql/` — SQLite schema and runtime tables.
  - `knowledge/` — dataset metadata, advice drafts and bilingual messages.
  - `tests/`, `docs/`, `artifacts/` — tests, documentation and generated outputs.
  - Original files remain untouched in `Data/`.

- **2. Cleaned and inventoried the images**
  - Inventoried **46,741 files**: 46,735 clean, four rejected and two ignored.
  - Applied image orientation, removed metadata, recorded dimensions and calculated hashes.
  - Reduced exact duplicates to **6,308 unique cleaned files**.
  - Preserved provenance: Rust/Cercospora/Phoma → JMuBEN; Healthy/Miner → JMuBEN2.

- **3. Grouped duplicates and assigned splits**
  - Grouped exact rotation/flip matches and flagged **411 near-duplicate pairs** for review.
  - Assigned complete groups to train/validation/test.
  - Current retained manifest contains **4,545 images across 3,201 groups**.
  - Pending-review groups remain excluded. **Healthy currently has no retained training images.**
  - PlantDoc remains OOD-test-only; own photos are not yet supplied; Common Voice remains separate.

- **4. Built and populated SQLite**
  - Development database: `artifacts/databases/coffee.sqlite`.
  - Added foreign keys, constraints, indexes, split-leakage protection and dataset/class count views.
  - Importer verifies manifest fingerprints and image files before importing.
  - Repeated imports are checked for consistency.

- **5. Added the knowledge-base structure**
  - Added **six unreviewed advice drafts/placeholders**.
  - Included fields for symptoms, treatment, prevention and escalation guidance.
  - Only human-reviewed advice can be returned to users.
  - **No treatment records are approved yet.**

- **6. Implemented Python queries and a local API**
  - Supports class lookup, reviewed advice, predictions, history, escalation and sync-queue operations.
  - Uploads are normalized and stripped of metadata.
  - Low-confidence, close-score and unknown results trigger escalation.
  - Inference currently uses an explicit **mock**; no trained model is connected.

- **7. Implemented local store-and-forward behavior**
  - Predictions and escalation changes enter a persistent SQLite queue.
  - Added retry tracking, duplicate-request handling and stale-acknowledgement protection.
  - Queue persistence across restarts is tested.
  - **Remote sync transport is not implemented yet.**

- **8. Verified mappings and added Hindi text**
  - Checked **33 source-folder mappings** and all six stable class IDs.
  - Corrected English/Hindi display names; Miner is classified as a pest.
  - Added **11 bilingual fallback messages**.
  - Hindi wording still needs human language review.

- **9. Prepared demo images and edge cases**
  - Created **19 fixtures**: five source images and fourteen edge cases.
  - Includes blur, darkness, rotation, transparency, corrupt files and oversized dimensions.
  - Location: `artifacts/demo/coffee-v1/`.
  - These test API behavior, not disease accuracy.

- **10. Built the offline database package**
  - Package: `artifacts/packages/coffee-local-v1.zip`.
  - Contains a fresh SQLite database, bilingual labels/messages and a checksum manifest.
  - Installed locally at `artifacts/local/coffee-v1/`.
  - Validates integrity, hashes, mappings and approved-only advice.
  - Refuses to overwrite an existing device database.
  - Packaged inference is **unconfigured**, with zero approved advice records.

- **11. Completed automated validation**
  - **32 tests plus SQLite schema checks passed.**
  - Covers preprocessing, imports, mappings, advice gating, API behavior, sync persistence, fixtures and packaging.
  - Run: `python -m unittest discover -s tests -p 'test_*.py' -v`

- **12. Teammate’s starting points**
  - Read [README.md](../README.md), [API.md](API.md) and [OFFLINE_PACKAGE.md](OFFLINE_PACKAGE.md).
  - Review the 411 image pairs and restore eligible Healthy coverage.
  - Add own-phone test photos.
  - Get treatment content and Hindi wording reviewed.
  - Train/evaluate a real model and connect its inference adapter.
  - Integrate the local package into Android and implement remote sync.
