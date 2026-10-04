# Offline Crop Disease Diagnosis

World Bank "Small AI for Development" hackathon entry. A farmer picks a crop, photographs a leaf, and gets a likely diagnosis, an urgency level, what to do today, treatment, prevention and a safety note. The model and the advice database run fully offline in the browser.

## Current status

| Part | Status |
|---|---|
| Data layer (SQLite + JSON bundle) | Done, crop set `full` (bean, coffee, maize), model version `v0.3` |
| Model (MobileNetV3Small, 13 classes) | `v0.3` trained, validation accuracy 90.5% (previous `v0.2b`, 8 classes: 92.7%) |
| TF.js export | `web/model/` (about 3.9 MB, float32 weights, iPhone safe) |
| Browser inference (`web/infer.js`) | Done |
| Port of `build_response()` to JS | **Not started** (critical path) |
| Product UI | Not started |

## Classes (model v0.3, index order is the contract)

0 `bean_angular_leaf_spot`, 1 `bean_healthy`, 2 `bean_rust`, 3 `coffee_cercospora`, 4 `coffee_healthy`, 5 `coffee_leaf_miner`, 6 `coffee_leaf_rust`, 7 `coffee_phoma`, 8 `maize_common_rust`, 9 `maize_gray_leaf_spot`, 10 `maize_healthy`, 11 `maize_northern_leaf_blight`, 12 `unknown`

Fallback model `v0.2b` (bean + maize, 8 classes) is kept: `python3 cropdb.py set-cropset crop.db bean_maize`.

## Validation recall per class, model v0.3 (same distribution as training, so optimistic)

| Class | Recall | | Class | Recall |
|---|---|---|---|---|
| bean_angular_leaf_spot | 0.80 | | coffee_phoma | 0.80 |
| bean_healthy | 0.97 | | maize_common_rust | 1.00 |
| bean_rust | 0.91 | | maize_gray_leaf_spot | 0.96 |
| coffee_cercospora | 1.00 (only 8 images) | | maize_healthy | 0.98 |
| coffee_healthy | 1.00 | | maize_northern_leaf_blight | 0.73 |
| coffee_leaf_miner | 0.91 | | unknown | 0.98 |
| coffee_leaf_rust | 0.78 | | | |

## Repo layout

```
data_layer/   crop_db.sql (schema + seed), cropdb.py (Python data layer + CLI), crop.db (built)
model/        train.py (prep, train, export), verify.py + verify.mjs (parity check on JPEG files),
              diag.py + diag.mjs (parity on identical pixels), calibrate.py (confidence thresholds)
web/          infer.js (offline inference), test.html (phone test page), crop_bundle.json, model/ (TF.js)
docs/         sample response objects
```

## Contracts

1. Canonical disease IDs are the only link between model, database and UI.
2. `web/model/labels.json` must equal `model_classes` for the active model version. `infer.js` refuses to load otherwise.
3. Model input: RGB, 224x224, float in 0..255, no normalization (the model normalizes internally). Output: softmax over the classes above.
4. `infer.js` returns `{ model_version, backend, latency_ms, probs: { disease_id: probability } }`, which feeds `build_response(crop_id, probs, lang)`.

## Confidence rules

Applied after restricting probabilities to the picked crop plus `unknown`, then renormalizing. Thresholds live in the `meta` table.

1. `unknown` if the top class is `unknown` or top probability is below 0.40: ask for a new photo.
2. `high` if top probability is at least 0.90 and beats the second by at least 0.15: show the result.
3. Otherwise `uncertain`: ask up to 2 yes/no questions, recompute, then show the result or the top two with "see an expert".

## Commands

```
# data layer (laptop)
cd data_layer
python3 cropdb.py build crop.db
python3 cropdb.py set-cropset crop.db bean_maize     # or: full (needs coffee model)
python3 cropdb.py check crop.db
python3 cropdb.py export crop.db ../web/crop_bundle.json
python3 cropdb.py demo crop.db hi

# model (Google Colab, see model/train.py header)
python train.py smoke
python train.py prep bean_maize
python train.py train
python train.py export

# phone test (laptop and phone on the same wifi)
cd web && python3 -m http.server 8000      # then open http://<laptop-ip>:8000/test.html
```

