# Offline coffee advisory data workflow

For the next application step, see [runtime query functions and inference API](docs/API.md).
Start with `python scripts/serve_api.py init --mock`, then follow the token/server
instructions there. Mock inference is explicitly labelled and uses a separate
operational database; image-pair review can remain pending while API work proceeds.

The workflow cleans images, preserves dataset provenance, creates group-based
splits, verifies stored media, imports a development SQLite database, and imports
structured advisory drafts. Original data and human review decisions are preserved.

## Layout

```text
Data/                         Original datasets; never modified
processed/coffee-v1/           Existing clean media, inventory and duplicate review
  splits/                     Frozen image manifest, exclusions and split counts
scripts/
  preprocess_coffee.py         prepare / split / verify
  coffee_db.py                 build / check / counts / advice / import-advice
  pipeline.py                  End-to-end workflow, reuses completed preparation
  runtime_db.py                Operational queries, inference and sync transactions
  serve_api.py                 Authenticated localhost API and runtime initialization
sql/
  coffee_schema.sql            Real schema, version 2
  coffee_sample_data.sql       Demonstration fixtures ONLY
  runtime.sql                  Operational photo metadata, outside the dataset catalog
knowledge/
  catalog.json                Dataset provenance and six canonical classes
  advice.csv                  Editable structured advisory drafts
tests/                        Schema, preprocessing and importer integration tests
docs/                         Detailed preprocessing and schema documentation
artifacts/
  databases/coffee.sqlite      Generated development database
  reports/                    Build report, submission counts and validation summary
  runtime/                    Separate runtime database and sanitized submitted photos
requirements-preprocessing.txt
```

Run commands from the project root. Python dependencies are listed in
`requirements-preprocessing.txt`; SQLite is provided by Python's standard library.

```powershell
python -m pip install -r requirements-preprocessing.txt
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/pipeline.py
```

The default pipeline reuses the existing `processed/coffee-v1` cleaning output.
If it does not exist, preparation starts from `Data/JMuBEN` and `Data/PlantDoc`.
Common Voice is untouched. The run creates splits if missing, checks source
fingerprints, verifies every retained image, imports in a transaction, and writes
reports. Raw data, processed media and databases are excluded from source control.

Pending near-duplicate candidates and conflicting labels remain **excluded**;
they are never approved automatically. The database report clearly marks a
catalog with exclusions as partial. To require all candidate reviews first:

```powershell
python scripts/pipeline.py --require-complete-review
```

Edit only `decision` in `processed/coffee-v1/grouping_review.csv`, using `same_leaf`,
`different_leaf`, or `pending`. Human review is needed to establish leaf identity;
hash similarity alone cannot do it. See [preprocessing details](docs/PREPROCESSING.md).

After review changes, preserve the old manifest/database and create a new version:

```powershell
python scripts/pipeline.py --split-name splits-v2 --database artifacts/databases/coffee-v2.sqlite --reports artifacts/reports-v2
```

To add own photos, use a **new preparation directory** and labelled class folders:

```powershell
python scripts/pipeline.py --prepared processed/coffee-v2 --own-photos Data/own_photos --own-groups own_groups.csv --database artifacts/databases/coffee-v2.sqlite --reports artifacts/reports-v2
```

## Direct database commands

```powershell
python scripts/coffee_db.py build --prepared processed/coffee-v1 --manifest processed/coffee-v1/splits/images_manifest.csv --database artifacts/databases/coffee.sqlite
python scripts/coffee_db.py check --database artifacts/databases/coffee.sqlite
python scripts/coffee_db.py counts --database artifacts/databases/coffee.sqlite
python scripts/coffee_db.py advice --database artifacts/databases/coffee.sqlite --class-id 1 --language en
```

