// app.js: farmer-facing screens. Everything runs on the phone: model (infer.js), advice logic (logic.js),
// advice data (crop_bundle.json). No server is needed after the first load.

const STR = {
  en: {
    app: "Leaf Check", loading: "Getting ready…", pick: "Which crop is it?",
    tip: "One leaf, close up, in daylight. Fill the photo with the leaf.",
    take: "Take photo of one leaf", gallery: "Choose from gallery", change: "Change crop",
    checking: "Checking the leaf…", see: "See result", yes: "Yes", no: "No", notsure: "Not sure",
    today: "What to do today", treatment: "Treatment", prevention: "Prevention",
    why: "Why this result", signs: "Signs to look for", confidence: "How sure the app is",
    read: "Read aloud", again: "New photo", retake: "Take a new photo", couldbe: "It could also be",
    safety: "Safety", offline: "✓ Ready to work without internet", online: "Works on this phone",
    photoOf: "Photo of", healthyNote: "No disease found. Keep checking your crop every week.",
    loadFail: "Could not start the app. Reload the page once with internet.",
  },
  hi: {
    app: "पत्ती जाँच", loading: "तैयार हो रहा है…", pick: "कौन सी फ़सल है?",
    tip: "एक पत्ती, पास से, दिन की रोशनी में। पत्ती से पूरी फ़ोटो भर दें।",
    take: "एक पत्ती की फ़ोटो लें", gallery: "गैलरी से चुनें", change: "फ़सल बदलें",
    checking: "पत्ती की जाँच हो रही है…", see: "नतीजा देखें", yes: "हाँ", no: "नहीं", notsure: "पता नहीं",
    today: "आज क्या करें", treatment: "इलाज", prevention: "बचाव",
    why: "यह नतीजा क्यों", signs: "किन लक्षणों को देखें", confidence: "ऐप कितना पक्का है",
    read: "सुनें", again: "नई फ़ोटो", retake: "नई फ़ोटो लें", couldbe: "यह भी हो सकता है",
    safety: "सुरक्षा", offline: "✓ बिना इंटरनेट के काम करने को तैयार", online: "इस फ़ोन पर चलता है",
    photoOf: "फ़ोटो:", healthyNote: "कोई बीमारी नहीं मिली। हर हफ़्ते फ़सल जाँचते रहें।",
    loadFail: "ऐप शुरू नहीं हो सका। इंटरनेट के साथ पेज एक बार फिर खोलें।",
  },
};
const EMOJI = { bean: "🫘", coffee: "☕", maize: "🌽" };

const state = { lang: "en", crop: null, photoUrl: null, probs: null, answers: {}, resp: null, L: null };
const $ = id => document.getElementById(id);
const S = k => (STR[state.lang] || STR.en)[k];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function show(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.toggle("on", s.id === `s-${name}`));
  window.scrollTo(0, 0);
}

function applyLang() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-t]").forEach(el => { el.textContent = S(el.dataset.t); });
  document.querySelectorAll(".lang button").forEach(b => b.classList.toggle("on", b.dataset.lang === state.lang));
  try { localStorage.setItem("lang", state.lang); } catch (e) { /* private mode */ }
  if (state.L) renderCrops();
  if (state.crop) $("camera-title").textContent = `${EMOJI[state.crop] || ""} ${cropName(state.crop)}`;
  if (state.probs) render();   // re-render the current result in the new language
}

function cropName(id) {
  const c = state.L.crops[id];
  const r = state.L.getDiseaseRecord(`${id}_healthy`, state.lang);   // carries the translated crop name
  return r.crop_name || c.name_en;
}

