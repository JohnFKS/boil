"""四维掌握度：回忆 / 解释 / 应用 / 迁移。

掌握度不是一个自评分数，而是多条证据的加权移动平均：
- 卡片复习 → recall
- 苏格拉底报告 → explain / transfer
- 费曼讲解 → explain / transfer
- 探针作答 → apply / recall
"""
from __future__ import annotations

from typing import Any, Iterable

from .db import Database
from .utils import clamp, new_id, now, now_iso, parse_iso, round4

FACETS = ("recall", "explain", "apply", "transfer")
FACET_LABELS = {
    "recall": "回忆",
    "explain": "解释",
    "apply": "应用",
    "transfer": "迁移",
}
FACET_ICONS = {
    "recall": "◉",
    "explain": "◈",
    "apply": "▸",
    "transfer": "⇄",
}


def _empty_facet() -> dict[str, Any]:
    return {"score": 0.0, "confidence": 0.0, "reps": 0, "lapses": 0,
            "last_evidence_at": None, "updated_at": None}


def add_evidence(db: Database, user_id: str, unit_id: str, facet: str,
                 score: float, confidence: float = 0.7, weight: float = 1.0,
                 source: str = "", session_id: str | None = None,
                 attempt_id: str | None = None) -> dict[str, Any]:
    facet = facet if facet in FACETS else "recall"
    score = clamp(float(score), 0.0, 1.0)
    confidence = clamp(float(confidence), 0.05, 1.0)
    weight = clamp(float(weight), 0.05, 2.0)
    row = db.one(
        "SELECT * FROM mastery WHERE user_id=? AND unit_id=? AND facet=?",
        (user_id, unit_id, facet),
    )
    if row:
        alpha = clamp(0.16 + 0.38 * weight * confidence, 0.16, 0.62)
        new_score = row["score"] * (1.0 - alpha) + score * alpha
        new_conf = clamp(row["confidence"] + (1.0 - row["confidence"]) * 0.12 * confidence, 0.0, 1.0)
        reps = int(row["reps"]) + 1
        lapses = int(row["lapses"]) + (1 if score < 0.45 else 0)
    else:
        new_score = score
        new_conf = clamp(0.25 + 0.55 * confidence, 0.0, 1.0)
        reps = 1
        lapses = 1 if score < 0.45 else 0

    ts = now_iso()
    db.upsert("mastery", {
        "user_id": user_id,
        "unit_id": unit_id,
        "facet": facet,
        "score": round4(new_score),
        "confidence": round4(new_conf),
        "reps": reps,
        "lapses": lapses,
        "last_evidence_at": ts,
        "updated_at": ts,
    }, "user_id,unit_id,facet")

    evidence_id = new_id("evd")
    db.insert("evidence", {
        "id": evidence_id,
        "user_id": user_id,
        "unit_id": unit_id,
        "facet": facet,
        "score": round4(score),
        "confidence": round4(confidence),
        "weight": round4(weight),
        "source": source or "unknown",
        "session_id": session_id,
        "attempt_id": attempt_id,
        "created_at": ts,
    })
    return {"facet": facet, "score": round4(new_score), "confidence": round4(new_conf),
            "reps": reps, "lapses": lapses, "evidence_id": evidence_id}


def get_mastery(db: Database, user_id: str, unit_id: str) -> dict[str, Any]:
    rows = db.query(
        "SELECT facet, score, confidence, reps, lapses, last_evidence_at, updated_at "
        "FROM mastery WHERE user_id=? AND unit_id=?",
        (user_id, unit_id),
    )
    facets = {f: _empty_facet() for f in FACETS}
    for r in rows:
        if r["facet"] in facets:
            facets[r["facet"]] = {
                "score": r["score"], "confidence": r["confidence"],
                "reps": r["reps"], "lapses": r["lapses"],
                "last_evidence_at": r["last_evidence_at"],
                "updated_at": r["updated_at"],
            }
    return _shape(facets)


