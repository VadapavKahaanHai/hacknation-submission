// logic.js: JavaScript port of cropdb.build_response(). Runs fully offline from crop_bundle.json.
// Must behave exactly like data_layer/cropdb.py (checked by web/parity_test.mjs).
//   const L = CropLogic(bundle);
//   L.buildResponse(cropId, probs, lang, answers)   probs = { disease_id: probability } from infer.js

function CropLogic(bundle) {
  const meta = Object.fromEntries(bundle.meta.map(r => [r.key, r.value]));
  const tr = new Map(bundle.translations.map(r => [`${r.entity_type}|${r.entity_id}|${r.field}|${r.lang}`, r.text]));
  const diseases = Object.fromEntries(bundle.diseases.map(d => [d.disease_id, d]));
  const crops = Object.fromEntries(bundle.crops.map(c => [c.crop_id, c]));
  const urgency = Object.fromEntries(bundle.urgency_levels.map(u => [u.level, u]));
  const ui = Object.fromEntries(bundle.ui_strings.map(u => [u.key, u.text_en]));
  const num = k => parseFloat(meta[k]);

  // Returns [text, isFallback]. English is the source; other languages fall back to English.
  function t(type, id, field, lang, fallback) {
    if (lang === "en" || fallback === null || fallback === undefined) return [fallback ?? null, false];
    const hit = tr.get(`${type}|${String(id)}|${field}|${lang}`);
    return hit !== undefined ? [hit, false] : [fallback, true];
  }

  function getDiseaseRecord(diseaseId, lang = "en") {
    const d = diseases[diseaseId];
    if (!d) throw new Error(`No disease record for '${diseaseId}'`);
    const u = urgency[d.urgency];
    const fallbacks = [];
    const T = (type, id, field, en) => {
      const [text, fb] = t(type, id, field, lang, en);
      if (fb) fallbacks.push(`${type}:${id}:${field}`);
      return text;
    };
    const symptoms = bundle.symptoms
      .filter(s => s.disease_id === diseaseId)
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(s => ({ plant_part: s.plant_part, text: T("symptom", s.symptom_id, "text", s.text_en) }));
    return {
      disease_id: d.disease_id,
      crop_id: d.crop_id,
      crop_name: d.crop_id ? T("crop", d.crop_id, "name", crops[d.crop_id].name_en) : null,
      name: T("disease", diseaseId, "name", d.name_en),
      name_en: d.name_en,
      pathogen: d.pathogen,
      cause_type: d.cause_type,
      urgency: {
        level: d.urgency,
        label: T("urgency", d.urgency, "label", u.label_en),
        action: T("urgency", d.urgency, "action", u.action_en),
        color: u.color,
      },
      is_curable: !!d.is_curable,
      see_expert: !!d.see_expert,
      summary: T("disease", diseaseId, "summary", d.summary_en),
      why: T("disease", diseaseId, "why", d.why_en),
      symptoms,
      content_status: d.content_status,
      lang,
      untranslated_fields: fallbacks,
    };
  }

  function getAdvice(diseaseId, lang = "en") {
    const out = { today: [], treatment: [], prevention: [] };
    const rows = bundle.advice.filter(a => a.disease_id === diseaseId)
      .sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : a.sort_order - b.sort_order));
    for (const a of rows) {
      const [text, fb1] = t("advice", a.advice_id, "text", lang, a.text_en);
      const [note, fb2] = t("advice", a.advice_id, "safety_note", lang, a.safety_note);
      out[a.kind].push({ advice_id: a.advice_id, method: a.method, text, safety_note: note, is_fallback: fb1 || fb2 });
    }
    return out;
  }

  // Keep only the picked crop's classes plus 'unknown', renormalize, sort high to low.
  function restrictToCrop(cropId, probs) {
    const keep = new Set(bundle.diseases.filter(d => d.crop_id === cropId || d.disease_id === "unknown").map(d => d.disease_id));
    const sub = Object.entries(probs).filter(([k]) => keep.has(k));
    const total = sub.reduce((s, [, v]) => s + v, 0) || 1;
    return Object.fromEntries(sub.map(([k, v]) => [k, v / total]).sort((a, b) => b[1] - a[1]));
  }

  const r3 = x => Math.round(x * 1000) / 1000;

  function classifyConfidence(probs) {
    const hi = num("threshold_high"), unk = num("threshold_unknown"), marginMin = num("threshold_margin");
    const danger = num("threshold_danger_watch");
    const ranked = Object.entries(probs).sort((a, b) => b[1] - a[1]);
    const [top, p1] = ranked[0];
    const [second, p2] = ranked.length > 1 ? ranked[1] : [null, 0];
    const margin = p1 - p2;
    let level;
    if (top === "unknown" || p1 < unk) level = "unknown";
    else if (p1 >= hi && margin >= marginMin) level = "high";
    else level = "uncertain";
    const dangerWatch = ranked.slice(1).filter(([d, p]) => p >= danger && diseases[d].urgency === 3).map(([d]) => d);
    return { level, top, top_prob: r3(p1), second, second_prob: r3(p2), margin: r3(margin), danger_watch: dangerWatch };
  }

  function getFollowupQuestions(cropId, candidates, lang = "en", limit = 2) {
    const qs = [];
    for (const did of candidates) {
      for (const q of bundle.followup_questions.filter(q => q.crop_id === cropId && q.disease_id === did)) {
        qs.push({ question_id: q.question_id, disease_id: did, text: t("question", q.question_id, "text", lang, q.text_en)[0] });
      }
    }
    return qs.slice(0, limit);
  }

  function applyAnswers(probs, answers) {
    const p = { ...probs };
    for (const [qid, yes] of Object.entries(answers)) {
      const q = bundle.followup_questions.find(x => x.question_id === qid);
      if (q && q.disease_id in p) p[q.disease_id] *= yes ? q.yes_weight : q.no_weight;
    }
    const total = Object.values(p).reduce((s, v) => s + v, 0) || 1;
    return Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v / total]));
  }

  function uiText(key, lang) {
    return t("ui", key, "text", lang, ui[key])[0];
  }

  // The one object the app renders. probs = full model output keyed by disease_id.
  function buildResponse(cropId, probs, lang = "en", answers = null) {
    let p = restrictToCrop(cropId, probs);
    const hasAnswers = answers && Object.keys(answers).length > 0;
    if (hasAnswers) p = applyAnswers(p, answers);
    const conf = classifyConfidence(p);
    const resp = {
      schema_version: parseInt(meta.schema_version, 10),
      content_version: meta.content_version,
      model_version: meta.active_model_version,
      crop_id: cropId,
      lang,
      confidence: conf,
      disclaimer: uiText("disclaimer", lang),
    };
    if (conf.level === "unknown") {
      return Object.assign(resp, { status: "retake", message: uiText("retake", lang), diagnosis: null, advice: null });
    }
    if (conf.level === "uncertain" && !hasAnswers) {
      const qs = getFollowupQuestions(cropId, [conf.top, conf.second], lang);
      if (qs.length) {
        return Object.assign(resp, { status: "needs_answers", message: uiText("uncertain", lang), questions: qs, diagnosis: null, advice: null });
      }
    }
    resp.status = conf.level === "high" ? "result" : "possible";
    resp.diagnosis = getDiseaseRecord(conf.top, lang);
    resp.advice = getAdvice(conf.top, lang);
    if (conf.level === "uncertain") {
      resp.message = uiText("could_be", lang);
      resp.alternative = conf.second ? getDiseaseRecord(conf.second, lang) : null;
    }
    resp.danger_watch = conf.danger_watch.map(d => getDiseaseRecord(d, lang));
    resp.see_expert = resp.diagnosis.see_expert || conf.level !== "high";
    return resp;
  }

  return { buildResponse, getDiseaseRecord, getAdvice, restrictToCrop, classifyConfidence, uiText, meta, crops, diseases };
}

if (typeof module !== "undefined") module.exports = CropLogic;