function renderCrops() {
  const list = Object.values(state.L.crops).filter(c => c.enabled).sort((a, b) => a.sort_order - b.sort_order);
  $("crop-list").innerHTML = list.map(c =>
    `<button class="crop" data-crop="${c.crop_id}"><span class="emoji">${EMOJI[c.crop_id] || "🌱"}</span>${esc(cropName(c.crop_id))}</button>`).join("");
  $("crop-list").querySelectorAll(".crop").forEach(b => b.onclick = () => {
    state.crop = b.dataset.crop;
    $("camera-title").textContent = `${EMOJI[state.crop] || ""} ${cropName(state.crop)}`;
    show("camera");
  });
}

// Draw the photo straight into a 224x224 canvas (whole image, like training), so huge camera
// photos never become huge tensors on a low-end phone.
function toCanvas(img) {
  const c = document.createElement("canvas");
  c.width = c.height = 224;
  const ctx = c.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, 0, 0, 224, 224);
  return c;
}

async function onPhoto(file) {
  if (!file) return;
  if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
  state.photoUrl = URL.createObjectURL(file);
  ["checking-photo", "q-photo", "r-photo"].forEach(id => { $(id).src = state.photoUrl; });
  show("checking");
  const img = new Image();
  img.onload = async () => {
    const r = await Infer.predict(toCanvas(img));
    state.probs = r.probs;
    state.answers = {};
    $("status").textContent = `${r.backend} · ${r.latency_ms} ms · model ${r.model_version}`;
    render();
  };
  img.src = state.photoUrl;
}

function render() {
  const answers = Object.keys(state.answers).length ? state.answers : null;
  const resp = state.L.buildResponse(state.crop, state.probs, state.lang, answers);
  state.resp = resp;
  if (resp.status === "needs_answers") return renderQuestions(resp);
  if (resp.status === "retake") return renderRetake(resp);
  renderResult(resp);
}

function renderQuestions(resp) {
  $("q-message").textContent = resp.message;
  $("q-list").innerHTML = resp.questions.map(q => `
    <div class="card" data-q="${q.question_id}">
      <p class="q">${esc(q.text)}</p>
      <div class="yn"><button data-a="yes">${S("yes")}</button><button data-a="no">${S("no")}</button></div>
      <button class="skip" data-a="skip">${S("notsure")}</button>
    </div>`).join("");
  $("q-list").querySelectorAll("[data-q]").forEach(card => {
    card.querySelectorAll("button").forEach(b => b.onclick = () => {
      const qid = card.dataset.q;
      card.querySelectorAll(".yn button").forEach(x => x.classList.remove("on"));
      if (b.dataset.a === "skip") delete state.pending[qid];
      else { state.pending[qid] = b.dataset.a === "yes"; b.classList.add("on"); }
    });
  });
  state.pending = {};
  show("questions");
}

$("q-done").onclick = () => {
  // No answer at all: show the best guess honestly as "possible", with "see an expert".
  state.answers = Object.keys(state.pending || {}).length ? { ...state.pending } : { __none__: true };
  render();
};

function renderRetake(resp) {
  $("r-body").innerHTML = `
    <div class="card possible"><h3>📷 ${esc(S("retake"))}</h3><p style="margin:0">${esc(resp.message)}</p></div>`;
  $("r-disclaimer").textContent = resp.disclaimer;
  show("result");
}

function adviceList(items) {
  return `<ul>${items.map(a => `<li>${esc(a.text)}${a.safety_note
    ? `<div class="safety">⚠️ <b>${esc(S("safety"))}:</b> ${esc(a.safety_note)}</div>` : ""}</li>`).join("")}</ul>`;
}

