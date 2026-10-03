# Offline coffee advisory database

For demonstration only, run `sql/coffee_schema.sql`, then `sql/coffee_sample_data.sql`,
against a fresh database. For actual data, use `python scripts/pipeline.py` from
the project root; see [the main README](../README.md).
Use a SQLite build with JSON1; the supplied test passes on SQLite 3.42.0.
Enable `PRAGMA foreign_keys = ON` on **every connection**, before transactions.
Run schema verification with `python tests/test_coffee_schema.py` (Python standard library only).

Schema version 2 additionally stores build provenance in `database_metadata`,
allows unknown image augmentation as NULL, and adds structured symptoms,
treatment, prevention and escalation wording to advice. All content edits revoke
review. The six English rows in `knowledge/advice.csv` are unreviewed drafts.

The sample creates five rows per table, except `classes`, which needs six to
include `unknown`. The five escalations and five queue entries are automatically
created by prediction triggers; their explicit sample inserts use `NOT EXISTS`
to avoid duplicates. All paths, hashes, model sizes, and predictions are fixtures.
Accuracy values are null, meaning unmeasured. All advice remains unreviewed.
No image or audio files are supplied, and no human review is claimed.

## ER summary

| Parent | Child | Relationship |
|---|---|---|
| datasets | images | One dataset has many retained image files. |
| datasets | label_map | One dataset has many original-folder mappings. |
| classes | label_map | Many source labels may map to one canonical class. |
| classes | images | Each image has one canonical class, including unknown. |
| classes | advice | At most one current advice row per class and language. |
| classes | voice_phrases | Optional class link for recorded or TTS UI phrases. |
| model_versions | predictions | Each prediction records the exact immutable model version. |
| classes | predictions | Separate foreign keys for top and second classes. |
| predictions | escalations | Zero or one escalation per prediction. |
| predictions | sync_queue | Zero or one pending envelope per prediction. |

`source_group_id` is a global grouping key shared across datasets, not a separate
table. `prediction.image_path` is a filesystem reference, not a foreign key into
the curated dataset catalog: operational photos need not become test data.
`common_voice_hi` is a speech dataset; its clips/transcripts belong in
`voice_phrases`, not `images`. Pin the speech release in `datasets`; retain the
official split and source-clip manifest with the speech-development assets.

## Rules and application responsibilities

1. **Provenance and grouping.** The merged `jmuben/` directory is not itself a
   dataset. Resolve Rust/Cercospora/Phoma to JMuBEN and Healthy/Miner to JMuBEN2
   through `label_map`. Deduplicate exact bytes before inserting: one canonical
   file row per SHA-256. Keep discarded path aliases in the import manifest;
   quarantine conflicting labels/provenance for review rather than silently
   overwriting the retained record. Compute a consistent perceptual hash after
   image normalization and compare rotated/flipped variants; ordinary pHash is
   not automatically invariant to these transformations. Assign groups before
   train/validation/test splitting. Audit grouping decisions because hash
   similarity does not prove that two photos show the same leaf. Repeated field
   captures of one leaf also share a group. Triggers enforce a common split on
   INSERT and UPDATE, across all datasets. `split_leakage` is an additional audit.

2. **Evaluation separation.** Dataset `role='train'` means eligible for development;
   its individual groups may be train, validation, or held-out test. Test and OOD
   datasets can only contain test images. Voice datasets cannot contain image
   rows. The training loader must select only `role='train' AND split='train'`;
   tune only on validation groups. Do not tune thresholds on own-photo or
   PlantDoc test results. Import all 20-30 own leaves when available; the five-row
   image fixture does not pretend to contain the actual pilot collection.

3. **Uncertainty.** The added `model_versions.min_margin` makes “too close” explicit.
   Reject when top confidence is below the model threshold, the top-minus-second
   score is below the margin, or the top class is unknown. Threshold equality
   passes; exact ties fail because the margin must be positive. Scores must be
   probabilities from the same mutually exclusive class distribution. The
   sample 0.10 margin is illustrative and needs validation. Unknown/non-coffee
   detection still needs a suitable model or input gate; a database cannot
   detect such input from an overconfident known-class score. Insert predictions
   with their top two scores; the trigger sets status, creates an escalation,
   and queues the envelope in the same transaction. Re-inference gets a new ID.
   Escalation `reason` records the first applicable reason: unknown, low score,
   then close scores. Resolving an escalation does not rewrite model evidence.

