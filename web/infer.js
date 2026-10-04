// infer.js: offline browser inference. Output = { disease_id: probability } for all 12 classes.
// Load in the page (both cached by the service worker for offline use):
//   <script src="vendor/tf.min.js"></script>              (@tensorflow/tfjs, self-hosted, not CDN)
//   <script src="vendor/tf-backend-wasm.min.js"></script>  (optional fallback backend)
// Files served from the app: /model/model.json, /model/group1-shard*.bin, /model/labels.json,
//                            /data/crop_bundle.json

const Infer = (() => {
  let model = null;
  let meta = null;      // labels.json written by train.py
  let backend = null;

  // Call once at app start. bundle = parsed crop_bundle.json.
  // opts.backend: preferred backend ("wasm" starts fastest on iPhone). If a backend cannot run the
  // model (load or warm-up throws), the next one is tried automatically: wasm -> webgl -> cpu.
  async function load(bundle, modelUrl = "model/model.json", labelsUrl = "model/labels.json", opts = {}) {
    const t0 = performance.now();
    meta = await (await fetch(labelsUrl)).json();
    // Contract check: model labels must equal model_classes in the DB bundle, in order.
    const dbLabels = bundle.model_classes
      .filter(r => r.model_version === meta.model_version)
      .sort((a, b) => a.class_index - b.class_index)
      .map(r => r.disease_id);
    if (JSON.stringify(dbLabels) !== JSON.stringify(meta.labels)) {
      throw new Error(`Label mismatch between model ${meta.model_version} and crop_bundle.json`);
    }
    const order = [...new Set([opts.backend, "wasm", "webgl", "cpu"].filter(Boolean))];
    const tried = [];
    for (const b of order) {
      try {
        if (!(await tf.setBackend(b))) { tried.push(`${b}: unavailable`); continue; }
        await tf.ready();
        const t1 = performance.now();
        model = await tf.loadGraphModel(modelUrl);
        const t2 = performance.now();
        // Warm-up (compiles shaders on WebGL) and class-count check from a real prediction.
        const probe = tf.tidy(() => model.predict(tf.zeros([1, meta.input_size, meta.input_size, 3])));
        const outDim = probe.shape[probe.shape.length - 1];
        probe.dispose();
        if (outDim !== meta.labels.length) throw new Error(`Model outputs ${outDim} classes, labels has ${meta.labels.length}`);
        backend = b;
        return { backend, model_version: meta.model_version, tried,
                 download_ms: Math.round(t2 - t1), warmup_ms: Math.round(performance.now() - t2),
                 total_ms: Math.round(performance.now() - t0) };
      } catch (e) {
        tried.push(`${b}: ${e.message}`);
        if (e.message.startsWith("Model outputs")) throw e;
      }
    }
    throw new Error("No backend could run the model. " + tried.join(" | "));
  }

  // imgEl: <img>, <canvas>, ImageBitmap or <video>. Returns the frozen output format.
  async function predict(imgEl) {
    const t0 = performance.now();
    const probs = tf.tidy(() => {
      const x = tf.browser.fromPixels(imgEl, 3)                       // int32 [H,W,3] in 0..255
        .resizeBilinear([meta.input_size, meta.input_size])            // same as training resize
        .toFloat()                                                      // keep 0..255: model rescales itself
        .expandDims(0);                                                 // [1,224,224,3]
      return model.predict(x).squeeze();                                // softmax [12]
    });
    const arr = await probs.data();
    probs.dispose();

    const out = {};
    meta.labels.forEach((id, i) => { out[id] = Number(arr[i].toFixed(4)); });
    return {
      model_version: meta.model_version,
      backend,
      latency_ms: Math.round(performance.now() - t0),
      probs: out,   // { "tomato_late_blight": 0.81, ..., "unknown": 0.01 }  all 12 keys, sums to ~1
    };
  }

  return { load, predict };
})();

if (typeof module !== "undefined") module.exports = Infer;