Repeated builds with identical inputs do not duplicate rows. Changed image
manifests, catalogs, or schemas require a new database destination. Existing
records are checked for conflicts rather than silently replaced. A failed fresh
build does not publish a partial database; failed updates roll back. No sample
images, model versions, predictions, audio records or claimed model accuracies
are imported into the real catalog.

`database_metadata` stores build fingerprints and a **relative** media-root path.
Keep the directory relationship when moving the database, or update that deployment
configuration explicitly. Binary images remain outside SQLite. The generated DB
is a development catalog, not the phone's final package: ship reviewed guidance,
class/model metadata and approved audio without the training corpus. Phone export,
model training and audio synthesis are not performed here.

## Advisory content workflow

`knowledge/advice.csv` has one English **draft** per class. It includes:
`advice_text`, `action_steps`, `symptoms`, `treatment`, `prevention`,
`when_to_escalate`, citations and review metadata. Treatment/prevention placeholders
are explicit where expert regional content has not been obtained. These are not
validated treatment recommendations. Hindi class labels exist; Hindi advice still
needs translation and review, using new rows with `language=hi`.

1. Have a qualified coffee adviser supply region-appropriate symptoms, treatment,
   prevention and escalation wording with authoritative source citations.
2. Import content as `reviewed_by_human=0`, leaving reviewer/date blank.
3. After actual content and language review, set `reviewed_by_human=1`, a staff
   reviewer identifier and a UTC ISO date such as `2026-10-01T10:00:00Z`.
4. Import the updated CSV:

```powershell
python scripts/coffee_db.py import-advice --database artifacts/databases/coffee.sqlite --advice knowledge/advice.csv
```

The importer checks review fields, not whether a human actually reviewed content:
that remains the content owner's responsibility. Editing an existing approved
record requires importing it as a draft first; a stale approval flag is rejected.
Review unchanged content separately after that. Editing content directly in SQL
also revokes its review. Omitted CSV rows are not deleted from the database.

Farmer-facing code must use `reviewed_advice` or `prediction_advice`, never the
base advice table. The command `coffee_db.py advice` uses the reviewed view, so it
returns an empty list for the supplied drafts. SQLite views do not implement an
access-control boundary; the application repository must enforce this query path.

## Data and validation

- Rust/Cercospora/Phoma map to JMuBEN; Healthy/Miner map to JMuBEN2.
- `Cerscospora` and `Leaf rust` are handled as actual local folder aliases.
- Exact SHA-256 aliases preserve their original provenance in the inventory.
- Rotation/flip pixel identity and reviewed near matches determine groups before
  70/15/15 class-stratified **group** splitting. No new augmentation is generated.
- Own photos and PlantDoc remain test-only. PlantDoc evaluates non-coffee/unknown
  rejection, not coffee disease classification. Common Voice is ASR-only.
- EXIF orientation is applied before saving fresh metadata-free RGB images.
- Unknown augmentation provenance stays SQL `NULL`, not a fabricated false flag.
- Dataset sizes are retained clean-file sizes, not published corpus download sizes.
- Unverified licenses/releases stay null or explicitly marked unverified. No own
  photos, voice clips or evaluated model metrics are invented.

Reports are in `artifacts/reports/DATA_REPORT.md`, `build_report.json` and
`database_counts.csv`. Tests cover cleaning, orientation, metadata removal,
deduplication, group splits, OOD separation, review gating, automatic escalation,
sync requeue, repeated imports, rollback, tampered manifests/media, counts and
foreign keys. Successful checks do not prove complete near-duplicate detection,
correct agronomic labels or real-world model accuracy.

Dataset references: [JMuBEN](https://data.mendeley.com/datasets/t2r6rszp5c/1),
[JMuBEN2](https://data.mendeley.com/datasets/tgv3zb82nd/1).
Draft content references to review: [Coffee Board of India](https://coffeeboard.gov.in/planter.aspx),
[UH CTAHR Cercospora publication](https://www.ctahr.hawaii.edu/oc/freepubs/pdf/PD-41.pdf).
