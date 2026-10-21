"""业务服务层：把 DB、算法、Pack、LLM 组合成 API 可调用的用例。"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import config, embeddings, heat, llm, mastery, planner, socratic
from .compiler import compile_material
from .db import Database, SCHEMA_VERSION, json_dumps, json_loads
from .events import emit, recent, touch_study_day
from .packs import (PackError, all_available_packs, get_goal_relations,
                    get_goal_units, install_pack, normalize_pack, pack_progress,
                    pack_quality, pack_summary, save_user_pack, unit_from_row,
                    validate_pack)
from .srs.fsrs import card_from_row, interval_label, scheduler
from .utils import (clamp, coverage, days_between, new_id, now, now_iso,
                    parse_iso, round4, safe_div, slugify, today_str, truncate)

DEFAULT_PACK = "boil.xueshen.learning"
STATUS_LABELS = {"new": "未开始", "learning": "学习中", "boiled": "已烧开", "rusting": "需复习"}


# ---------------------------------------------------------------------------
# 用户与目标
# ---------------------------------------------------------------------------
def ensure_user(db: Database, name: str = "") -> dict[str, Any]:
    user = db.one("SELECT * FROM users WHERE id=?", (config.DEFAULT_USER,))
    if user:
        if name and name != user["name"]:
            db.execute("UPDATE users SET name=?, updated_at=? WHERE id=?",
                       (name.strip()[:40], now_iso(), config.DEFAULT_USER))
            user = db.one("SELECT * FROM users WHERE id=?", (config.DEFAULT_USER,))
        return user
    ts = now_iso()
    db.insert("users", {
        "id": config.DEFAULT_USER,
        "name": (name or config.DEMO_USER_NAME).strip()[:40],
        "email": None,
        "created_at": ts,
        "updated_at": ts,
    })
    return db.one("SELECT * FROM users WHERE id=?", (config.DEFAULT_USER,))


def active_goal(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any] | None:
    if goal_id:
        row = db.one("SELECT * FROM goals WHERE id=? AND user_id=?", (goal_id, user_id))
        if row:
            row["meta"] = json_loads(row.pop("meta_json", "{}"), {})
            return row
    row = db.one("SELECT * FROM goals WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                 (user_id,))
    if row:
        row["meta"] = json_loads(row.pop("meta_json", "{}"), {})
    return row


def list_goals(db: Database, user_id: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM goals WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    for r in rows:
        r["meta"] = json_loads(r.pop("meta_json", "{}"), {})
        r["progress"] = pack_progress(db, user_id, r["id"])
    return rows


def latest_plan(db: Database, goal_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM plans WHERE goal_id=? ORDER BY version DESC LIMIT 1", (goal_id,))
    if not row:
        return None
    plan = json_loads(row["plan_json"], {}) or {}
    plan["id"] = row["id"]
    plan["version"] = row["version"]
    plan["created_at"] = row["created_at"]
    return plan


def save_plan(db: Database, goal_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    version = int(db.scalar("SELECT COALESCE(MAX(version),0) FROM plans WHERE goal_id=?", (goal_id,), 0) or 0) + 1
    pid = new_id("pl")
    plan["id"] = pid
    plan["version"] = version
    db.insert("plans", {
        "id": pid,
        "goal_id": goal_id,
        "version": version,
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "created_at": now_iso(),
    })
    emit(db, "plan.built", {"goal_id": goal_id, "plan_id": pid, "version": version,
                            "mode": (plan.get("strategy") or {}).get("mode")})
    return plan


def onboarding(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    user = ensure_user(db, str(payload.get("name") or ""))
    pack_id = (payload.get("pack_id") or "").strip()
    material = str(payload.get("material") or "").strip()
    material_title = str(payload.get("material_title") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    warnings: list[str] = []

    pack = None
    if material:
        pack, warnings = compile_material(material_title or "我的材料", subject, material,
                                          use_llm=bool(payload.get("use_llm", True)))
        save_user_pack(pack)
    elif pack_id:
        pack = next((p for p in all_available_packs() if p.get("id") == pack_id), None)
        if not pack:
            raise PackError("找不到内容包", [pack_id])
    else:
        pack = next((p for p in all_available_packs() if p.get("id") == DEFAULT_PACK), None)
        if not pack and all_available_packs():
            pack = all_available_packs()[0]
        if not pack:
            raise PackError("没有可用内容包", ["content/packs 为空"])

    goal_title = str(payload.get("goal_title") or pack.get("name") or "我的学习目标").strip()[:80]
    deadline_str = str(payload.get("deadline") or "").strip()
    deadline = None
    if deadline_str:
        try:
            deadline = date.fromisoformat(deadline_str)
        except ValueError:
            warnings.append("截止日期格式不正确，已按 8 周计划生成")
    daily_minutes = int(clamp(int(payload.get("daily_minutes") or 40), 10, 240))
    level = str(payload.get("level") or "beginner")[:20]

    # 归档旧目标
    db.execute("UPDATE goals SET status='archived', updated_at=? WHERE user_id=? AND status='active'",
               (now_iso(), user["id"]))
    goal_id = new_id("goal")
    ts = now_iso()
    ai_meta = {
        "ai_digest": pack.get("digest") or {},
        "ai_outline": pack.get("outline") or [],
        "ai_study_plan": pack.get("study_plan") or [],
        "ai_generated_by": pack.get("generated_by") or "rules",
    }
    db.insert("goals", {
        "id": goal_id,
        "user_id": user["id"],
        "title": goal_title,
        "subject": subject or pack.get("subject", ""),
        "target": str(payload.get("target") or "")[:200],
        "deadline": deadline.isoformat() if deadline else None,
        "daily_minutes": daily_minutes,
        "level": level,
        "status": "active",
        "pack_id": pack["id"],
        "meta_json": json_dumps({"warnings": warnings,
                                 "pack_name": pack.get("name"),
                                 "material": bool(material),
                                 **ai_meta}),
        "created_at": ts,
        "updated_at": ts,
    })
    install_result = install_pack(db, user["id"], goal_id, pack)
    units = get_goal_units(db, goal_id)
    relations = get_goal_relations(db, goal_id)
    plan = planner.build_plan(units, relations, daily_minutes=daily_minutes,
                              start=_local_today(), deadline=deadline, goal_id=goal_id,
                              version=1, ai_outline=ai_meta["ai_outline"])
    plan = save_plan(db, goal_id, plan)
    emit(db, "goal.created", {"goal_id": goal_id, "title": goal_title,
                              "pack_id": pack["id"], "units": len(units)}, user["id"])
    result = dashboard(db, user["id"], goal_id)
    result["warnings"] = warnings
    result["install"] = install_result
    return result


def _local_today() -> date:
    return datetime.now().astimezone().date()


def goals_overview(db: Database, user_id: str) -> dict[str, Any]:
    user = ensure_user(db)
    goals = list_goals(db, user_id)
    return {"user": _public_user(user), "goals": goals,
            "active_goal": active_goal(db, user_id)}


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "name": user["name"], "created_at": user["created_at"]}


# ---------------------------------------------------------------------------
# 仪表盘 / 地图 / 单元
# ---------------------------------------------------------------------------
def _due_count_map(db: Database, user_id: str, goal_id: str) -> dict[str, int]:
    rows = db.query(
        "SELECT u.id AS unit_id, COUNT(c.id) AS n FROM cards c "
        "JOIN units u ON u.id=c.unit_id "
        "WHERE c.user_id=? AND u.goal_id=? AND c.due_at<=? AND c.state!='new' "
        "GROUP BY u.id",
        (user_id, goal_id, now_iso()),
    )
    return {r["unit_id"]: int(r["n"]) for r in rows}


def _unit_status_map(db: Database, user_id: str, units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = [u["id"] for u in units]
    bulk = mastery.get_mastery_bulk(db, user_id, ids)
    due = _due_count_map(db, user_id, units[0]["goal_id"]) if units else {}
    out = {}
    for u in units:
        m = bulk.get(u["id"], {})
        status = mastery.status_for(m, due.get(u["id"], 0), mastery.days_since((m.get("facets") or {}).get("recall", {}).get("last_evidence_at")))
        h = heat.compute_heat(m, due.get(u["id"], 0))
        out[u["id"]] = {**m, "status": status, "due_count": due.get(u["id"], 0),
                        "status_label": STATUS_LABELS.get(status, status),
                        "heat": h, "heat_label": heat.heat_label(h)}
    return out


def dashboard(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    user = ensure_user(db)
    goal = active_goal(db, user_id, goal_id)
    if not goal:
        return {"user": _public_user(user), "goal": None, "has_goal": False,
                "llm": llm.public_config()}
    units = get_goal_units(db, goal["id"])
    relations = get_goal_relations(db, goal["id"])
    plan = latest_plan(db, goal["id"]) or {}
    statuses = _unit_status_map(db, user_id, units)
    due_total = sum(s["due_count"] for s in statuses.values())
    msummary = mastery.summary(db, user_id, goal["id"])
    today = planner.today_plan(plan, today_str())
    done_ids = {uid for uid, s in statuses.items() if s["status"] == "boiled"}
    progress = planner.progress(plan, done_ids)
    stats = _study_stats(db, user_id)
    next_units = _next_units(units, statuses, limit=4)
    next_action = _next_action(due_total, next_units, statuses)
    plan_days = plan.get("days") or []
    today_day = next((d for d in plan_days if d.get("date") == today_str()), today)
    return {
        "user": _public_user(user),
        "goal": goal,
        "has_goal": True,
        "plan": {
            "id": plan.get("id"),
            "version": plan.get("version"),
            "strategy": plan.get("strategy", {}),
            "today": today_day,
            "milestones": plan.get("milestones", []),
            "outline": plan.get("outline", []),
            "progress": progress,
            "days": plan_days[:14],
        },
        "stats": stats,
        "mastery": msummary,
        "statuses": statuses,
        "counts": {
            "units": len(units),
            "boiled": sum(1 for s in statuses.values() if s["status"] == "boiled"),
            "learning": sum(1 for s in statuses.values() if s["status"] == "learning"),
            "new": sum(1 for s in statuses.values() if s["status"] == "new"),
            "rusting": sum(1 for s in statuses.values() if s["status"] == "rusting"),
            "due": due_total,
            "average_heat": round4(sum(s["heat"] for s in statuses.values()) / len(statuses)) if statuses else 0.0,
        },
        "next_units": next_units,
        "next_action": next_action,
        "digest": (goal.get("meta") or {}).get("ai_digest") or {},
        "study_plan_phases": (goal.get("meta") or {}).get("ai_study_plan") or [],
        "generated_by": (goal.get("meta") or {}).get("ai_generated_by") or "rules",
        "activity": recent(db, user_id, 12),
        "map_preview": {
            "units": [{"id": u["id"], "title": u["title"], "status": statuses[u["id"]]["status"]}
                      for u in units[:8]],
            "relations": [r for r in relations if r["from_unit"] in statuses and r["to_unit"] in statuses][:12],
        },
        "llm": llm.public_config(),
    }


def _study_stats(db: Database, user_id: str) -> dict[str, Any]:
    rows = db.query("SELECT day, minutes, sessions, reviews FROM study_days WHERE user_id=? "
                    "ORDER BY day DESC LIMIT 60", (user_id,))
    today = _local_today()
    today_row = next((r for r in rows if r["day"] == today.isoformat()), None)
    week_start = (today - timedelta(days=6)).isoformat()
    week = [r for r in rows if r["day"] >= week_start]
    streak = 0
    cursor = today
    days = {r["day"]: r for r in rows}
    if cursor.isoformat() not in days:
        cursor -= timedelta(days=1)
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    reviews_today = db.scalar(
        "SELECT COUNT(*) FROM review_logs WHERE user_id=? AND reviewed_at>=?",
        (user_id, _day_start_iso(today)), 0,
    ) or 0
    reviews = db.query("SELECT rating, reviewed_at FROM review_logs WHERE user_id=? "
                       "AND reviewed_at>=? ORDER BY reviewed_at DESC LIMIT 500",
                       (user_id, (today - timedelta(days=30)).isoformat()))
    success = sum(1 for r in reviews if int(r["rating"]) >= 3)
    return {
        "today_minutes": int(today_row["minutes"]) if today_row else 0,
        "week_minutes": sum(int(r["minutes"]) for r in week),
        "week_sessions": sum(int(r["sessions"]) for r in week),
        "streak": streak,
        "reviews_today": int(reviews_today),
        "reviews_30d": len(reviews),
        "retention_30d": round4(safe_div(success, len(reviews))),
    }


def _day_start_iso(d: date) -> str:
    dt = datetime(d.year, d.month, d.day, tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _next_units(units: list[dict[str, Any]], statuses: dict[str, dict[str, Any]],
                limit: int = 4) -> list[dict[str, Any]]:
    out = []
    for u in sorted(units, key=lambda x: (x.get("order_index") or 0, x["id"])):
        st = statuses.get(u["id"], {})
        if st.get("status") in ("boiled",):
            continue
        out.append(_unit_card(u, st))
        if len(out) >= limit:
            break
    return out


def _unit_card(u: dict[str, Any], st: dict[str, Any] | None = None) -> dict[str, Any]:
    st = st or {}
    return {
        "id": u["id"], "title": u["title"], "essence": truncate(u.get("essence"), 140),
        "tags": u.get("tags") or [], "difficulty": u.get("difficulty"),
        "importance": u.get("importance"), "estimated_minutes": u.get("estimated_minutes"),
        "status": st.get("status", "new"),
        "status_label": st.get("status_label", STATUS_LABELS.get(st.get("status", "new"), "未开始")),
        "mastery": st.get("average", 0.0), "due_count": st.get("due_count", 0),
        "heat": st.get("heat", 0.0), "heat_label": st.get("heat_label", "未点火"),
    }


def _next_action(due_total: int, next_units: list[dict[str, Any]],
                 statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if due_total > 0:
        return {"kind": "review", "title": f"先还 {due_total} 张复习卡", "subtitle": "对抗遗忘优先于学新内容",
                "href": "#/review", "cta": "开始复习"}
    if next_units:
        u = next_units[0]
        return {"kind": "learn", "title": f"继续「{u['title']}」", "subtitle": "预消化 → 苏格拉底 → 费曼",
                "href": f"#/unit/{u['id']}", "cta": "开始闭环", "unit_id": u["id"]}
    weakest = None
    for uid, st in statuses.items():
        if st.get("status") in ("learning", "rusting") and (weakest is None or st.get("average", 0) < statuses[weakest].get("average", 0)):
            weakest = uid
    if weakest:
        return {"kind": "feynman", "title": "用费曼法补最后一个洞", "subtitle": "讲给一个外行听，暴露剩余缺口",
                "href": f"#/feynman/{weakest}", "cta": "开始讲解", "unit_id": weakest}
    return {"kind": "done", "title": "这个目标已经全部烧开", "subtitle": "去洞察页看看掌握雷达，或开启一个新目标",
            "href": "#/insights", "cta": "查看洞察"}


def update_goal_and_replan(db: Database, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    goal = active_goal(db, user_id, payload.get("goal_id"))
    if not goal:
        raise ValueError("还没有学习目标")
    title = str(payload.get("title") or goal["title"]).strip()[:80]
    daily = int(clamp(int(payload.get("daily_minutes") or goal["daily_minutes"]), 10, 240))
    deadline = goal.get("deadline")
    if "deadline" in payload:
        deadline = str(payload.get("deadline") or "").strip() or None
        if deadline:
            try:
                date.fromisoformat(deadline)
            except ValueError as exc:
                raise ValueError("截止日期格式应为 YYYY-MM-DD") from exc
    db.execute(
        "UPDATE goals SET title=?, daily_minutes=?, deadline=?, updated_at=? WHERE id=?",
        (title, daily, deadline, now_iso(), goal["id"]),
    )
    units = get_goal_units(db, goal["id"])
    bulk = mastery.get_mastery_bulk(db, user_id, [u["id"] for u in units])
    remaining = []
    for u in units:
        m = bulk.get(u["id"], {})
        if not (m.get("status") == "boiled" and (m.get("reps") or 0) >= 4):
            remaining.append(u)
    remaining_ids = {u["id"] for u in remaining}
    relations = [r for r in get_goal_relations(db, goal["id"])
                 if r["from_unit"] in remaining_ids and r["to_unit"] in remaining_ids]
    deadline_date = None
    if deadline:
        try:
            deadline_date = date.fromisoformat(deadline)
        except ValueError:
            deadline_date = None
    goal_meta = json_loads(goal.get("meta_json") or "{}")
    plan = planner.build_plan(remaining, relations, daily_minutes=daily,
                              start=_local_today(), deadline=deadline_date,
                              goal_id=goal["id"], version=1,
                              ai_outline=goal_meta.get("ai_outline") or None)
    plan = save_plan(db, goal["id"], plan)
    emit(db, "goal.updated", {"goal_id": goal["id"], "daily_minutes": daily,
                              "deadline": deadline, "remaining": len(remaining)}, user_id)
    return dashboard(db, user_id, goal["id"])


def map_data(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    goal = active_goal(db, user_id, goal_id)
    if not goal:
        raise ValueError("还没有学习目标")
    units = get_goal_units(db, goal["id"])
    relations = get_goal_relations(db, goal["id"])
    statuses = _unit_status_map(db, user_id, units)
    nodes = []
    for i, u in enumerate(units):
        st = statuses[u["id"]]
        nodes.append({**_unit_card(u, st), "order_index": i,
                      "facets": st.get("facets") or {},
                      "boundaries": u.get("boundaries") or [],
                      "key_points": u.get("key_points") or []})
    edges = [{"id": r["id"], "source": r["from_unit"], "target": r["to_unit"],
              "kind": r["kind"]} for r in relations
             if r["from_unit"] in statuses and r["to_unit"] in statuses]
    return {"goal": goal, "nodes": nodes, "edges": edges,
            "summary": mastery.summary(db, user_id, goal["id"])}


def get_unit_notes(db: Database, user_id: str, unit_id: str) -> dict[str, Any]:
    rows = db.query(
        "SELECT kind, content, updated_at FROM unit_notes WHERE user_id=? AND unit_id=?",
        (user_id, unit_id),
    )
    return {r["kind"]: {"content": r["content"], "updated_at": r["updated_at"]} for r in rows}


def save_unit_note(db: Database, user_id: str, unit_id: str, kind: str,
                   content: str) -> dict[str, Any]:
    if kind not in ("note", "mnemonic"):
        raise ValueError("kind 必须是 note 或 mnemonic")
    content = str(content or "").strip()
    if len(content) > 4000:
        raise ValueError("笔记最多 4000 字")
    ts = now_iso()
    existing = db.one(
        "SELECT id, created_at FROM unit_notes WHERE user_id=? AND unit_id=? AND kind=?",
        (user_id, unit_id, kind),
    )
    if not content:
        db.execute("DELETE FROM unit_notes WHERE user_id=? AND unit_id=? AND kind=?",
                   (user_id, unit_id, kind))
    elif existing:
        db.execute(
            "UPDATE unit_notes SET content=?, updated_at=? WHERE id=?",
            (content, ts, existing["id"]),
        )
    else:
        db.insert("unit_notes", {
            "id": new_id("un"), "user_id": user_id, "unit_id": unit_id,
            "kind": kind, "content": content, "created_at": ts, "updated_at": ts,
        })
    emit(db, "note.saved", {"unit_id": unit_id, "kind": kind, "empty": not bool(content)}, user_id)
    return get_unit_notes(db, user_id, unit_id)


# ---------------------------------------------------------------------------
# 灵感笔记（划词保存，跨页面）
# ---------------------------------------------------------------------------
NOTE_SOURCES = ("unit", "socratic", "feynman", "review", "insights", "dashboard",
                "map", "library", "notes")


def list_notes(db: Database, user_id: str, unit_id: str | None = None,
               q: str | None = None, limit: int = 200) -> dict[str, Any]:
    limit = int(clamp(int(limit or 200), 1, 500))
    sql = "SELECT n.*, u.title AS unit_title FROM notes n " \
          "LEFT JOIN units u ON u.id = n.unit_id WHERE n.user_id=?"
    params: list[Any] = [user_id]
    if unit_id:
        sql += " AND n.unit_id=?"
        params.append(unit_id)
    keyword = (q or "").strip()
    if keyword:
        sql += " AND (n.quote LIKE ? OR n.content LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    sql += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    for r in rows:
        r["chars"] = len(r.get("content") or "") + len(r.get("quote") or "")
    goal = active_goal(db, user_id)
    return {"notes": rows, "total": db.scalar(
        "SELECT COUNT(*) FROM notes WHERE user_id=?", (user_id,), 0) or 0,
        "goal_id": goal["id"] if goal else None}


def create_note(db: Database, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    quote = re.sub(r"\r\n?", "\n", str(payload.get("quote") or "")).strip()[:2000]
    content = re.sub(r"\r\n?", "\n", str(payload.get("content") or "")).strip()[:4000]
    if not quote and not content:
        raise ValueError("引用文字和灵感内容至少要有一个")
    unit_id = str(payload.get("unit_id") or "").strip()[:80] or None
    if unit_id and not db.one("SELECT id FROM units WHERE id=?", (unit_id,)):
        unit_id = None
    goal = active_goal(db, user_id)
    source = str(payload.get("source") or "").strip()[:40]
    if source not in NOTE_SOURCES:
        source = ""
    ts = now_iso()
    note_id = new_id("no")
    db.insert("notes", {
        "id": note_id, "user_id": user_id,
        "goal_id": str(payload.get("goal_id") or "").strip()[:80] or (goal["id"] if goal else None),
        "unit_id": unit_id, "kind": "inspiration",
        "quote": quote, "content": content, "source": source,
        "created_at": ts, "updated_at": ts,
    })
    emit(db, "note.created", {"note_id": note_id, "unit_id": unit_id, "source": source,
                              "chars": len(quote) + len(content)}, user_id)
    row = db.one("SELECT n.*, u.title AS unit_title FROM notes n "
                 "LEFT JOIN units u ON u.id = n.unit_id WHERE n.id=? AND n.user_id=?",
                 (note_id, user_id))
    return {"note": row, "id": note_id}


def delete_note(db: Database, user_id: str, note_id: str) -> dict[str, Any]:
    cur = db.execute("DELETE FROM notes WHERE id=? AND user_id=?", (note_id, user_id))
    if cur.rowcount == 0:
        raise ValueError("笔记不存在")
    emit(db, "note.deleted", {"note_id": note_id}, user_id)
    return {"deleted": note_id}


def export_notes_markdown(db: Database, user_id: str) -> str:
    rows = db.query(
        "SELECT n.*, u.title AS unit_title FROM notes n "
        "LEFT JOIN units u ON u.id = n.unit_id "
        "WHERE n.user_id=? ORDER BY n.created_at DESC", (user_id,))
    lines = ["# Boil 灵感库", ""]
    for r in rows:
        if r.get("quote"):
            lines.append(f"> {r['quote']}")
        if r.get("content"):
            lines.append(f"\n{r['content']}")
        meta = [x for x in (r.get("unit_title"), r.get("source"), (r.get("created_at") or "")[:10]) if x]
        if meta:
            lines.append(f"\n— {' · '.join(meta)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def unit_detail(db: Database, user_id: str, unit_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM units WHERE id=?", (unit_id,))
    if not row:
        raise ValueError("单元不存在")
    unit = unit_from_row(row)
    st = _unit_status_map(db, user_id, [unit])
    m = mastery.get_mastery(db, user_id, unit_id)
    probes = []
    for p in db.query("SELECT * FROM probes WHERE unit_id=? ORDER BY created_at, id", (unit_id,)):
        d = dict(p)
        d["rubric"] = json_loads(d.pop("rubric_json", "[]"), [])
        d["hints"] = json_loads(d.pop("hints_json", "[]"), [])
        d["attempts"] = db.scalar("SELECT COUNT(*) FROM attempts WHERE probe_id=? AND user_id=?",
                                  (d["id"], user_id), 0) or 0
        probes.append(d)
    cards = []
    for c in db.query("SELECT * FROM cards WHERE unit_id=? AND user_id=? ORDER BY id", (unit_id, user_id)):
        d = dict(c)
        d["preview"] = scheduler.preview_labels(card_from_row(d))
        d["due_label"] = _due_label(d)
        cards.append(d)
    sessions = db.query(
        "SELECT * FROM sessions WHERE unit_id=? AND user_id=? ORDER BY started_at DESC LIMIT 5",
        (unit_id, user_id),
    )
    for s in sessions:
        meta = json_loads(s.pop("meta_json", "{}"), {})
        s["report"] = meta.get("report")
        s["engine"] = meta.get("engine")
    relations = db.query(
        "SELECT r.kind, r.from_unit, r.to_unit, "
        "uf.title AS from_title, ut.title AS to_title FROM relations r "
        "LEFT JOIN units uf ON uf.id=r.from_unit LEFT JOIN units ut ON ut.id=r.to_unit "
        "WHERE r.goal_id=? AND (r.from_unit=? OR r.to_unit=?)",
        (unit["goal_id"], unit_id, unit_id),
    )
    attempts = db.query(
        "SELECT * FROM attempts WHERE unit_id=? AND user_id=? ORDER BY created_at DESC LIMIT 8",
        (unit_id, user_id),
    )
    h = heat.heat_for_unit(db, user_id, unit_id)
    pn = heat.pn_state(db, user_id, unit)
    records = heat.history(db, user_id, unit_id=unit_id, limit=6)
    all_units = get_goal_units(db, unit["goal_id"])
    related = embeddings.related_units(unit, all_units)
    return {"unit": unit, "mastery": m, "status": st[unit_id],
            "heat": h, "heat_label": heat.heat_label(h), "pn": pn,
            "records": records, "notes": get_unit_notes(db, user_id, unit_id),
            "related": related,
            "probes": probes, "cards": cards, "sessions": sessions,
            "relations": relations, "attempts": attempts,
            "source": unit.get("source_ref") or {}}


def _due_label(card: dict[str, Any]) -> str:
    if card.get("state") == "new":
        return "新卡"
    due = parse_iso(card.get("due_at"))
    delta = (due - now()).total_seconds()
    if delta <= 0:
        return "已到期"
    return interval_label(delta) + "后"


# ---------------------------------------------------------------------------
# 复习
# ---------------------------------------------------------------------------
def review_queue(db: Database, user_id: str, limit: int = 30,
                 unit_id: str | None = None) -> dict[str, Any]:
    limit = int(clamp(limit, 1, 100))
    now_s = now_iso()
    clause = " AND c.unit_id=?" if unit_id else ""
    params: list[Any] = [user_id, now_s]
    if unit_id:
        params.append(unit_id)
    rows = db.query(
        "SELECT c.*, u.title AS unit_title, u.essence AS unit_essence "
        "FROM cards c JOIN units u ON u.id=c.unit_id "
        f"WHERE c.user_id=? AND c.due_at<=?{clause} "
        "ORDER BY CASE WHEN c.state='new' THEN 1 ELSE 0 END, c.due_at, c.created_at LIMIT ?",
        (*params, limit),
    )
    cards = []
    unit_ids = list({r["unit_id"] for r in rows})
    mnemonics = {}
    if unit_ids:
        ph = ",".join("?" for _ in unit_ids)
        for n in db.query(
            f"SELECT unit_id, content FROM unit_notes WHERE user_id=? AND kind='mnemonic' "
            f"AND unit_id IN ({ph})", (user_id, *unit_ids),
        ):
            mnemonics[n["unit_id"]] = n["content"]
    for r in rows:
        d = dict(r)
        d["preview"] = scheduler.preview_labels(card_from_row(d))
        d["due_label"] = _due_label(d)
        d["retention"] = round4(scheduler.retrievability(float(d.get("stability") or 0),
                                                         _elapsed_days(d)))
        d["mnemonic"] = mnemonics.get(d["unit_id"], "")
        cards.append(d)
    due_total = db.scalar(
        "SELECT COUNT(*) FROM cards c JOIN units u ON u.id=c.unit_id "
        "WHERE c.user_id=? AND c.due_at<=? AND c.state!='new'" + (" AND c.unit_id=?" if unit_id else ""),
        (user_id, now_s, *([unit_id] if unit_id else [])), 0,
    ) or 0
    new_total = db.scalar(
        "SELECT COUNT(*) FROM cards c JOIN units u ON u.id=c.unit_id "
        "WHERE c.user_id=? AND c.state='new'" + (" AND c.unit_id=?" if unit_id else ""),
        (user_id, *([unit_id] if unit_id else [])), 0,
    ) or 0
    return {"cards": cards, "due_total": int(due_total), "new_total": int(new_total),
            "stats": _study_stats(db, user_id)}


def _elapsed_days(card: dict[str, Any]) -> float:
    last = card.get("last_review")
    if not last:
        return 0.0
    return max(0.0, (now() - parse_iso(last)).total_seconds() / 86400.0)


def rate_card(db: Database, user_id: str, card_id: str, rating: int,
              elapsed_ms: int = 0) -> dict[str, Any]:
    card = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id))
    if not card:
        raise ValueError("卡片不存在")
    rating = int(clamp(int(rating), 1, 4))
    before_heat = heat.heat_for_unit(db, user_id, card["unit_id"])
    before = dict(card)
    result = scheduler.schedule(card_from_row(card), rating)
    ts = now_iso()
    db.execute(
        "UPDATE cards SET state=?, step=?, stability=?, difficulty=?, due_at=?, "
        "last_review=?, reps=?, lapses=?, updated_at=? WHERE id=?",
        (result.state, result.step, result.stability, result.difficulty,
         result.due_iso(), ts, result.reps, result.lapses, ts, card_id),
    )
    db.insert("review_logs", {
        "id": new_id("rv"),
        "card_id": card_id,
        "user_id": user_id,
        "rating": rating,
        "reviewed_at": ts,
        "elapsed_days": round4(result.elapsed_days),
        "scheduled_days": round4(result.interval_days),
        "stability_before": round4(float(before.get("stability") or 0)),
        "stability_after": round4(result.stability),
        "difficulty_before": round4(float(before.get("difficulty") or 0)),
        "difficulty_after": round4(result.difficulty),
        "retention_before": round4(result.retention_before),
        "state_before": before.get("state") or "new",
        "state_after": result.state,
        "source": "web",
    })
    score = {1: 0.1, 2: 0.45, 3: 0.8, 4: 0.95}[rating]
    add = mastery.add_evidence(db, user_id, card["unit_id"], "recall", score,
                               confidence=0.8, weight=0.9, source="review")
    after_heat = heat.heat_for_unit(db, user_id, card["unit_id"])
    heat.record_boil(db, user_id, card["unit_id"], "recall", 3,
                     {"rating": rating, "interval_days": round4(result.interval_days)},
                     before_heat, after_heat, [])
    touch_study_day(db, user_id, minutes=max(1, int(elapsed_ms / 60000)) if elapsed_ms else 1,
                    sessions=0, reviews=1)
    emit(db, "review.rated", {"card_id": card_id, "unit_id": card["unit_id"],
                              "rating": rating, "interval_days": round4(result.interval_days)},
         user_id)
    updated = db.one("SELECT * FROM cards WHERE id=?", (card_id,))
    return {
        "card": {**_public_card(updated), "preview": scheduler.preview_labels(card_from_row(updated))},
        "result": {
            "state": result.state,
            "due_at": result.due_iso(),
            "interval_label": interval_label(result.interval_seconds),
            "interval_days": round4(result.interval_days),
            "retention_before": round4(result.retention_before),
        },
        "mastery": add,
        "remaining": review_queue(db, user_id, limit=1)["due_total"],
    }


def _public_card(card: dict[str, Any]) -> dict[str, Any]:
    return {k: card.get(k) for k in ("id", "unit_id", "front", "back", "state", "due_at",
                                     "reps", "lapses", "stability", "difficulty")}


# ---------------------------------------------------------------------------
# 洞察
# ---------------------------------------------------------------------------
def insights(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    goal = active_goal(db, user_id, goal_id)
    if not goal:
        raise ValueError("还没有学习目标")
    units = get_goal_units(db, goal["id"])
    statuses = _unit_status_map(db, user_id, units)
    msummary = mastery.summary(db, user_id, goal["id"])
    plan = latest_plan(db, goal["id"]) or {}
    done_ids = {uid for uid, s in statuses.items() if s["status"] == "boiled"}
    progress = planner.progress(plan, done_ids)

    # 保留率趋势（按天聚合）
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    logs = db.query("SELECT rating, reviewed_at, retention_before FROM review_logs "
                    "WHERE user_id=? AND reviewed_at>=? ORDER BY reviewed_at", (user_id, since))
    by_day: dict[str, list[int]] = {}
    for r in logs:
        day = r["reviewed_at"][:10]
        by_day.setdefault(day, []).append(1 if int(r["rating"]) >= 3 else 0)
    retention = [{"date": d, "total": len(v), "success": sum(v),
                  "rate": round4(safe_div(sum(v), len(v)))} for d, v in sorted(by_day.items())]

    # 未来 7 天到期
    today = _local_today()
    forecast = []
    for i in range(7):
        d = today + timedelta(days=i)
        start = _day_start_iso(d)
        end = _day_start_iso(d + timedelta(days=1))
        n = db.scalar("SELECT COUNT(*) FROM cards WHERE user_id=? AND due_at>=? AND due_at<? AND state!='new'",
                      (user_id, start, end), 0) or 0
        forecast.append({"date": d.isoformat(), "weekday": "一二三四五六日"[d.weekday()], "due": int(n)})

    # 56 天热力图
    heat_rows = db.query("SELECT day, minutes, sessions, reviews FROM study_days "
                         "WHERE user_id=? ORDER BY day DESC LIMIT 56", (user_id,))
    heat_by_day = {r["day"]: r for r in heat_rows}
    heatmap = []
    for i in range(55, -1, -1):
        d = today - timedelta(days=i)
        row = heat_by_day.get(d.isoformat(), {})
        heatmap.append({"date": d.isoformat(), "minutes": int(row.get("minutes", 0)),
                        "sessions": int(row.get("sessions", 0)),
                        "reviews": int(row.get("reviews", 0))})

    # 最近报告缺口
    reports = db.query("SELECT unit_id, meta_json, started_at FROM sessions "
                       "WHERE user_id=? AND kind='socratic' AND meta_json LIKE '%\"report\"%' "
                       "ORDER BY started_at DESC LIMIT 12", (user_id,))
    gaps = []
    for r in reports:
        meta = json_loads(r["meta_json"], {})
        rep = meta.get("report") or {}
        for g in (rep.get("gaps") or [])[:2]:
            gaps.append({"unit_id": r["unit_id"], "gap": g, "at": r["started_at"]})
    seen = set()
    unique_gaps = []
    for g in gaps:
        key = (g["unit_id"], g["gap"])
        if key not in seen:
            seen.add(key)
            unique_gaps.append(g)
        if len(unique_gaps) >= 5:
            break

    next_action = _next_action(sum(s["due_count"] for s in statuses.values()),
                               _next_units(units, statuses, 2), statuses)
    return {
        "goal": goal,
        "mastery": msummary,
        "statuses": statuses,
        "counts": {
            "units": len(units),
            "boiled": sum(1 for s in statuses.values() if s["status"] == "boiled"),
            "learning": sum(1 for s in statuses.values() if s["status"] == "learning"),
            "new": sum(1 for s in statuses.values() if s["status"] == "new"),
            "rusting": sum(1 for s in statuses.values() if s["status"] == "rusting"),
        },
        "plan_progress": progress,
        "average_heat": round4(sum(s["heat"] for s in statuses.values()) / len(statuses)) if statuses else 0.0,
        "records": heat.history(db, user_id, limit=10),
        "retention": retention,
        "forecast": forecast,
        "heatmap": heatmap,
        "stats": _study_stats(db, user_id),
        "gaps": unique_gaps,
        "next_action": next_action,
        "recent_sessions": db.query(
            "SELECT id, kind, status, stage, turn, started_at, ended_at, unit_id "
            "FROM sessions WHERE user_id=? ORDER BY started_at DESC LIMIT 8", (user_id,)),
    }


def insights_synthesize(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    """把材料总览与全部学习数据一起交给模型，生成一段本周解读。"""
    data = insights(db, user_id, goal_id)
    goal = data.get("goal") or {}
    digest = (goal.get("meta") or {}).get("ai_digest") or {}
    statuses = data.get("statuses") or {}
    units = {u["id"]: u for u in get_goal_units(db, goal["id"])} if goal else {}
    ranked = sorted(statuses.items(), key=lambda kv: float(kv[1].get("average") or 0.0))
    weak = [
        f"{units.get(uid, {}).get('title') or uid}"
        f"（掌握度 {float(st.get('average') or 0):.2f}，热度 {round4(float(st.get('heat') or 0))}）"
        for uid, st in ranked[:3]
    ]
    counts = data.get("counts") or {}
    stats_blob = {
        "单元总数": counts.get("units", 0),
        "已烧开": counts.get("boiled", 0),
        "学习中": counts.get("learning", 0),
        "正在生锈": counts.get("rusting", 0),
        "平均热度": data.get("average_heat", 0),
        "计划完成度百分比": (data.get("plan_progress") or {}).get("percent"),
        "近30天保留率": (data.get("stats") or {}).get("retention_30d"),
        "最近待补的缺口": [g.get("gap") for g in (data.get("gaps") or [])][:5],
    }
    warnings: list[str] = []
    if llm.enabled():
        try:
            prompt = (llm.load_prompt("insights_synth.md")
                      .replace("{overview}", digest.get("overview") or "（还没有材料总览）")
                      .replace("{stats}", json_dumps(stats_blob))
                      .replace("{weak_units}", "、".join(weak) or "（还没有学习记录）"))
            res = llm.chat_json([
                {"role": "system", "content": "你是严谨的学习教练。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.3, max_tokens=900)
            if isinstance(res, dict) and str(res.get("summary") or "").strip():
                return {
                    "summary": _meta_field(res.get("summary"), 240),
                    "suggestions": [_meta_field(s, 80) for s in (res.get("suggestions") or [])
                                    if _meta_field(s, 80)][:5],
                    "risks": [_meta_field(r, 80) for r in (res.get("risks") or [])
                              if _meta_field(r, 80)][:4],
                    "based_on": {"digest": bool(digest.get("overview")), "units": len(statuses)},
                    "source": "model",
                    "warnings": warnings,
                }
            warnings.append("模型没有给出可用解读，已切换到本地规则。")
        except Exception as exc:
            warnings.append(f"AI 解读失败，已切换到本地规则：{str(exc)[:160]}")
    weakest = units.get(ranked[0][0], {}).get("title") if ranked else None
    avg_heat = float(data.get("average_heat") or 0)
    summary = (
        f"共 {counts.get('units', 0)} 个单元，已烧开 {counts.get('boiled', 0)} 个，"
        f"{counts.get('rusting', 0)} 个正在生锈，平均热度 {round4(avg_heat)}。"
    )
    if weakest:
        summary += f" 当前最薄弱的是「{weakest}」。"
    return {
        "summary": summary,
        "suggestions": [
            "今天先清掉到期卡片，再学新单元",
            f"给{weakest or'最弱单元'}做一次费曼讲解" if weakest else "挑一个单元做一次费曼讲解",
            "答不出的探针第二天优先重测",
        ][:3],
        "risks": [
            f"{counts.get('rusting', 0)} 个单元已生锈，拖越久重学成本越高",
        ] if counts.get("rusting") else ["暂无明显风险，保持每日提取练习"],
        "based_on": {"digest": bool(digest.get("overview")), "units": len(statuses)},
        "source": "rules",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 内容包与编译器
# ---------------------------------------------------------------------------
def pack_catalog(db: Database, user_id: str) -> dict[str, Any]:
    goal = active_goal(db, user_id)
    installed = {}
    if goal:
        for r in db.query("SELECT pack_id, version FROM goal_packs WHERE goal_id=?", (goal["id"],)):
            installed[r["pack_id"]] = r["version"]
    packs = [pack_summary(p, installed=p.get("id") in installed, goal_id=goal["id"] if goal else None)
             for p in all_available_packs()]
    packs.sort(key=lambda p: (not p["installed"], p["name"]))
    return {"packs": packs, "installed": installed,
            "goal": goal, "default_pack": DEFAULT_PACK}


def pack_detail(pack_id: str) -> dict[str, Any]:
    pack = next((p for p in all_available_packs() if p.get("id") == pack_id), None)
    if not pack:
        raise ValueError("找不到内容包")
    normalized = normalize_pack(pack)
    normalized["quality"] = pack_quality(normalized)
    return normalized


def import_pack(payload: dict[str, Any]) -> dict[str, Any]:
    pack = payload.get("pack") if isinstance(payload, dict) else None
    if not isinstance(pack, dict):
        raise ValueError("缺少 pack 对象")
    errors = validate_pack(pack)
    if errors:
        raise PackError("内容包校验失败", errors)
    pack = normalize_pack(pack, source="imported")
    save_user_pack(pack)
    return {"pack": pack, "summary": pack_summary(pack)}


MAX_MATERIAL_CHARS = 200_000


def merge_material_documents(documents: list[Any] | None, text: str) -> tuple[str, list[dict[str, Any]]]:
    docs: list[dict[str, Any]] = []
    for i, raw in enumerate((documents or [])[:20]):
        if not isinstance(raw, dict):
            continue
        name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:100]
        content = str(raw.get("text") or "").replace("\r\n", "\n").strip()
        if not content:
            continue
        docs.append({"name": name or f"材料 {i + 1}", "text": content, "chars": len(content)})
    parts = []
    for doc in docs:
        if len(docs) > 1:
            parts.append(f"## {doc['name']}\n\n{doc['text']}")
        else:
            parts.append(doc["text"])
    pasted = (text or "").strip()
    if pasted:
        if docs:
            parts.append(f"## 补充笔记\n\n{pasted}")
        else:
            parts.append(pasted)
    merged = "\n\n".join(parts).strip()
    if len(merged) > MAX_MATERIAL_CHARS:
        raise ValueError(
            f"材料过长（{len(merged):,} 字符），请压缩到 {MAX_MATERIAL_CHARS:,} 字符以内。"
        )
    return merged, docs


def _meta_field(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def suggest_meta(text: str, documents: list[Any] | None = None) -> dict[str, Any]:
    """材料元信息即时生成：优先真实模型，失败回退本地规则并如实标注来源。"""
    merged, _docs = merge_material_documents(documents, text)
    if len(merged) < 60:
        raise ValueError("材料太短，至少提供 60 个字符。")
    if llm.enabled():
        try:
            prompt = llm.load_prompt("meta_suggest.md").replace("{text}", merged[:6000])
            data = llm.chat_json([
                {"role": "system", "content": "你是严谨的学习材料元信息提取器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(), temperature=0.3, max_tokens=500)
            title = _meta_field(data.get("title"), 40) if isinstance(data, dict) else ""
            if title:
                return {
                    "title": title,
                    "subject": _meta_field(data.get("subject"), 20),
                    "goal_title": _meta_field(data.get("goal_title"), 40),
                    "description": _meta_field(data.get("description"), 80),
                    "source": "model",
                }
        except Exception:
            pass
    heading = re.search(r"^#{1,4}\s+(.+)$", merged, re.M)
    first_line = heading.group(1) if heading else merged.splitlines()[0]
    title = _meta_field(re.sub(r"^[\s>#*\-•]+", "", first_line), 28) or "我的学习材料"
    return {
        "title": title,
        "subject": "",
        "goal_title": f"掌握{title[:16]}" if len(title) <= 16 else title,
        "description": "",
        "source": "rules",
    }


def compile_preview(db: Database, title: str, subject: str, text: str,
                    use_llm: bool = True, use_embeddings: bool = True,
                    documents: list[Any] | None = None) -> dict[str, Any]:
    merged, docs = merge_material_documents(documents, text)
    if len(merged) < 60:
        raise ValueError("材料太短，至少提供 60 个字符，才能切出可学习的单元。")
    pack, warnings = compile_material(title, subject, merged, use_llm=use_llm,
                                      use_embeddings=use_embeddings)
    if docs:
        pack.setdefault("source", {})["documents"] = [
            {"name": d["name"], "chars": d["chars"]} for d in docs
        ]
        pack["source"]["material_chars"] = len(merged)
    save_user_pack(pack)
    return {"pack": pack, "summary": pack_summary(pack), "warnings": warnings}


# ---------------------------------------------------------------------------
# 搜索 / 导出 / 重置
# ---------------------------------------------------------------------------
def search(db: Database, user_id: str, q: str) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"units": [], "packs": [], "engine": embeddings.MODEL}
    goal = active_goal(db, user_id)
    units = get_goal_units(db, goal["id"]) if goal else []
    qlow = q.lower()
    scored: dict[str, float] = {}
    for u in units:
        hay = (str(u.get("title") or "") + " " + str(u.get("essence") or "") + " " +
               " ".join(str(x) for x in (u.get("tags") or []))).lower()
        if qlow in hay:
            scored[u["id"]] = 1.0
    semantic = embeddings.rank(q, units, embeddings.unit_text)
    engine = embeddings.last_engine()
    min_score = 0.20 if engine != embeddings.LOCAL_MODEL else 0.06
    for score, u in semantic:
        if score >= min_score:
            scored[u["id"]] = max(scored.get(u["id"], 0.0), score)
    by_id = {u["id"]: u for u in units}
    ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:20]
    found = []
    for uid, score in ranked:
        u = by_id.get(uid)
        if not u:
            continue
        found.append({
            "id": u["id"], "title": u.get("title"), "essence": u.get("essence"),
            "tags": u.get("tags") or [], "goal_id": u["goal_id"],
            "score": round(score, 3),
        })
    packs = [pack_summary(p) for p in all_available_packs()
             if q.lower() in (p.get("name", "") + p.get("description", "") + " ".join(p.get("tags", []))).lower()]
    return {"units": found, "packs": packs[:8], "engine": engine if units else embeddings.active_model()}

def export_data(db: Database, user_id: str) -> dict[str, Any]:
    user = db.one("SELECT * FROM users WHERE id=?", (user_id,))
    goals = db.query("SELECT * FROM goals WHERE user_id=?", (user_id,))
    goal_ids = [g["id"] for g in goals]
    placeholders = ",".join("?" for _ in goal_ids) if goal_ids else "''"
    data = {
        "exported_at": now_iso(),
        "app": "Boil",
        "version": config.VERSION,
        "schema_version": SCHEMA_VERSION,
        "user": user,
        "goals": goals,
        "plans": db.query(f"SELECT * FROM plans WHERE goal_id IN ({placeholders})", tuple(goal_ids)) if goal_ids else [],
        "units": db.query(f"SELECT * FROM units WHERE goal_id IN ({placeholders})", tuple(goal_ids)) if goal_ids else [],
        "relations": db.query(f"SELECT * FROM relations WHERE goal_id IN ({placeholders})", tuple(goal_ids)) if goal_ids else [],
        "packs": [dict(r) for r in db.query("SELECT * FROM packs")],
        "goal_packs": db.query(f"SELECT * FROM goal_packs WHERE goal_id IN ({placeholders})", tuple(goal_ids)) if goal_ids else [],
        "probes": db.query("SELECT p.* FROM probes p JOIN units u ON u.id=p.unit_id "
                           f"WHERE u.goal_id IN ({placeholders})", tuple(goal_ids)) if goal_ids else [],
        "cards": db.query("SELECT * FROM cards WHERE user_id=?", (user_id,)),
        "review_logs": db.query("SELECT * FROM review_logs WHERE user_id=?", (user_id,)),
        "sessions": db.query("SELECT * FROM sessions WHERE user_id=?", (user_id,)),
        "messages": db.query("SELECT m.* FROM messages m JOIN sessions s ON s.id=m.session_id "
                             "WHERE s.user_id=?", (user_id,)),
        "evidence": db.query("SELECT * FROM evidence WHERE user_id=?", (user_id,)),
        "mastery": db.query("SELECT * FROM mastery WHERE user_id=?", (user_id,)),
        "explanations": db.query("SELECT * FROM explanations WHERE user_id=?", (user_id,)),
        "attempts": db.query("SELECT * FROM attempts WHERE user_id=?", (user_id,)),
        "boil_records": db.query("SELECT * FROM boil_records WHERE user_id=?", (user_id,)),
        "unit_notes": db.query("SELECT * FROM unit_notes WHERE user_id=?", (user_id,)),
        "notes": db.query("SELECT * FROM notes WHERE user_id=?", (user_id,)),
        "events": db.query("SELECT * FROM events WHERE user_id=?", (user_id,)),
        "study_days": db.query("SELECT * FROM study_days WHERE user_id=?", (user_id,)),
        "llm": {k: v for k, v in llm.get_config().items() if k != "api_key"},
    }
    return data


def export_anki(db: Database, user_id: str) -> str:
    rows = db.query(
        "SELECT c.front, c.back, c.unit_id, u.title AS unit_title, g.subject AS subject "
        "FROM cards c JOIN units u ON u.id=c.unit_id JOIN goals g ON g.id=u.goal_id "
        "WHERE c.user_id=? ORDER BY g.subject, u.title, c.created_at",
        (user_id,),
    )
    unit_ids = list({r["unit_id"] for r in rows})
    mnemonics: dict[str, str] = {}
    if unit_ids:
        ph = ",".join("?" for _ in unit_ids)
        for n in db.query(
            f"SELECT unit_id, content FROM unit_notes "
            f"WHERE user_id=? AND kind='mnemonic' AND unit_id IN ({ph})",
            (user_id, *unit_ids),
        ):
            mnemonics[n["unit_id"]] = n["content"]

    def tag(text: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(text or "").strip())
        return cleaned.strip("_")[:32] or "general"

    def field(text: Any) -> str:
        return (html.escape(str(text or ""))
                .replace("\r", "").replace("\t", "    ").replace("\n", "<br>"))

    lines = [
        "#separator:tab",
        "#html:true",
        "#tags column:3",
        "# Boil 卡片导出：Anki → 文件 → 导入，选择“制表符分隔”",
    ]
    for r in rows:
        front = field(r["front"])
        back = field(r["back"])
        source = f"{r['unit_title']}" + (f" · {r['subject']}" if r["subject"] else "")
        back += f"<br><br><small>来源：{field(source)}</small>"
        if mnemonics.get(r["unit_id"]):
            back += f"<br><small>口诀：{field(mnemonics[r['unit_id']])}</small>"
        tags = " ".join(dict.fromkeys(["Boil", tag(r["subject"]), tag(r["unit_title"])]))
        lines.append(f"{front}\t{back}\t{tags}")
    return "\n".join(lines) + "\n"


def reset_user(db: Database, user_id: str = config.DEFAULT_USER) -> None:
    goal_ids = [r["id"] for r in db.query("SELECT id FROM goals WHERE user_id=?", (user_id,))]
    with db.transaction():
        if goal_ids:
            ph = ",".join("?" for _ in goal_ids)
            db.execute(f"DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE user_id=?)", (user_id,))
            for table in ("sessions", "explanations", "attempts", "evidence", "mastery",
                          "review_logs", "cards", "probes", "relations", "units", "plans",
                          "goal_packs", "boil_records", "unit_notes", "notes"):
                if table == "probes":
                    db.execute(f"DELETE FROM probes WHERE unit_id IN (SELECT id FROM units WHERE goal_id IN ({ph}))", tuple(goal_ids))
                elif table in ("relations", "units", "plans", "goal_packs"):
                    db.execute(f"DELETE FROM {table} WHERE goal_id IN ({ph})", tuple(goal_ids))
                else:
                    db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            db.execute(f"DELETE FROM goals WHERE id IN ({ph})", tuple(goal_ids))
        db.execute("DELETE FROM events WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM study_days WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
    emit(db, "data.reset", {}, user_id)
