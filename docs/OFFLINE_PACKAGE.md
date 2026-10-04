# Offline local database package

Build and check the content seed:

```powershell
python scripts/offline_package.py build
python scripts/offline_package.py validate artifacts/packages/coffee-local-v1.zip
python -m unittest discover -s tests -p 'test_*.py' -v
```

The ZIP contains `coffee.sqlite`, `class_labels.json`, `ui_messages.json` and
`manifest.json`. The database has six stable classes, reviewed advice only, and
the runtime schema for sanitized photos, predictions, escalations and queued sync.
English/Hindi JSON files are the offline UI content cache. They are versioned with
the database and need no network access. No additional caching service is needed.

The current catalog has **zero approved advice records**. This package therefore
has no treatment advice. Its inference mode is **unconfigured**, with no model
weights or mock model. Upload inference returns 503 until a real adapter is
configured. The manifest explicitly reports these availability limits. It is a
validated data seed, not a complete Android app or a production-ready diagnosis tool.

Install once into a new local directory:

```powershell
python scripts/offline_package.py install artifacts/packages/coffee-local-v1.zip --destination artifacts/local/coffee-v1
```

On Android, copy the validated seed into app-private writable storage on first
launch, load the JSON labels/messages, and enable `PRAGMA foreign_keys=ON` on every
SQLite connection. Save normalized photos in the sibling `media/` directory.
The Python installer is a desktop reference; Android integration still needs its
own first-run handling and device testing. Existing runtime API Python code continues
to load its repository UI resources; package validation requires those resources to
match the bundled cache. The ZIP does not contain API code, Python or dependencies.

Installation refuses an existing directory. **Never replace an active device
database to refresh content:** that would discard unsynced records. A later content
update mechanism should merge approved content transactionally and preserve local
predictions, media and queues. This script only implements first installation.
Use a new `--output` path when building a new package version.

Validation checks:

- Consistent SQLite backup of the source, integrity, foreign keys, class IDs and split leakage.
- Fresh runtime seed excludes training images, draft advice, operational records and audio.
- SQLite version, row counts, review flags, empty media records and runtime metadata.
- Fixed ZIP member names, duplicate/path rejection, 16 MiB compressed/expanded limit.
- SHA-256 and byte counts for every payload, supported package version and exact UI content.
- Installation validates its own snapshot before publishing the new directory.

Checksums detect corruption; they do not authenticate the sender. Distribute this
seed with trusted app assets. Model distribution, remote content updates and remote
sync transport are separate work. UI wording still needs human language review.
Sample/edge-case images stay in the separate demo pack and are not shipped on device.
