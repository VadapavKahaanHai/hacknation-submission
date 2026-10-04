// verify.mjs: run the exported TF.js model on the images in ref.json and compare to Keras.
// Colab:  !npm install --silent @tensorflow/tfjs-node   then   !node verify.mjs
// Pass criteria: argmax agreement 100% and max |prob diff| < 0.05.  Usage: node verify.mjs [model_folder]
import * as tf from "@tensorflow/tfjs-node";
import fs from "fs";

const dir = process.argv[2] || "web_model";   // e.g. node verify.mjs web_model_f16
const meta = JSON.parse(fs.readFileSync(`${dir}/labels.json`, "utf8"));
const ref = JSON.parse(fs.readFileSync("ref.json", "utf8"));
const model = await tf.loadGraphModel(`file://${dir}/model.json`);
console.log("model folder:", dir);

// Output shape metadata is not filled in by our export, so check a real prediction instead.
const probe = tf.tidy(() => model.predict(tf.zeros([1, meta.input_size, meta.input_size, 3])));
const outDim = probe.shape[probe.shape.length - 1];
probe.dispose();
console.log("input:", model.inputs[0].name, JSON.stringify(model.inputs[0].shape), "output classes:", outDim);
if (outDim !== meta.labels.length) throw new Error(`model outputs ${outDim} classes, labels.json has ${meta.labels.length}`);

let agree = 0, correct = 0, maxDiff = 0;
const t0 = Date.now();
for (const r of ref) {
  const probs = tf.tidy(() => {
    // Identical preprocessing to infer.js: RGB, bilinear 224, float 0..255, no normalization.
    const x = tf.node.decodeImage(fs.readFileSync(r.path), 3)
      .resizeBilinear([meta.input_size, meta.input_size]).toFloat().expandDims(0);
    return model.predict(x).squeeze();
  });
  const js = Array.from(await probs.data());
  probs.dispose();
  const argmax = a => a.indexOf(Math.max(...a));
  const jsTop = argmax(js), kTop = argmax(r.keras);
  if (jsTop === kTop) agree++;
  if (meta.labels[jsTop] === r.label) correct++;
  js.forEach((v, i) => { maxDiff = Math.max(maxDiff, Math.abs(v - r.keras[i])); });
  const flag = jsTop === kTop ? "  " : "!!";
  console.log(`${flag} ${r.label.padEnd(28)} js=${meta.labels[jsTop].padEnd(28)} keras=${meta.labels[kTop]}`);
}
const n = ref.length;
console.log(`\nargmax agreement JS vs Keras: ${agree}/${n}`);
console.log(`max |prob diff|: ${maxDiff.toFixed(4)}`);
console.log(`matches true label (training images, optimistic): ${correct}/${n}`);
console.log(`avg latency (Colab CPU, not a phone): ${Math.round((Date.now() - t0) / n)} ms`);
console.log(agree === n && maxDiff < 0.05 ? "PASS: export is faithful" : "FAIL: see !! rows above");

// Sample in the frozen output format, for whoever ports build_response():
const sample = {};
meta.labels.forEach((id, i) => { sample[id] = Number(ref[0].keras[i].toFixed(4)); });
console.log("\nsample probs object:", JSON.stringify({ model_version: meta.model_version, probs: sample }));
