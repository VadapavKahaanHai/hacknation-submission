"""
cropdb.py: Sprint 1 data layer. Pure functions over a read-only SQLite file.
FastAPI (or any caller) imports this; nothing here depends on FastAPI.

CLI
  python3 cropdb.py build crop.db          # build DB from crop_db.sql
  python3 cropdb.py check crop.db          # pre-ship consistency checks
  python3 cropdb.py export crop.db out.json # offline bundle for the web app
  python3 cropdb.py demo crop.db [lang]    # print the coffee leaf rust sample response
  python3 cropdb.py set-cropset crop.db bean_maize   # coffee fallback (or 'full' to switch back)
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Literal, Optional, TypedDict

Confidence = Literal["high", "uncertain", "unknown"]
AdviceKind = Literal["today", "treatment", "prevention"]


# ---------------------------------------------------------------- connection

def connect(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection, safe to share across FastAPI threads."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _tr(con, entity_type: str, entity_id, field: str, lang: str, fallback: Optional[str]) -> tuple[Optional[str], bool]:
    """Return (text, is_fallback). Falls back to English if no translation exists."""
    if lang == "en" or fallback is None:
        return fallback, False
    row = con.execute(
        "SELECT text FROM translations WHERE entity_type=? AND entity_id=? AND field=? AND lang=?",
        (entity_type, str(entity_id), field, lang)).fetchone()
    return (row["text"], False) if row else (fallback, True)


def _meta(con, key: str) -> str:
    return con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()["value"]


# ---------------------------------------------------------------- the four contract functions

def normalize_label(raw: str) -> str:
    """'Corn_(maize)___Gray leaf-spot ' -> 'corn_maize_gray_leaf_spot'. No fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def map_label_to_canonical(con: sqlite3.Connection, source: str, raw_label: str) -> Optional[str]:
    """
    Raw dataset label -> canonical disease_id.
    Returns None if the label is deliberately dropped (out of MVP scope).
    Raises KeyError if the label is not listed at all (never guess).
    """
    key = normalize_label(raw_label)
    for row in con.execute("SELECT raw_label, disease_id FROM label_aliases WHERE source=?", (source,)):
        if normalize_label(row["raw_label"]) == key:
            return row["disease_id"]
    raise KeyError(f"Unmapped label {source}:'{raw_label}' (normalized '{key}'). Add it to label_aliases.")


def get_disease_record(con: sqlite3.Connection, disease_id: str, lang: str = "en") -> dict:
    """Disease + crop + urgency + symptoms, translated with English fallback. KeyError if unknown ID."""
    d = con.execute("""
        SELECT d.*, u.label_en AS urgency_label_en, u.action_en AS urgency_action_en, u.color AS urgency_color,
               c.name_en AS crop_name_en
        FROM diseases d JOIN urgency_levels u ON u.level = d.urgency
        LEFT JOIN crops c ON c.crop_id = d.crop_id
        WHERE d.disease_id=?""", (disease_id,)).fetchone()
    if d is None:
        raise KeyError(f"No disease record for '{disease_id}'")
    fallbacks = []

    def t(etype, eid, field, en):
        text, fb = _tr(con, etype, eid, field, lang, en)
        if fb:
            fallbacks.append(f"{etype}:{eid}:{field}")
        return text

    symptoms = [
        {"plant_part": s["plant_part"], "text": t("symptom", s["symptom_id"], "text", s["text_en"])}
        for s in con.execute("SELECT * FROM symptoms WHERE disease_id=? ORDER BY sort_order", (disease_id,))
    ]
    return {
        "disease_id": d["disease_id"],
        "crop_id": d["crop_id"],
        "crop_name": t("crop", d["crop_id"], "name", d["crop_name_en"]) if d["crop_id"] else None,
        "name": t("disease", disease_id, "name", d["name_en"]),
        "name_en": d["name_en"],
        "pathogen": d["pathogen"],
        "cause_type": d["cause_type"],
        "urgency": {
            "level": d["urgency"],
            "label": t("urgency", d["urgency"], "label", d["urgency_label_en"]),
            "action": t("urgency", d["urgency"], "action", d["urgency_action_en"]),
            "color": d["urgency_color"],
        },
        "is_curable": bool(d["is_curable"]),
        "see_expert": bool(d["see_expert"]),
        "summary": t("disease", disease_id, "summary", d["summary_en"]),
        "why": t("disease", disease_id, "why", d["why_en"]),
        "symptoms": symptoms,
        "content_status": d["content_status"],
        "lang": lang,
        "untranslated_fields": fallbacks,
    }


