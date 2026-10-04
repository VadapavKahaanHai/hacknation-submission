# Class names, mappings and fallbacks

## Stable IDs and corrected display names

| ID | Stored key | Display name | Hindi display name | Type |
|---:|---|---|---|---|
| 1 | Rust | Coffee leaf rust | कॉफी पत्ती का रतुआ | disease |
| 2 | Cercospora | Cercospora leaf spot | सर्कोस्पोरा पत्ती धब्बा रोग | disease |
| 3 | Phoma | Phoma disease | फोमा रोग | disease |
| 4 | Miner | Coffee leaf miner | कॉफी पत्ती सुरंगक कीट | pest |
| 5 | Healthy | Healthy leaf | स्वस्थ पत्ती | healthy |
| 6 | unknown | Unknown / uncertain | अज्ञात / अनिश्चित | unknown |

The original `label_en` and `label_hi` database fields are preserved for import
compatibility. API class objects additionally provide `display_name_en`,
`display_name_hi`, and language-selected `display_name`. Use those for UI text.
Prediction responses now include `top_class` and `second_class` objects.
The stored IDs and probability ordering have not changed; the API never converts
an uncertain result into a disease diagnosis to obtain a nicer display name.

Rust naming follows the [Coffee Board of India](https://coffeeboard.gov.in/planter.aspx);
Cercospora naming follows the [UH CTAHR coffee leaf spot publication](https://www.ctahr.hawaii.edu/oc/freepubs/pdf/PD-41.pdf).
Phoma remains a deliberately broad disease label, matching the source class;
neither a precise disease syndrome nor a pathogen species is inferred from that
folder name. Coffee leaf miner is a pest category, with no inferred insect species.
Hindi labels/messages are editorial translations, not a claim of expert linguistic
or agronomic approval. No treatment drafts have been approved by these changes.

## Mapping checks

`scripts/class_mapping.py` defines IDs, aliases, display names, class types and
model output order. Preprocessing and runtime inference share it. Aliases include
the actual folder spellings `Leaf rust`, `Cerscospora`, and `Cescospora`, plus
canonical names and the corrected English display names. Matching ignores case
and surrounding/repeated whitespace while preserving original source strings.

- Rust, Cercospora and Phoma belong to JMuBEN.
- Healthy and Miner belong to JMuBEN2.
- Own photos remain test-only with their independently supplied labels.
- PlantDoc source classes remain preserved, but their coffee-app class is unknown
  and their role is OOD test. Apple rust is never mapped to coffee leaf rust.
- Voice data is not a leaf class or a source of disease-treatment advice.

The importer now builds `label_map` from the full clean inventory, not only the
retained split subset. This restores Healthy's folder mapping even while all its
images remain excluded for review. Missing mappings are added; contradictory
existing mappings are rejected instead of overwritten. Runtime initialization,
startup and database audits reject swapped ID/label/type assignments. Tests pass
each of the six output positions through inference to verify the selected class
and that class's approved-advice lookup.

```powershell
python scripts/audit_mappings.py
# Repair missing source-folder entries only, when needed:
python scripts/audit_mappings.py --fix-missing
```

The audit writes `artifacts/reports/mapping_audit.json`, checks manifest/database
image parity, and fingerprints the review CSV, manifest and runtime DB before/after.
This is not a trained-model accuracy evaluation; no real model is installed.

## Bilingual fallback entries

All fallback text is versioned in `knowledge/ui_messages.json`. These are
**technical/UI messages**, kept separate from treatment/prevention advice.
Every message has English and Hindi text and `is_treatment_advice: false`.

| Code | Trigger |
|---|---|
| `unknown_input` | Top result is unknown; it does not assert the image is definitely non-coffee |
| `low_confidence` | Score falls below the model threshold |
| `close_scores` | Top two scores are insufficiently separated |
| `no_approved_advice` | No reviewed advice exists for the predicted class |
| `advice_language_unavailable` | Reviewed content exists in another language only |
| `unreadable_image` | Unreadable/unsupported image or invalid base64 image field |
| `model_unavailable` | No configured model or invalid model output; no prediction is stored |
| `pending_sync` | A prediction envelope is still queued locally |
| `mock_result` | Explicit demo-mode prediction |
| `unsupported_language` | Unsupported requested language; English UI error fallback is explicit |
| `request_failed` | Generic technical failure without private diagnostic details |

Prediction `fallbacks` is a list: uncertainty and pending-sync messages can both
apply. Queued does not mean the app detected loss of connectivity; the wording
only says the upload has not been confirmed. Acknowledgement removes that message;
an escalation change that requeues the envelope brings it back. A resolved
escalation does not change model confidence or suppress uncertainty messaging.

The existing `/advice` endpoint still returns a list for backward compatibility.
For explicit missing-content behavior use:

```text
GET /classes?language=hi
GET /advice-status?class_id=1&language=hi
GET /fallbacks?language=hi
```

These authenticated routes return corrected display names, advice availability,
available reviewed languages and/or technical fallback entries. Unsupported
prediction/advice languages still get HTTP 400; there is no silent substitution
of disease advice from English into Hindi. The `/fallbacks` UI-message catalog can
use English for an unsupported language, explicitly marked `language_fallback`.

HTTP error responses retain their existing `error` codes and add a localized
`fallback` object. Prediction routes take the language from the JSON body; GET
routes take it from the query. If invalid JSON cannot be parsed, English is used.

## Compatibility and verification

No schema migration, renaming of stored classes, or changes to schema/catalog
fingerprints were needed. Only missing source-folder mapping rows were repaired
in the existing development database. Images, advice approvals, prediction
records, pending reviews, and runtime data remain unchanged. Restart an already
running API process to load the code and message changes.

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests cover aliases/provenance, wrong mappings, all output positions, bilingual
messages, missing/unreviewed advice, wrong-language availability, unreadable
images, absent models, pending/requeued sync, and the original API/data workflow.