4. **Reviewed advice.** The serving repository must query `prediction_advice`
   filtered by prediction ID and language. It returns only reviewed advice for
   confident predictions. For general browsing use `reviewed_advice`. Never
   expose the raw `advice` table through the farmer-facing interface. SQLite
   views are not access-control boundaries: this restriction must also be
   enforced in application code. Missing reviewed content means no disease
   advice is available; show a reviewed generic escalation message instead.
   Editing advice content revokes its review. Clinical/agronomic content and
   Hindi translations need actual human review; the seeds claim neither.

5. **Audio provenance.** Playback uses `playback_phrases`, which excludes Common
   Voice. Common Voice clips/transcripts are speech-recognition material, never
   generated speech or farmer-facing synthesized advice. Record TTS assets as
   `tts` with the engine/voice's actual license, and project recordings as
   `own_recording` with recording rights. Verify assets and rights before
   shipping. Spoken disease advice must go through the same review gate as text.

6. **Store-and-forward.** A queue row targets a prediction envelope containing
   the prediction plus its optional escalation. Therefore `table_name` is
   deliberately restricted to `predictions` and `row_id` has a real foreign key.
   Use random, globally unique prediction IDs and idempotent server upserts;
   local integer `queue_id` is never the server identity. A single sync worker
   takes a payload snapshot, retries with bounded backoff, increments attempts,
   and records sanitized error codes. On an explicit server acknowledgement,
   use a short transaction to verify the current envelope still matches the
   transmitted snapshot before setting `synced=1`, setting UTC `synced_at`, and
   deleting the queue row. If an escalation changed in flight, retain the queue
   entry and send the newer envelope. Escalation creation, routing, and
   resolution automatically clear the synced flag and enqueue the envelope.
   A failed/partial upload never marks the envelope synced. Process server
   replies serially; do not hold a database transaction across network I/O.

7. **Photo privacy.** Before saving any dataset or operational photo, decode it,
   apply its EXIF orientation to pixels, then encode a fresh image without EXIF,
   GPS, XMP, or other metadata. Verify metadata removal, then hash the final stored
   bytes and compute pHash. Store files under random, app-private relative paths;
   do not retain the original metadata-bearing copy. Validate paths against
   traversal. SQLite cannot inspect or strip filesystem metadata. Use a random
   installation `device_id`, not a phone number, advertising ID, or hardware ID.
   Do not put farmer names, contacts, coordinates, or raw payloads in paths,
   reasons, routing fields, or error logs. Reviewer IDs identify staff only.

8. **Small-device storage.** Keep image/audio/model binaries on disk. Grouping,
   training, and corpus storage happen on the development machine; the phone
   needs the active model, class metadata, reviewed guidance, a small playback
   bundle, and local operational records. The schema can serve both uses without
   shipping the training corpus. Retain pending photos until acknowledged;
   prune acknowledged data according to an explicit retention policy. Use
   batched transactions. Indexes cover the requested filters and relationships;
   a normal B-tree on pHash would not accelerate Hamming-distance grouping.
   `classes.image_count` is trigger-maintained and must not be edited directly.
   `class_image_counts` reports imported file and distinct-group counts, including
   zero counts; it does not claim published dataset totals. Avoid `INSERT OR
   REPLACE`: it has deletion semantics that can undermine counts and references.

## Sources

- [JMuBEN record and CC BY 4.0 license](https://data.mendeley.com/datasets/t2r6rszp5c/1)
- [JMuBEN2 record and CC BY 4.0 license](https://data.mendeley.com/datasets/tgv3zb82nd/1)
- [SQLite foreign-key connection requirements](https://www.sqlite.org/foreignkeys.html)
- [SQLite triggers](https://www.sqlite.org/lang_createtrigger.html)
- [SQLite JSON support](https://www.sqlite.org/json1.html)

Unverified release sizes, licenses, and source metadata are intentionally NULL;
fill them from the actual downloaded artifacts rather than guessing.