def get_advice(con: sqlite3.Connection, disease_id: str, lang: str = "en") -> dict[AdviceKind, list[dict]]:
    """Advice grouped by kind, ordered. Chemical rows always carry a safety_note."""
    out: dict[str, list[dict]] = {"today": [], "treatment": [], "prevention": []}
    for a in con.execute("SELECT * FROM advice WHERE disease_id=? ORDER BY kind, sort_order", (disease_id,)):
        text, fb1 = _tr(con, "advice", a["advice_id"], "text", lang, a["text_en"])
        note, fb2 = _tr(con, "advice", a["advice_id"], "safety_note", lang, a["safety_note"])
        out[a["kind"]].append({
            "advice_id": a["advice_id"], "method": a["method"], "text": text,
            "safety_note": note, "is_fallback": fb1 or fb2,
        })
    return out


# ---------------------------------------------------------------- confidence rules

def restrict_to_crop(con, crop_id: str, probs: dict[str, float]) -> dict[str, float]:
    """Keep only classes of the picked crop plus 'unknown', renormalize to sum 1."""
    keep = {r[0] for r in con.execute(
        "SELECT disease_id FROM diseases WHERE crop_id=? OR disease_id='unknown'", (crop_id,))}
    sub = {k: v for k, v in probs.items() if k in keep}
    total = sum(sub.values()) or 1.0
    return {k: v / total for k, v in sorted(sub.items(), key=lambda kv: -kv[1])}


def classify_confidence(con, probs: dict[str, float]) -> dict:
    """probs: crop-restricted, renormalized. Returns level + top candidates + danger flag."""
    hi, unk, margin_min = (float(_meta(con, k)) for k in ("threshold_high", "threshold_unknown", "threshold_margin"))
    danger = float(_meta(con, "threshold_danger_watch"))
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    (top, p1), (second, p2) = ranked[0], (ranked[1] if len(ranked) > 1 else (None, 0.0))
    margin = p1 - p2

    if top == "unknown" or p1 < unk:
        level: Confidence = "unknown"
    elif p1 >= hi and margin >= margin_min:
        level = "high"
    else:
        level = "uncertain"

    # Safety bias: an urgency-3 disease that is not top-1 but still plausible gets surfaced.
    danger_watch = [
        did for did, p in ranked[1:]
        if p >= danger and con.execute("SELECT urgency FROM diseases WHERE disease_id=?", (did,)).fetchone()[0] == 3
    ]
    return {"level": level, "top": top, "top_prob": round(p1, 3), "second": second,
            "second_prob": round(p2, 3), "margin": round(margin, 3), "danger_watch": danger_watch}


def get_followup_questions(con, crop_id: str, candidates: list[str], lang: str = "en", limit: int = 2) -> list[dict]:
    qs = []
    for did in candidates:
        for q in con.execute("SELECT * FROM followup_questions WHERE crop_id=? AND disease_id=?", (crop_id, did)):
            text, _ = _tr(con, "question", q["question_id"], "text", lang, q["text_en"])
            qs.append({"question_id": q["question_id"], "disease_id": did, "text": text})
    return qs[:limit]


def apply_answers(con, probs: dict[str, float], answers: dict[str, bool]) -> dict[str, float]:
    """answers: {question_id: True/False}. Reweights and renormalizes."""
    p = dict(probs)
    for qid, yes in answers.items():
        q = con.execute("SELECT * FROM followup_questions WHERE question_id=?", (qid,)).fetchone()
        if q and q["disease_id"] in p:
            p[q["disease_id"]] *= q["yes_weight"] if yes else q["no_weight"]
    total = sum(p.values()) or 1.0
    return {k: v / total for k, v in p.items()}


