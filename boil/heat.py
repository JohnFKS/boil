"""火候：把四维掌握度、到期卡与时间衰减压缩成一个可解释的 0–100 温度。

T 不是科学测量值，而是产品状态量：
- 0：未点火（没有证据）
- 1–39：温热（有理解，但不稳）
- 40–79：升温中（可以尝试更高强度）
- 80–99：接近沸腾（该做极致对标/费曼）
- 100：四维过线且重复足够，已烧开
"""
from __future__ import annotations

from typing import Any

from .db import Database, json_loads
from .mastery import FACETS, get_mastery, get_mastery_bulk
from .utils import clamp, new_id, now, now_iso, parse_iso, round4

HEAT_LABELS = [
    (0, "未点火"),
    (40, "温热"),
    (80, "升温中"),
    (99.5, "接近沸腾"),
    (100.1, "已烧开"),
]


def _last_evidence(m: dict[str, Any]) -> str | None:
    vals = []
    for f in FACETS:
        item = (m.get("facets") or {}).get(f) or {}
        if item.get("last_evidence_at"):
            vals.append(item["last_evidence_at"])
    return max(vals) if vals else None


def compute_heat(m: dict[str, Any], due_count: int = 0, now_dt=None) -> float:
    reps = int(m.get("reps") or 0)
    if reps <= 0:
        return 0.0
    facets = m.get("facets") or {}
    scores = [float((facets.get(f) or {}).get("score") or 0.0) for f in FACETS]
    base = sum(scores) / len(scores) * 100
    if all(s >= 0.8 for s in scores) and reps >= 4:
        base = max(base, 96.0)
    last = _last_evidence(m)
    days = 0.0
    if last:
        days = max(0.0, ((now_dt or now()) - parse_iso(last)).total_seconds() / 86400.0)
    decay = max(0.0, days - 3.0) * 2.2
    penalty = min(20.0, max(0, int(due_count)) * 4.0)
    return round4(clamp(base - decay - penalty, 0.0, 100.0))


def heat_label(value: float) -> str:
    if value <= 0:
        return "未点火"
    if value < 40:
        return "温热"
    if value < 80:
        return "升温中"
    if value < 99.5:
        return "接近沸腾"
    return "已烧开"


def due_count(db: Database, user_id: str, unit_id: str) -> int:
    return int(db.scalar(
        "SELECT COUNT(*) FROM cards WHERE user_id=? AND unit_id=? AND due_at<=? AND state!='new'",
        (user_id, unit_id, now_iso()), 0,
    ) or 0)


def heat_for_unit(db: Database, user_id: str, unit_id: str) -> float:
    m = get_mastery(db, user_id, unit_id)
    return compute_heat(m, due_count(db, user_id, unit_id))


def heat_bulk(db: Database, user_id: str, units: list[dict[str, Any]],
              due_map: dict[str, int] | None = None) -> dict[str, dict[str, Any]]:
    due_map = due_map or {}
    bulk = get_mastery_bulk(db, user_id, [u["id"] for u in units])
    out = {}
    for u in units:
        m = bulk.get(u["id"], {})
        h = compute_heat(m, due_map.get(u["id"], 0))
        out[u["id"]] = {"heat": h, "heat_label": heat_label(h), **m}
    return out


def pn_state(db: Database, user_id: str, unit: dict[str, Any]) -> dict[str, Any]:
    """PN 缺口：最近一次苏格拉底/费曼报告里明确说缺失的步骤。"""
    steps = unit.get("steps") or unit.get("key_points") or []
    total = len(steps)
    gaps: list[str] = []
    source = None
    latest = None
    row = db.one(
        "SELECT meta_json, ended_at FROM sessions WHERE user_id=? AND unit_id=? "
        "AND kind='socratic' AND status='completed' ORDER BY ended_at DESC LIMIT 1",
        (user_id, unit["id"]),
    )
    if row:
        meta = json_loads(row.get("meta_json"), {})
        report = meta.get("report") or {}
        gaps = [str(x) for x in (report.get("gaps") or []) if str(x).strip()]
        if report:
            source = "socratic"
            latest = row.get("ended_at")
    exp = db.one(
        "SELECT score_json, created_at FROM explanations WHERE user_id=? AND unit_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, unit["id"]),
    )
    if exp:
        data = json_loads(exp.get("score_json"), {})
        fg = [str(x) for x in (data.get("gaps") or []) if str(x).strip()]
        if fg and (not gaps or (exp.get("created_at") or "") > (latest or "")):
            gaps = fg
            source = "feynman"
            latest = exp.get("created_at")
        elif not source:
            source = "feynman" if data else None
            latest = exp.get("created_at")
    unique = []
    for g in gaps:
        if g not in unique:
            unique.append(g)
    return {
        "total": total,
        "missing": unique[:6],
        "source": source,
        "latest_at": latest,
        "complete": (not unique) if source else None,
    }


def pn_diff(unit: dict[str, Any], gaps: list[str] | None) -> list[str]:
    """把报告缺口限制在标准步骤/关键点范围内，便于历史分析。"""
    if not gaps:
        return []
    steps = unit.get("steps") or unit.get("key_points") or []
    if not steps:
        return [str(g)[:120] for g in gaps[:6]]
    return [str(g)[:120] for g in gaps[:6]]


def record_boil(db: Database, user_id: str, unit_id: str, mode: str, level: int,
                result: dict[str, Any], heat_before: float, heat_after: float,
                pn_diff_items: list[str] | None = None,
                advice_id: str | None = None, accepted: int = 0) -> str:
    from .db import json_dumps
    rid = new_id("br")
    db.insert("boil_records", {
        "id": rid,
        "user_id": user_id,
        "unit_id": unit_id,
        "mode": mode,
        "level": int(level),
        "result_json": json_dumps(result or {}),
        "heat_before": round4(float(heat_before)),
        "heat_after": round4(float(heat_after)),
        "pn_diff_json": json_dumps(pn_diff_items or []),
        "advice_id": advice_id,
        "accepted": int(accepted),
        "created_at": now_iso(),
    })
    return rid


def history(db: Database, user_id: str, limit: int = 12,
            unit_id: str | None = None) -> list[dict[str, Any]]:
    clause = " AND r.unit_id=?" if unit_id else ""
    params: list[Any] = [user_id]
    if unit_id:
        params.append(unit_id)
    rows = db.query(
        "SELECT r.*, u.title AS unit_title FROM boil_records r "
        "LEFT JOIN units u ON u.id=r.unit_id WHERE r.user_id=?" + clause +
        " ORDER BY r.created_at DESC LIMIT ?",
        (*params, min(max(int(limit), 1), 100)),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["result"] = json_loads(d.pop("result_json", "{}"), {})
        d["pn_diff"] = json_loads(d.pop("pn_diff_json", "[]"), [])
        out.append(d)
    return out


def summary(db: Database, user_id: str, units: list[dict[str, Any]],
            due_map: dict[str, int] | None = None) -> dict[str, Any]:
    states = heat_bulk(db, user_id, units, due_map)
    vals = [states[u["id"]]["heat"] for u in units]
    return {
        "average_heat": round4(sum(vals) / len(vals)) if vals else 0.0,
        "boiling": sum(1 for v in vals if v >= 99.5),
        "warm": sum(1 for v in vals if 0 < v < 40),
        "rising": sum(1 for v in vals if 40 <= v < 80),
        "near": sum(1 for v in vals if 80 <= v < 99.5),
        "cold": sum(1 for v in vals if v <= 0),
    }
