# Runtime database functions and local inference API

Run from `D:\Hackathon`. No additional packages beyond the existing Pillow
dependency are needed. This is a local development API and reusable Python
runtime layer; it is not a trained diagnostic model or a hosted production service.

## Start

Create a separate operational database once:

```powershell
python scripts/serve_api.py init --mock
```

Defaults: catalog `artifacts/databases/coffee.sqlite`, runtime
`artifacts/runtime/coffee.sqlite`. The initializer refuses to overwrite a file.
It copies the six classes and **only human-reviewed advice**, never training
images, pending reviews, drafts, fake evaluation results, or old predictions.
With the present catalog it copies zero advice records. The catalog is not changed.
Runtime advice is a content snapshot: subsequent catalog edits do not propagate
automatically. A reviewed-content update/distribution step will be needed before
shipping later catalog approvals or revocations to existing offline installations.
The mock model has a plainly labelled placeholder metadata record; its tiny
file-size value satisfies the existing schema and is not a measured model artifact.

Start the local server in a terminal:

```powershell
$env:COFFEE_API_TOKEN = python -c "import secrets; print(secrets.token_hex(32))"
python scripts/serve_api.py serve
```

Keep that terminal open; Ctrl+C stops the server. For a second client terminal,
set the same token there privately, or run requests from a client that can access
the token. Do not commit it. The server binds only `127.0.0.1:8000`, disables
request access logs, and sends no CORS permission. All endpoints except health
require `Authorization: Bearer <token>`. This token controls a **single local
installation**, not a multi-user hosted service.

The startup command uses Python's [WSGI reference server](https://docs.python.org/3/library/wsgiref.html),
which is not recommended for production. The `API` object is a WSGI callable
that can later be wrapped by a suitable server; TLS, hosted authentication and
device-level authorization would be additional deployment work.

## Test requests