function renderResult(resp) {
  const d = resp.diagnosis, a = resp.advice, c = resp.confidence;
  const healthy = d.cause_type === "none";
  const pct = Math.round(c.top_prob * 100);
  let html = "";
  if (resp.status === "possible") html += `<div class="card possible"><p style="margin:0">${esc(resp.message)}</p></div>`;
  html += `
    <div class="card">
      <div class="muted">${esc(d.crop_name || "")}</div>
      <div class="diag-name">${healthy ? "✅ " : ""}${esc(d.name)}</div>
      <span class="badge" style="background:${d.urgency.color}">${esc(d.urgency.label)} · ${esc(d.urgency.action)}</span>
      <div class="conf">${esc(S("confidence"))}: ${pct}%<div class="bar"><i style="width:${pct}%"></i></div></div>
    </div>`;
  if (healthy) html += `<div class="card today"><p>${esc(S("healthyNote"))}</p></div>`;
  if (a.today.length) html += `<div class="card today"><h3>${esc(S("today"))}</h3><p>${esc(a.today[0].text)}</p></div>`;
  if (resp.alternative) html += `<div class="card possible"><b>${esc(S("couldbe"))}:</b> ${esc(resp.alternative.name)}</div>`;
  if (resp.see_expert) html += `<div class="card expert">👩‍🌾 ${esc(state.L.uiText("see_expert", state.lang))}</div>`;
  if (a.treatment.length) html += `<div class="card"><h3>${esc(S("treatment"))}</h3>${adviceList(a.treatment)}</div>`;
  if (a.prevention.length) html += `<div class="card"><h3>${esc(S("prevention"))}</h3>${adviceList(a.prevention)}</div>`;
  html += `<div class="card"><h3>${esc(S("why"))}</h3><p style="margin:0 0 8px">${esc(d.why)}</p>
    ${d.symptoms.length ? `<b>${esc(S("signs"))}</b><ul>${d.symptoms.map(s => `<li>${esc(s.text)}</li>`).join("")}</ul>` : ""}</div>`;
  $("r-body").innerHTML = html;
  $("r-disclaimer").textContent = resp.disclaimer;
  show("result");
}

// Read the result aloud with the phone's own voice (works offline for installed voices).
$("speak").onclick = () => {
  const r = state.resp;
  if (!r || !("speechSynthesis" in window)) return;
  let parts;
  if (!r.diagnosis) parts = [r.message];
  else {
    const a = r.advice;
    parts = [r.diagnosis.name, r.diagnosis.urgency.action,
      ...(a.today.length ? [S("today"), a.today[0].text] : []),
      ...a.treatment.flatMap(x => [x.text, x.safety_note].filter(Boolean)),
      ...a.prevention.map(x => x.text)];
  }
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(parts.join(". "));
  u.lang = state.lang === "hi" ? "hi-IN" : "en-IN";
  const v = speechSynthesis.getVoices().find(v => v.lang && v.lang.toLowerCase().startsWith(state.lang));
  if (v) u.voice = v;
  u.rate = 0.9;
  speechSynthesis.speak(u);
};

document.querySelectorAll("[data-go]").forEach(b => b.onclick = () => {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  show(b.dataset.go);
});
document.querySelectorAll(".lang button").forEach(b => b.onclick = () => { state.lang = b.dataset.lang; applyLang(); });
["in-camera", "in-gallery"].forEach(id => $(id).onchange = e => { onPhoto(e.target.files[0]); e.target.value = ""; });

async function start() {
  try { state.lang = localStorage.getItem("lang") || "en"; } catch (e) { /* ignore */ }
  applyLang();
  try {
    if (tf.wasm) tf.wasm.setWasmPaths("vendor/");
    const bundle = await (await fetch("crop_bundle.json")).json();
    state.L = CropLogic(bundle);
    const info = await Infer.load(bundle, "model/model.json", "model/labels.json", { backend: "wasm" });
    $("status").textContent = `${S("online")} · ${info.backend} · model ${info.model_version}`;
    applyLang();
    show("crop");
  } catch (e) {
    $("err-text").textContent = `${S("loadFail")} (${e.message})`;
    show("error");
    return;
  }
  // Offline: cache everything once, so the next visit works with no signal.
  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("sw.js");
      await navigator.serviceWorker.ready;
      $("status").textContent = `${S("offline")} · model ${state.L.meta.active_model_version}`;
    } catch (e) { /* plain http on a LAN: offline caching needs https */ }
  }
}
start();