## Calibration (validation set, model v0.3, `model/calibrate.py`)

The app's exact decision logic replayed on 715 held-out images (660 real crop leaf decisions, 165 other crop checks), with the crop picker simulated. Same distribution as training, so field numbers will be lower.

| threshold_high | Real leaves answered instantly | Accuracy when confident | Confident and wrong |
|---|---|---|---|
| 0.70 | 88% | 94.0% | 35 |
| 0.80 | 83% | 95.6% | 24 |
| **0.90 (chosen)** | **74%** | **97.8%** | **11** |

Confident errors cluster in coffee_phoma and maize_northern_leaf_blight, so 0.90 sends more of those to the yes/no questions. Other crops' leaves caught as unknown: 99.4%. Real crop leaves wrongly rejected: 0.2%.

## Export verification

Fed identical pixels, the TF.js model matches Keras: float32 max probability difference 0.0000, float16 0.0045, 16/16 agreement (`diag.py` + `diag.mjs`). uint8 quantization was tried first and replaced by float16; float16 then failed to load on iPhone Safari because mixing 2 and 4 byte weights leaves some 4 byte weights misaligned. The shipped model stores every weight as float32 (`model/dequant.py` converts a float16 export with identical outputs). Decoding the same JPEG with different libraries changes pixels by about 1 level (max 4), which only flips images that were already a coin toss (0.50 vs 0.50); the confidence rules send those to follow-up questions.

## Datasets and limitations

### Coffee data findings (measured with `model/d4_diag.py`)

| Class | Files | Unique files | Distinct leaves kept | Source |
|---|---|---|---|---|
| Cercospora | 7,681 | 322 | 75 | JMuBEN |
| Healthy | 20,163 | 63 JMuBEN + Uganda | 400 | JMuBEN2 gave only about 12 distinct leaves; the rest is Uganda field photos |
| Leaf miner | 16,978 | 1,639 | 294 | JMuBEN2 |
| Leaf rust | 9,369 | about 2,000 | 400 | JMuBEN + Uganda |
| Phoma | 7,681 | about 1,800 | 400 | JMuBEN + Uganda |

1. JMuBEN is heavily pre-augmented: most files are byte copies or rotated/flipped copies of a few leaves. The loader removes them before splitting, so one leaf cannot appear in both train and validation.
2. Source confound: healthy coffee is almost all Uganda field photos, while cercospora and leaf miner are only JMuBEN tight crops. The model may partly learn photo style instead of disease. Validation cannot detect this; the phone test is the check.
3. Cercospora has only 75 distinct leaves (8 in validation), so its 1.00 recall is not meaningful. Class weights compensate during training.


| Data | Source | Notes |
|---|---|---|
| Bean | iBean, Makerere AI Lab (Hugging Face `AI-Lab-Makerere/beans`), MIT | Field smartphone photos, Uganda |
| Maize | PlantVillage (Hugging Face `BrandonFors/Plant-Diseases-PlantVillage-Dataset`), CC BY-SA 3.0 | Lab photos, single leaves on plain backgrounds. Field accuracy will be lower. |
| Coffee | JMuBEN + JMuBEN2 (Mendeley, CC BY 4.0, Kenya) and the Uganda coffee leaf dataset (Mendeley k36wnd6knb, license not stated, used for research with attribution) | Pre-augmented; see findings above |
| Unknown | Leaves of 10 unsupported PlantVillage crops | Teaches "a leaf, but not ours". Does not cover every non-leaf photo. |

1. Validation accuracy is measured on the same sources as training. It is not a field accuracy number.
2. Maize images are lab images. For demos, photograph maize leaves on a plain background.
3. All advice content is `draft` until reviewed by an agronomist. No product names or doses are given.
4. Hindi translations are unverified.

## Colab environment notes (Python 3.13, TF 2.20)

`tensorflowjs` does not install cleanly. Working recipe:

```
pip uninstall -y tensorflowjs
pip install --no-deps "tensorflowjs==4.22.0"
pip install "packaging>=24.2" "tf_keras==2.20.*" tensorflow_hub importlib_resources "setuptools<81" jax "protobuf>=6.31.1,<8" datasets
# then create an empty package folder: site-packages/tensorflow_decision_forests/__init__.py
# then Runtime > Restart session
```