In a terminal with the token set:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$headers = @{ Authorization = "Bearer $env:COFFEE_API_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/classes -Headers $headers

$predictionId = [guid]::NewGuid().ToString()
$deviceId = [guid]::NewGuid().ToString()
$imageBytes = [IO.File]::ReadAllBytes('D:\path\to\test-leaf.jpg')
$payload = @{
    prediction_id = $predictionId
    device_id = $deviceId
    image_base64 = [Convert]::ToBase64String($imageBytes)
    language = 'en'
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/predictions -Method Post -Headers $headers -ContentType application/json -Body $payload
```

Generate the anonymous device UUID once per installation and persist it locally.
Never derive it from a farmer name, phone number, coordinates or hardware ID.
Generate a new prediction UUID for a new inference; reuse the same ID and photo
for an HTTP retry. A repeat returns the existing result. Reusing an ID with a
different device or normalized image returns HTTP 409. Rerunning the same photo
with a new model is a new prediction, not an edit of prior evidence.

The mock always returns a fixed unknown result; it does not analyze symptoms.
Every prediction and sync envelope includes `is_mock: true`. It still exercises
image sanitization, storage, uncertainty escalation, queueing and advice gating.
Do not use mock predictions as farmer advice or evaluation results.

## Endpoints

| Method | Path | Input / result |
|---|---|---|
| GET | `/health` | Readiness and mode; public, no local paths |
| GET | `/classes` | Six canonical classes |
| GET | `/classes?language=hi` | Classes with corrected Hindi `display_name` and both display-name translations |
| GET | `/fallbacks?language=hi` | Bilingual technical/UI fallback catalog |
| GET | `/advice-status?class_id=1&language=hi` | Approved content or an explicit missing-content/language fallback |
| GET | `/advice?class_id=1&language=en` | Approved advice only; empty list when unavailable |
| POST | `/predictions` | UUIDs, base64 image, optional language; 201 new / 200 retry |
| GET | `/predictions/{uuid}?language=en` | Stored evidence, escalation, media hash and approved advice |
| GET | `/predictions?device_id={uuid}&limit=20&offset=0` | Paginated installation history |
| POST | `/escalations/resolve` | `prediction_id`, optional `routed_to` service code |
| GET | `/sync/pending?limit=20` | Up to 100 queued envelopes and snapshot tokens |
| POST | `/sync/ack` | `prediction_id`, `sync_token`; acknowledge unchanged envelope |
| POST | `/sync/failure` | Same IDs plus a sanitized `error_code`; increments attempts |

Resolve is an adviser/operator action, not a new model diagnosis; it does not
change an uncertain prediction to confident. Routing accepts only `local_adviser`
or `extension_service`, not names or phone numbers. Failure codes are limited to
`network_unavailable`, `timeout`, `server_error`, and `unauthorized`.

An uncertain prediction returns no class-specific advice. A confident result
without approved content has `advice_available: false`. There is no automatic
fallback to another language. Editing approved content revokes its review, and
later reads of old predictions respect that revocation.

Errors include `{"error":"machine_readable_code","fallback":{...}}`: 400 invalid input, 401 missing
authentication, 404 absent resource, 409 conflicting retry/stale acknowledgement,
413 size limit, 415 unsupported content, and 503 unconfigured/invalid model or
temporarily unavailable database. Limits: 8 MiB image bytes, 12 million pixels,
12 MiB JSON request. JPEG, PNG and single-frame WebP are accepted. Extra POST
fields are rejected, including unsolicited farmer identity fields.

See [mapping and fallback behavior](MAPPINGS_AND_FALLBACKS.md). Prediction responses
also include named `top_class`/`second_class` objects and a `fallbacks` list. Stable
class keys and IDs are preserved; use the added `display_name` fields for UI text.

## Python functions

`scripts/runtime_db.py` provides:

- `initialize_runtime(catalog, destination, mock=False)`
- `Runtime.get_reviewed_advice(class_id, language)`
- `Runtime.submit_image(prediction_id, device_id, image_bytes, language)`
- `Runtime.get_prediction(prediction_id, language)`
- `Runtime.list_predictions(device_id, limit, offset, language)`
- `Runtime.resolve_escalation(prediction_id, routed_to)`
- `Runtime.list_pending_sync(limit)`
- `Runtime.acknowledge_sync(prediction_id, sync_token, error_code=None)`

`submit_image` is the guarded prediction-writing function. The API never accepts
client-provided scores, model IDs or status. Each operation has its own SQLite
connection with foreign keys enabled. Scores are validated as six finite
probabilities summing to one; the existing database trigger derives uncertainty
from the model's threshold/margin and unknown class.

Model integration later: register the exact validated model metadata in a fresh
non-mock runtime and supply `Runtime(database, predictor=callable, model_id=...)`.
The callable receives a metadata-free RGB Pillow image and returns probabilities
in ID order **Rust, Cercospora, Phoma, Miner, Healthy, unknown**. The adapter must
apply the model's own resize/normalization and map its outputs to these IDs. A
five-class model needs an explicit out-of-distribution/input rejection strategy;
do not treat an appended zero as meaningful unknown detection. Set the runtime's
`inference_mode` metadata to `model` when configuring a genuine adapter. The
standard CLI has no trained-model loader yet. Without a configured callable it
returns 503, not guessed results. Mock and real evidence should use separate
runtime databases.

## Storage and sync boundaries

Uploads are decoded in memory, oriented, re-encoded from fresh RGB pixels, and
saved under random prediction IDs in the runtime `media/` folder. Raw uploads are
not saved. Metadata, filenames and GPS are not copied. `prediction_media` stores
the final SHA-256 and dimensions; the curated `images` dataset table stays empty.
An image write is flushed before committing its database reference. On ordinary
transaction failure, unreferenced new media is removed. An abrupt process/power
failure can leave an orphan file; do not delete such files blindly while serving.
Single-device inference is serialized with writes for straightforward retry
safety; a queued inference worker is appropriate if higher throughput is needed.

Pending sync returns an immutable-evidence snapshot and a hash token. Upload the
envelope and sanitized image through a future transport worker, using prediction
ID as the server idempotency key. This implementation sends **nothing remotely**.
Only call `/sync/ack` after the remote service confirms durable receipt of both
the necessary media and records. The hash token is a local snapshot version,
not a cryptographically signed server receipt.

Acknowledgement compares the current envelope within the same write transaction
that marks it synced and removes its queue entry. If an escalation changed in
flight, the old token gets 409 and the newer envelope stays pending. Duplicate
acknowledgements are harmless. Report failure instead of acknowledgement after
an unsuccessful upload; the queue remains. Network backoff and the remote service
are intentionally left for the transport implementation.

## Verification

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Runtime tests include real localhost HTTP requests with server shutdown afterward,
WSGI routes, authentication, bounded uploads, EXIF stripping, repeated/concurrent
requests, model-output validation, rollback, review revocation, paging, stale
acknowledgements and requeue after resolution. Tests use synthetic images and
temporary databases; they never modify the development catalog or review CSV.