def get_mastery_bulk(db: Database, user_id: str, unit_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = list(unit_ids)
    out = {uid: {f: _empty_facet() for f in FACETS} for uid in ids}
    if not ids:
        return {uid: _shape(v) for uid, v in out.items()}
    placeholders = ",".join("?" for _ in ids)
    rows = db.query(
        f"SELECT unit_id, facet, score, confidence, reps, lapses, last_evidence_at, updated_at "
        f"FROM mastery WHERE user_id=? AND unit_id IN ({placeholders})",
        (user_id, *ids),
    )
    for r in rows:
        uid = r["unit_id"]
        if uid in out and r["facet"] in FACETS:
            out[uid][r["facet"]] = {
                "score": r["score"], "confidence": r["confidence"],
                "reps": r["reps"], "lapses": r["lapses"],
                "last_evidence_at": r["last_evidence_at"],
                "updated_at": r["updated_at"],
            }
    return {uid: _shape(v) for uid, v in out.items()}


def _shape(facets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scores = [facets[f]["score"] for f in FACETS]
    avg = sum(scores) / len(scores) if scores else 0.0
    reps = sum(facets[f]["reps"] for f in FACETS)
    weakest = min(FACETS, key=lambda f: facets[f]["score"]) if reps else None
    status = "new"
    if reps:
        status = "learning"
        if all(facets[f]["score"] >= 0.8 for f in FACETS) and reps >= 4:
            status = "boiled"
    return {
        "facets": facets,
        "average": round4(avg),
        "reps": reps,
        "weakest": weakest,
        "weakest_label": FACET_LABELS.get(weakest) if weakest else None,
        "status": status,
    }


def status_for(unit_mastery: dict[str, Any], due_count: int = 0,
               days_since_evidence: float | None = None) -> str:
    if unit_mastery["reps"] == 0:
        return "new"
    if due_count > 0 or (days_since_evidence is not None and days_since_evidence > 14
                          and unit_mastery["average"] < 0.85):
        return "rusting"
    if unit_mastery["status"] == "boiled":
        return "boiled"
    return "learning"


def days_since(value: str | None) -> float | None:
    if not value:
        return None
    return max(0.0, (now() - parse_iso(value)).total_seconds() / 86400.0)


def summary(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    where = "u.user_id IS NULL"  # placeholder
    params: list[Any] = [user_id]
    goal_clause = ""
    if goal_id:
        goal_clause = " AND u.goal_id=?"
        params.append(goal_id)
    rows = db.query(
        "SELECT u.id, u.title, u.importance, m.facet, m.score, m.confidence, m.reps, "
        "m.last_evidence_at FROM units u LEFT JOIN mastery m "
        "ON m.unit_id=u.id AND m.user_id=? WHERE 1=1" + goal_clause,
        tuple(params),
    )
    units: dict[str, dict[str, Any]] = {}
    for r in rows:
        uid = r["id"]
        if uid not in units:
            units[uid] = {"id": uid, "title": r["title"], "importance": r["importance"],
                          "facets": {f: 0.0 for f in FACETS}, "reps": 0,
                          "last_evidence_at": None}
        if r["facet"]:
            units[uid]["facets"][r["facet"]] = r["score"] or 0.0
            units[uid]["reps"] += r["reps"] or 0
            if r["last_evidence_at"] and (
                units[uid]["last_evidence_at"] is None
                or r["last_evidence_at"] > units[uid]["last_evidence_at"]
            ):
                units[uid]["last_evidence_at"] = r["last_evidence_at"]

    facet_avg = {}
    for f in FACETS:
        vals = [u["facets"][f] for u in units.values()]
        facet_avg[f] = round4(sum(vals) / len(vals)) if vals else 0.0
    avg = round4(sum(facet_avg.values()) / len(FACETS)) if units else 0.0
    boiled = sum(1 for u in units.values()
                 if all(u["facets"][f] >= 0.8 for f in FACETS) and u["reps"] >= 4)
    learning = sum(1 for u in units.values() if u["reps"] > 0 and u["id"] not in
                   {k for k, v in units.items() if all(v["facets"][f] >= 0.8 for f in FACETS) and v["reps"] >= 4})
    return {
        "units": len(units),
        "boiled": boiled,
        "learning": learning,
        "new": max(0, len(units) - boiled - learning),
        "average": avg,
        "facets": facet_avg,
        "facet_labels": FACET_LABELS,
        "weakest": min(FACET_AVG_ORDER, key=lambda f: facet_avg[f]) if units else None,
    }


FACET_AVG_ORDER = FACETS
