"""LearningPack 规范、校验、安装与查询。

Pack 是内容和能力的更新单元：
- Content Pack → units / probes / cards
- Subject Pack → 同一格式，附带学科元数据
- Skill Pack → 独立目录（提示词/策略），由 skills 层读取

安装是幂等的：同版本重复安装不会重置学习进度；新版本会更新内容，保留卡片 SRS 状态。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .db import Database, json_loads
from .events import emit
from .utils import new_id, now_iso, read_json, safe_div, slugify

PACK_SCHEMA_VERSION = "1.0"
PROBE_KINDS = {"recall", "explain", "apply", "boundary", "transfer", "contrast"}


class PackError(ValueError):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


def load_builtin_packs() -> list[dict[str, Any]]:
    packs = []
    for path in sorted(config.CONTENT_DIR.glob("packs/*.json")):
        data = read_json(path)
        if isinstance(data, dict):
            data.setdefault("_path", str(path))
            packs.append(data)
    return packs


def load_user_packs() -> list[dict[str, Any]]:
    packs = []
    for path in sorted(config.PACKS_DIR.glob("*.json")):
        data = read_json(path)
        if isinstance(data, dict):
            data.setdefault("_path", str(path))
            packs.append(data)
    return packs


def all_available_packs() -> list[dict[str, Any]]:
    seen = set()
    out = []
    for p in load_builtin_packs() + load_user_packs():
        pid = p.get("id")
        if pid and pid not in seen:
            seen.add(pid)
            out.append(p)
    return out


def pack_quality(pack: dict[str, Any]) -> dict[str, Any]:
    units = [u for u in (pack.get("units") or []) if isinstance(u, dict)]
    total = len(units)
    if not total:
        return {"atomic_rate": 0.0, "probe_coverage": 0.0, "dag_ok": False,
                "score": 0.0, "atomic_units": 0, "total_units": 0,
                "issues": ["没有单元"]}
    atomic = 0
    covered = 0
    issues = []
    required = {"recall", "explain", "apply", "boundary"}
    for u in units:
        probes = u.get("probes") or []
        kinds = {p.get("kind") for p in probes if isinstance(p, dict)}
        key_points = u.get("key_points") or derive_key_points(u)
        is_atomic = bool(u.get("essence")) and len(probes) >= 2 and len(key_points) >= 3 \
            and (len(u.get("steps") or []) >= 2 or len(u.get("boundaries") or []) >= 1)
        if is_atomic:
            atomic += 1
        else:
            issues.append(f"{u.get('title', u.get('id', '?'))}: 缺少本质/探针/关键点")
        if required.issubset(kinds):
            covered += 1
    # DAG 无环
    ids = {u.get("id") for u in units}
    edges = {u.get("id"): [] for u in units}
    indeg = {u.get("id"): 0 for u in units}
    for u in units:
        for rel in u.get("relations") or []:
            target = rel.get("to") if isinstance(rel, dict) else None
            if target in ids and u.get("id") != target:
                edges[u.get("id")].append(target)
                indeg[target] += 1
    ready = [uid for uid, n in indeg.items() if n == 0]
    seen = 0
    while ready:
        uid = ready.pop()
        seen += 1
        for t in edges.get(uid, []):
            indeg[t] -= 1
            if indeg[t] == 0:
                ready.append(t)
    dag_ok = seen == len(ids)
    atomic_rate = atomic / total
    coverage = covered / total
    score = round((atomic_rate * 0.5 + coverage * 0.4 + (1.0 if dag_ok else 0.0) * 0.1), 4)
    return {
        "atomic_rate": round(atomic_rate, 4),
        "probe_coverage": round(coverage, 4),
        "dag_ok": dag_ok,
        "score": score,
        "atomic_units": atomic,
        "total_units": total,
        "issues": issues[:6],
    }


def pack_summary(pack: dict[str, Any], installed: bool = False,
                 goal_id: str | None = None) -> dict[str, Any]:
    units = pack.get("units") or []
    return {
        "id": pack.get("id"),
        "version": pack.get("version", "1.0.0"),
        "kind": pack.get("kind", "subject"),
        "name": pack.get("name", "未命名内容包"),
        "description": pack.get("description", ""),
        "subject": pack.get("subject", ""),
        "language": pack.get("language", "zh-CN"),
        "license": pack.get("license", ""),
        "source": pack.get("source", {}),
        "unit_count": len(units),
        "estimated_minutes": sum(int(u.get("estimated_minutes") or 45) for u in units),
        "tags": pack.get("tags", []),
        "installed": installed,
        "goal_id": goal_id,
        "cover": pack.get("cover", "ember"),
        "quality": pack_quality(pack),
    }


def validate_pack(pack: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["Pack 必须是 JSON 对象"]
    for key in ("id", "name", "version", "units"):
        if not pack.get(key):
            errors.append(f"缺少字段 {key}")
    units = pack.get("units")
    if not isinstance(units, list) or not units:
        errors.append("units 必须是非空数组")
        return errors
    if len(units) > 100:
        errors.append("单个 Pack 最多 100 个单元")
    ids: set[str] = set()
    for i, unit in enumerate(units):
        prefix = f"units[{i}]"
        if not isinstance(unit, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        uid = unit.get("id")
        if not uid:
            errors.append(f"{prefix} 缺少 id")
        elif uid in ids:
            errors.append(f"{prefix} id 重复：{uid}")
        else:
            ids.add(uid)
        if not unit.get("title"):
            errors.append(f"{prefix} 缺少 title")
        if not unit.get("essence"):
            errors.append(f"{prefix} 缺少 essence")
        probes = unit.get("probes")
        if not isinstance(probes, list) or not probes:
            errors.append(f"{prefix} 至少需要 1 个 probe")
        else:
            kinds = set()
            for j, probe in enumerate(probes):
                if not isinstance(probe, dict) or not probe.get("prompt"):
                    errors.append(f"{prefix}.probes[{j}] 缺少 prompt")
                    continue
                kind = probe.get("kind", "recall")
                if kind not in PROBE_KINDS:
                    errors.append(f"{prefix}.probes[{j}] 未知 kind：{kind}")
                kinds.add(kind)
            if "recall" not in kinds:
                errors.append(f"{prefix} 至少需要一个 recall probe")
        cards = unit.get("cards")
        if cards is not None and not isinstance(cards, list):
            errors.append(f"{prefix}.cards 必须是数组")
        for key in ("difficulty", "importance"):
            if key in unit:
                try:
                    v = int(unit[key])
                    if not 1 <= v <= 5:
                        errors.append(f"{prefix}.{key} 必须在 1–5")
                except (TypeError, ValueError):
                    errors.append(f"{prefix}.{key} 必须是整数")
    # 关系目标必须存在
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        for rel in unit.get("relations") or []:
            target = rel.get("to") if isinstance(rel, dict) else None
            if target and target not in ids:
                errors.append(f"units[{i}] 关系指向不存在的单元：{target}")
    return errors


def normalize_pack(pack: dict[str, Any], source: str = "builtin") -> dict[str, Any]:
    p = dict(pack)
    p.setdefault("schema_version", PACK_SCHEMA_VERSION)
    p.setdefault("kind", "subject")
    p.setdefault("version", "1.0.0")
    p.setdefault("language", "zh-CN")
    p.setdefault("license", "CC-BY-4.0")
    p.setdefault("source", {"system": source, "generated_at": now_iso()})
    p.setdefault("description", "")
    p.setdefault("subject", "")
    p.setdefault("tags", [])
    p.setdefault("cover", "ember")
    units = []
    for i, raw in enumerate(p.get("units") or []):
        u = dict(raw)
        u.setdefault("id", f"unit-{i + 1}")
        u.setdefault("essence", "")
        u.setdefault("analogy", "")
        u.setdefault("example", "")
        u.setdefault("steps", [])
        u.setdefault("boundaries", [])
        u.setdefault("misconceptions", [])
        u.setdefault("faq", [])
        u.setdefault("tags", [])
        u.setdefault("key_points", [])
        u.setdefault("difficulty", 3)
        u.setdefault("importance", 3)
        u.setdefault("estimated_minutes", 45)
        u.setdefault("order_index", i)
        u.setdefault("relations", [])
        u.setdefault("probes", [])
        u.setdefault("cards", [])
        u.setdefault("source_ref", {})
        if not u["key_points"]:
            u["key_points"] = derive_key_points(u)
        probes = []
        for j, raw_probe in enumerate(u.get("probes") or []):
            pr = dict(raw_probe)
            pr.setdefault("id", f"{u['id']}-probe-{j + 1}")
            pr.setdefault("kind", "recall")
            pr.setdefault("answer", "")
            pr.setdefault("rubric", [])
            pr.setdefault("hints", [])
            probes.append(pr)
        u["probes"] = probes
        cards = []
        for j, raw_card in enumerate(u.get("cards") or []):
            c = dict(raw_card)
            c.setdefault("front", f"{u['title']}的核心是什么？")
            c.setdefault("back", u["essence"])
            c.setdefault("probe_id", probes[min(j, len(probes) - 1)]["id"] if probes else None)
            cards.append(c)
        if not cards and probes:
            cards.append({"front": f"{u['title']}的核心是什么？", "back": u["essence"],
                          "probe_id": probes[0]["id"]})
        u["cards"] = cards
        units.append(u)
    p["units"] = units
    return p


def derive_key_points(unit: dict[str, Any]) -> list[str]:
    from .utils import split_cn
    raw = []
    raw.append(unit.get("essence", ""))
    raw.extend(unit.get("steps") or [])
    raw.extend(unit.get("boundaries") or [])
    parts = []
    for r in raw:
        parts.extend(split_cn(str(r), limit=4))
    out = []
    seen = set()
    for p in parts:
        key = p.strip()
        if 2 <= len(key) <= 32 and key not in seen:
            seen.add(key)
            out.append(key)
        if len(out) >= 6:
            break
    return out


def _unit_id(goal_id: str, pack_id: str, pack_unit_id: str) -> str:
    base = slugify(f"{pack_id}-{pack_unit_id}", "unit")[:60]
    return f"u_{goal_id.split('_')[-1]}_{base}"


def install_pack(db: Database, user_id: str, goal_id: str, pack: dict[str, Any]) -> dict[str, Any]:
    pack = normalize_pack(pack)
    errors = validate_pack(pack)
    if errors:
        raise PackError("内容包校验失败", errors)
    pid = pack["id"]
    version = pack["version"]
    ts = now_iso()
    unit_id_map: dict[str, str] = {}
    stats = {"units": 0, "probes": 0, "cards": 0, "updated": 0, "created": 0}

    with db.transaction():
        db.upsert("packs", {
            "id": pid,
            "version": version,
            "kind": pack.get("kind", "subject"),
            "name": pack["name"],
            "description": pack.get("description", ""),
            "manifest_json": json.dumps(pack, ensure_ascii=False),
            "installed_at": ts,
        }, "id")
        db.upsert("goal_packs", {
            "goal_id": goal_id,
            "pack_id": pid,
            "version": version,
            "installed_at": ts,
        }, "goal_id,pack_id")

        for raw in pack["units"]:
            uid = _unit_id(goal_id, pid, raw["id"])
            unit_id_map[raw["id"]] = uid
            existing = db.one(
                "SELECT id FROM units WHERE goal_id=? AND pack_id=? AND pack_unit_id=?",
                (goal_id, pid, raw["id"]),
            )
            if existing:
                uid = existing["id"]
                unit_id_map[raw["id"]] = uid
                stats["updated"] += 1
            else:
                stats["created"] += 1
            db.upsert("units", {
                "id": uid,
                "goal_id": goal_id,
                "pack_id": pid,
                "pack_unit_id": raw["id"],
                "title": raw["title"],
                "essence": raw.get("essence", ""),
                "analogy": raw.get("analogy", ""),
                "example": raw.get("example", ""),
                "steps_json": json.dumps(raw.get("steps") or [], ensure_ascii=False),
                "boundaries_json": json.dumps(raw.get("boundaries") or [], ensure_ascii=False),
                "misconceptions_json": json.dumps(raw.get("misconceptions") or [], ensure_ascii=False),
                "faq_json": json.dumps(raw.get("faq") or [], ensure_ascii=False),
                "tags_json": json.dumps(raw.get("tags") or [], ensure_ascii=False),
                "key_points_json": json.dumps(raw.get("key_points") or [], ensure_ascii=False),
                "difficulty": int(raw.get("difficulty") or 3),
                "importance": int(raw.get("importance") or 3),
                "estimated_minutes": int(raw.get("estimated_minutes") or 45),
                "order_index": int(raw.get("order_index") or 0),
                "source_ref_json": json.dumps(raw.get("source_ref") or {}, ensure_ascii=False),
                "created_at": ts,
                "updated_at": ts,
            }, "goal_id,pack_id,pack_unit_id")
            for j, probe in enumerate(raw.get("probes") or []):
                probe_id = f"p_{uid}_{j + 1}"
                db.upsert("probes", {
                    "id": probe_id,
                    "unit_id": uid,
                    "kind": probe.get("kind", "recall"),
                    "prompt": probe["prompt"],
                    "answer": probe.get("answer", ""),
                    "rubric_json": json.dumps(probe.get("rubric") or [], ensure_ascii=False),
                    "hints_json": json.dumps(probe.get("hints") or [], ensure_ascii=False),
                    "created_at": ts,
                }, "id")
                stats["probes"] += 1
            for j, card in enumerate(raw.get("cards") or []):
                card_id = f"c_{uid}_{j + 1}"
                existing_card = db.one("SELECT id FROM cards WHERE id=?", (card_id,))
                if existing_card:
                    db.execute(
                        "UPDATE cards SET front=?, back=?, updated_at=? WHERE id=?",
                        (card["front"], card["back"], ts, card_id),
                    )
                else:
                    db.insert("cards", {
                        "id": card_id,
                        "user_id": user_id,
                        "unit_id": uid,
                        "probe_id": f"p_{uid}_1",
                        "front": card["front"],
                        "back": card["back"],
                        "state": "new",
                        "step": 0,
                        "stability": 0,
                        "difficulty": 0,
                        "due_at": ts,
                        "last_review": None,
                        "reps": 0,
                        "lapses": 0,
                        "created_at": ts,
                        "updated_at": ts,
                    })
                stats["cards"] += 1
            stats["units"] += 1

        # 关系：先清掉本 Pack 单元之间的关系，再重建
        if unit_id_map:
            placeholders = ",".join("?" for _ in unit_id_map.values())
            db.execute(
                f"DELETE FROM relations WHERE goal_id=? AND from_unit IN ({placeholders})",
                (goal_id, *unit_id_map.values()),
            )
            for raw in pack["units"]:
                from_uid = unit_id_map[raw["id"]]
                for rel in raw.get("relations") or []:
                    target_key = rel.get("to") if isinstance(rel, dict) else None
                    if target_key in unit_id_map:
                        db.insert("relations", {
                            "id": new_id("rel"),
                            "goal_id": goal_id,
                            "from_unit": from_uid,
                            "to_unit": unit_id_map[target_key],
                            "kind": rel.get("kind", "prerequisite"),
                        })
            # 按 order_index 隐式建立顺序前置（没有显式前置时）
            ordered = sorted(pack["units"], key=lambda x: int(x.get("order_index") or 0))
            for a, b in zip(ordered, ordered[1:]):
                from_uid, to_uid = unit_id_map[a["id"]], unit_id_map[b["id"]]
                has_incoming = db.scalar(
                    "SELECT COUNT(*) FROM relations WHERE goal_id=? AND to_unit=? AND kind='prerequisite'",
                    (goal_id, to_uid), 0,
                )
                if not has_incoming:
                    db.insert("relations", {
                        "id": new_id("rel"),
                        "goal_id": goal_id,
                        "from_unit": from_uid,
                        "to_unit": to_uid,
                        "kind": "prerequisite",
                    })

    emit(db, "pack.installed", {"pack_id": pid, "version": version, "goal_id": goal_id,
                                "stats": stats}, user_id)
    return {"pack_id": pid, "version": version, "goal_id": goal_id,
            "unit_id_map": unit_id_map, "stats": stats}


def unit_from_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("steps", "boundaries", "misconceptions", "faq", "tags", "key_points"):
        out[key] = json_loads(out.pop(f"{key}_json", "[]"), [])
    out["source_ref"] = json_loads(out.pop("source_ref_json", "{}"), {})
    return out


def get_goal_units(db: Database, goal_id: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM units WHERE goal_id=? ORDER BY order_index, id", (goal_id,))
    return [unit_from_row(r) for r in rows]


def get_goal_relations(db: Database, goal_id: str) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM relations WHERE goal_id=?", (goal_id,))


def installed_pack_ids(db: Database, goal_id: str) -> dict[str, str]:
    rows = db.query("SELECT pack_id, version FROM goal_packs WHERE goal_id=?", (goal_id,))
    return {r["pack_id"]: r["version"] for r in rows}


def find_pack(pack_id: str) -> dict[str, Any] | None:
    for p in all_available_packs():
        if p.get("id") == pack_id:
            return p
    row = None
    return row


def save_user_pack(pack: dict[str, Any]) -> Path:
    errors = validate_pack(pack)
    if errors:
        raise PackError("内容包校验失败", errors)
    config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PACKS_DIR / f"{slugify(pack['id'])}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pack_progress(db: Database, user_id: str, goal_id: str) -> dict[str, Any]:
    total = db.scalar("SELECT COUNT(*) FROM units WHERE goal_id=?", (goal_id,), 0) or 0
    mastered = db.scalar(
        "SELECT COUNT(*) FROM (SELECT unit_id FROM mastery WHERE user_id=? "
        "AND unit_id IN (SELECT id FROM units WHERE goal_id=?) GROUP BY unit_id "
        "HAVING MIN(score)>=0.8 AND SUM(reps)>=4)",
        (user_id, goal_id), 0,
    ) or 0
    return {"total": total, "mastered": mastered, "percent": round(safe_div(mastered, total) * 100, 1)}
