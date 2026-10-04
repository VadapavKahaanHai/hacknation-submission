// diag.mjs: feed TF.js the EXACT pixels Keras saw (ref_px.json from diag.py). No JPEG decoding in JS.
// If this agrees with Keras, the export is correct and the earlier FAIL came from image decoding.
// Usage in Colab:  !node diag.mjs web_model_f32
import * as tf from "@tensorflow/tfjs-node";
import fs from "fs";

const dir = process.argv[2] || "web_model";
const ref = JSON.parse(fs.readFileSync("ref_px.json", "utf8"));
const model = await tf.loadGraphModel(`file://${dir}/model.json`);
const [h, w, c] = ref.shape;
let agree = 0, maxDiff = 0;
ref.px.forEach((p, i) => {
  const out = tf.tidy(() => model.predict(tf.tensor3d(p, [h, w, c], "float32").expandDims(0)).squeeze().arraySync());
  const am = a => a.indexOf(Math.max(...a));
  if (am(out) === am(ref.keras[i])) agree++;
  out.forEach((v, j) => { maxDiff = Math.max(maxDiff, Math.abs(v - ref.keras[i][j])); });
});
console.log(`${dir}: identical pixels -> argmax agreement ${agree}/${ref.px.length}, max prob diff ${maxDiff.toFixed(4)}`);
console.log(agree === ref.px.length && maxDiff < 0.05
  ? "EXPORT IS CORRECT: the earlier mismatch comes from JPEG decoding, not the model conversion"
  : "CONVERSION PROBLEM: the TF.js graph computes something different from Keras");