def _ui(con, key: str, lang: str) -> str:
    en = con.execute("SELECT text_en FROM ui_strings WHERE key=?", (key,)).fetchone()["text_en"]
    return _tr(con, "ui", key, "text", lang, en)[0]


def build_response(con, crop_id: str, probs: dict[str, float], lang: str = "en",
                   answers: Optional[dict[str, bool]] = None) -> dict:
    """The one object the frontend renders. probs = full model softmax keyed by disease_id."""
    p = restrict_to_crop(con, crop_id, probs)
    if answers:
        p = apply_answers(con, p, answers)
    conf = classify_confidence(con, p)
    resp = {
        "schema_version": int(_meta(con, "schema_version")),
        "content_version": _meta(con, "content_version"),
        "model_version": _meta(con, "active_model_version"),
        "crop_id": crop_id,
        "lang": lang,
        "confidence": conf,
        "disclaimer": _ui(con, "disclaimer", lang),
    }
    if conf["level"] == "unknown":
        resp.update(status="retake", message=_ui(con, "retake", lang), diagnosis=None, advice=None)
        return resp
    if conf["level"] == "uncertain" and not answers:
        qs = get_followup_questions(con, crop_id, [conf["top"], conf["second"]], lang)
        if qs:
            resp.update(status="needs_answers", message=_ui(con, "uncertain", lang),
                        questions=qs, diagnosis=None, advice=None)
            return resp
    resp["status"] = "result" if conf["level"] == "high" else "possible"
    resp["diagnosis"] = get_disease_record(con, conf["top"], lang)
    resp["advice"] = get_advice(con, conf["top"], lang)
    if conf["level"] == "uncertain":
        resp["message"] = _ui(con, "could_be", lang)
        resp["alternative"] = get_disease_record(con, conf["second"], lang) if conf["second"] else None
    resp["danger_watch"] = [get_disease_record(con, d, lang) for d in conf["danger_watch"]]
    resp["see_expert"] = resp["diagnosis"]["see_expert"] or conf["level"] != "high"
    return resp


# ---------------------------------------------------------------- build / check / export

def build(db_path: str, sql_path: str = "crop_db.sql") -> None:
    Path(db_path).unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(Path(sql_path).read_text(encoding="utf-8"))
    con.commit()
    con.close()


