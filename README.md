# Leaf Check

Offline crop disease screening for smallholder farmers. Built for the World Bank **Small AI for Development** hackathon.

**[Try the live app](https://vedk08.github.io/leaf-check/)** — open online once and let it finish loading, then use it offline or add it to your home screen.

## What it does

1. Choose **coffee, maize or beans** and photograph a leaf.
2. Get a likely diagnosis, confidence, urgency and suggested actions for today, treatment and prevention.
3. If uncertain, answer up to two yes/no questions or consult an agriculture officer.

English and Hindi, with read-aloud support. Inference runs on the device; photos stay on your phone. No backend required.

## How it works

- **Model:** MobileNetV3Small v0.3, 13 classes including healthy leaves and unknown, approximately 3.9 MB.
- **Runtime:** TensorFlow.js with WASM, WebGL and CPU fallback; a service worker caches the app, model and advice for offline use.
- **Data:** SQLite-backed crop guidance exported as JSON for the browser.
- **Confidence:** instant results require at least 90% confidence and a 15-point lead over the next class. Low-confidence or unknown results prompt follow-up questions or a new photo.

## Results and limits

| Validation metric | Result |
|---|---:|
| Overall accuracy | 90.5% |
| Real leaves answered instantly | 74% |
| Accuracy on instant answers | 97.8% |
| Unsupported crop leaves rejected | 99.4% |

These are same-source validation results, **not field accuracy**. Training uses iBean, PlantVillage, JMuBEN/JMuBEN2 and Uganda coffee images. Exact and rotated/flipped duplicates are removed before splitting, but photo-style bias remains; maize training images are mostly lab photos. Unknown detection does not cover every non-leaf image.

Advice is draft pending agronomist review; Hindi translations are unverified. Chemical guidance includes safety notes and omits product names and doses.

## Run locally

From the repository root, with Python installed:

```sh
python -m http.server 8000 --directory web
```

Open **http://localhost:8000**. Use the HTTPS live app to test offline installation on a phone.

## Repository

| Folder | Contents |
|---|---|
| `web/` | Offline app, bundled model and browser inference |
| `model/` | Training, export, calibration and verification scripts |
| `data_layer/` | Crop database, response logic and JSON export |
| `scripts/`, `knowledge/`, `sql/` | Dataset preparation and advisory workflow |
| `tests/`, `docs/` | Tests and technical documentation |

Details: [data preprocessing](docs/PREPROCESSING.md) · [offline data package](docs/OFFLINE_PACKAGE.md) · [demo samples](docs/DEMO_SAMPLES.md) · [optional localhost API](docs/API.md). Training instructions are in `model/train.py`.

**Team:** Sanket — data pipeline and dataset audit · Ved — model, app and offline deployment.