def check(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    mv = con.execute("SELECT value FROM meta WHERE key='active_model_version'").fetchone()[0]
    err = [f"FK violation {r}" for r in con.execute("PRAGMA foreign_key_check")]
    idx = [r[0] for r in con.execute("SELECT class_index FROM model_classes WHERE model_version=? ORDER BY 1", (mv,))]
    if idx != list(range(len(idx))):
        err.append(f"class indices not contiguous: {idx}")
    for (did,) in con.execute("""SELECT m.disease_id FROM model_classes m JOIN diseases d USING(disease_id)
        WHERE m.model_version=? AND d.cause_type!='none'
        AND NOT EXISTS (SELECT 1 FROM advice a WHERE a.disease_id=m.disease_id AND a.kind='today')""", (mv,)):
        err.append(f"{did}: no 'today' advice")
    # Aliases of ENABLED crops (and 'unknown') must point at a class the active model outputs.
    for (did,) in con.execute("""SELECT DISTINCT a.disease_id FROM label_aliases a JOIN diseases d USING(disease_id)
        LEFT JOIN crops c ON c.crop_id = d.crop_id
        WHERE (d.crop_id IS NULL OR c.enabled = 1)
        AND a.disease_id NOT IN (SELECT disease_id FROM model_classes WHERE model_version=?)""", (mv,)):
        err.append(f"alias points at {did}, not a class in {mv}")
    # Active model and enabled crops must agree exactly.
    for (did,) in con.execute("""SELECT m.disease_id FROM model_classes m JOIN diseases d USING(disease_id)
        LEFT JOIN crops c ON c.crop_id = d.crop_id
        WHERE m.model_version=? AND d.crop_id IS NOT NULL AND c.enabled = 0""", (mv,)):
        err.append(f"{did} is a class in {mv} but its crop is disabled")
    for (did,) in con.execute("""SELECT d.disease_id FROM diseases d JOIN crops c USING(crop_id)
        WHERE c.enabled = 1 AND d.disease_id NOT IN
        (SELECT disease_id FROM model_classes WHERE model_version=?)""", (mv,)):
        err.append(f"{did} belongs to an enabled crop but is not a class in {mv}")
    orphan_sql = {
        "advice": "SELECT 1 FROM advice WHERE CAST(advice_id AS TEXT)=?",
        "symptom": "SELECT 1 FROM symptoms WHERE CAST(symptom_id AS TEXT)=?",
        "disease": "SELECT 1 FROM diseases WHERE disease_id=?",
        "crop": "SELECT 1 FROM crops WHERE crop_id=?",
        "question": "SELECT 1 FROM followup_questions WHERE question_id=?",
        "urgency": "SELECT 1 FROM urgency_levels WHERE CAST(level AS TEXT)=?",
        "ui": "SELECT 1 FROM ui_strings WHERE key=?",
    }
    for et, eid in con.execute("SELECT entity_type, entity_id FROM translations"):
        if not con.execute(orphan_sql[et], (eid,)).fetchone():
            err.append(f"translation points at missing {et}:{eid}")
    if err:
        print("FAIL"); [print("  -", e) for e in err]; return 1
    drafts = con.execute("SELECT COUNT(*) FROM diseases WHERE content_status='draft'").fetchone()[0]
    print(f"OK: {len(idx)} classes in {mv}, all mapped, all have 'today' advice, no orphan translations.")
    print(f"WARNING: {drafts} disease records still draft.")
    return 0


def export_bundle(db_path: str, out_path: str) -> None:
    """Every table as JSON, for an offline web app that cannot reach FastAPI."""
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    bundle = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")] for t in tables}
    Path(out_path).write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")


CROPSETS = {  # cropset name -> (active model version, enabled crops)
    "full": ("v0.2", {"bean", "coffee", "maize"}),
    "bean_maize": ("v0.2b", {"bean", "maize"}),
}


def set_cropset(db_path: str, name: str) -> None:
    """Switch the DB between crop set C and the bean+maize fallback. Re-export the bundle afterwards."""
    version, crops = CROPSETS[name]
    con = sqlite3.connect(db_path)
    con.execute("UPDATE meta SET value=? WHERE key='active_model_version'", (version,))
    con.execute("UPDATE meta SET value=? WHERE key='cropset'", (name,))
    for (cid,) in con.execute("SELECT crop_id FROM crops").fetchall():
        con.execute("UPDATE crops SET enabled=? WHERE crop_id=?", (1 if cid in crops else 0, cid))
    con.commit()
    print(f"cropset={name}, active_model_version={version}, enabled crops={sorted(crops)}")


DEMO_PROBS = {  # full 11-class softmax a model might emit for a coffee leaf rust photo
    "coffee_leaf_rust": 0.83, "coffee_phoma": 0.07, "coffee_healthy": 0.04, "unknown": 0.01,
    "bean_rust": 0.02, "bean_angular_leaf_spot": 0.01, "bean_healthy": 0.005,
    "maize_common_rust": 0.005, "maize_gray_leaf_spot": 0.002, "maize_healthy": 0.002,
    "maize_northern_leaf_blight": 0.001,
}

if __name__ == "__main__":
    cmd, db = sys.argv[1], sys.argv[2]
    if cmd == "build":
        build(db)
    elif cmd == "set-cropset":
        set_cropset(db, sys.argv[3])
    elif cmd == "check":
        sys.exit(check(db))
    elif cmd == "export":
        export_bundle(db, sys.argv[3])
    elif cmd == "demo":
        lang = sys.argv[3] if len(sys.argv) > 3 else "en"
        print(json.dumps(build_response(connect(db), "coffee", DEMO_PROBS, lang), ensure_ascii=False, indent=2))
