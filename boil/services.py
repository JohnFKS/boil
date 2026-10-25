"""业务服务层：把 DB、算法、Pack、LLM 组合成 API 可调用的用例。"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import (config, decompose, embeddings, heat, llm, mastery, planner, quality, socratic, styles)
from .compiler import compile_material
from .db import Database, SCHEMA_VERSION, json_dumps, json_loads
from .events import emit, recent, touch_study_day
from .packs import (PackError, all_available_packs, find_pack, get_goal_relations,
                    get_goal_units, install_pack, normalize_pack, pack_progress,
                    pack_quality, pack_summary, save_user_pack, unit_from_row,
                    validate_pack)
from .srs.fsrs import card_from_row, interval_label, scheduler
from .utils import (clamp, coverage, days_between, iso_after, new_id, now, now_iso,
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
    # 并发重置会同时走到"用户不存在"这条路上：ON CONFLICT DO NOTHING，
    # 别把第二个请求变成 500（用户看到的是"重置失败"，其实已经重置好了）。
    db.execute(
        "INSERT INTO users (id, name, email, created_at, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(id) DO NOTHING",
        (config.DEFAULT_USER, (name or config.DEMO_USER_NAME).strip()[:40], None, ts, ts))
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
        if u.get("merged_into"):
            continue          # 被吸收的单元不再被推荐，内容仍在归宿里
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


def pack_structure(db: Database, goal_id: str) -> list[dict[str, Any]]:
    """这份目标所用内容包的拆解形状：章→小节、覆盖率、溯源率、类型缺口。

    没有小节登记表（演示包、第三方包）时如实给 None，让界面显示"不知道"，
    而不是把没依据的覆盖率凑成 100%。
    """
    rows = db.query("SELECT DISTINCT pack_id FROM units WHERE goal_id=?", (goal_id,))
    out: list[dict[str, Any]] = []
    for row in rows:
        pack = find_pack(row["pack_id"])
        if not pack:
            continue
        quality_block = pack_quality(pack)
        out.append({"pack_id": pack.get("id"),
                    "name": pack.get("name") or "",
                    "coverage_mode": str((pack.get("source") or {}).get("coverage_mode") or ""),
                    "generated_by": pack.get("generated_by") or "",
                    "chapters": pack.get("chapters") or [],
                    "sections": pack.get("sections") or [],
                    "coverage": quality_block["coverage"],
                    "grounding": quality_block["grounding"],
                    "kinds": quality_block["kinds"],
                    "kind_gaps": quality_block["kind_gaps"],
                    "lint": [i["code"] for i in quality.lint_pack(pack)
                             if i["level"] in ("error", "warn")]})
    return out


def map_data(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
    goal = active_goal(db, user_id, goal_id)
    if not goal:
        raise ValueError("还没有学习目标")
    units = get_goal_units(db, goal["id"])
    relations = get_goal_relations(db, goal["id"])
    statuses = _unit_status_map(db, user_id, units)
    live_units = [u for u in units if not u.get("merged_into")]
    nodes = []
    for i, u in enumerate(live_units):
        st = statuses[u["id"]]
        ref = u.get("source_ref") or {}
        nodes.append({**_unit_card(u, st), "order_index": i,
                      "point_kind": u.get("point_kind") or "",
                      "section": ref.get("id") or "",
                      "facets": st.get("facets") or {},
                      "boundaries": u.get("boundaries") or [],
                      "key_points": u.get("key_points") or []})
    edges = [{"id": r["id"], "source": r["from_unit"], "target": r["to_unit"],
              "kind": r["kind"]} for r in relations
             if r["from_unit"] in statuses and r["to_unit"] in statuses]
    return {"goal": goal, "nodes": nodes, "edges": edges,
            "structure": pack_structure(db, goal["id"]),
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
        _note_anchor(r)
    goal = active_goal(db, user_id)
    return {"notes": rows, "total": db.scalar(
        "SELECT COUNT(*) FROM notes WHERE user_id=?", (user_id,), 0) or 0,
        "goal_id": goal["id"] if goal else None}


def _note_anchor(row: dict[str, Any]) -> dict[str, Any]:
    """anchor_json 是内部列：对外只给解析后的 {search, occurrence}。"""
    raw = row.pop("anchor_json", "{}")
    anchor = json_loads(raw or "{}", {})
    if isinstance(anchor, dict) and anchor.get("search"):
        row["anchor"] = {"search": anchor["search"],
                         "occurrence": int(anchor.get("occurrence") or 0)}
    return row


def create_note(db: Database, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    quote = re.sub(r"\r\n?", "\n", str(payload.get("quote") or "")).strip()[:2000]
    content = re.sub(r"\r\n?", "\n", str(payload.get("content") or "")).strip()[:4000]
    if not quote and not content:
        raise ValueError("引用文字和灵感内容至少要有一个")
    unit_id = str(payload.get("unit_id") or "").strip()[:200] or None
    if unit_id and not db.one("SELECT id FROM units WHERE id=?", (unit_id,)):
        unit_id = None
    goal = active_goal(db, user_id)
    source = str(payload.get("source") or "").strip()[:40]
    if source not in NOTE_SOURCES:
        source = ""
    anchor = payload.get("anchor")
    anchor_out: dict[str, Any] = {}
    if isinstance(anchor, dict):
        search = str(anchor.get("search") or "")[:4000]
        try:
            occurrence = int(anchor.get("occurrence") or 0)
        except (TypeError, ValueError):
            occurrence = 0
        if len(search.strip()) >= 4:
            anchor_out = {"search": search, "occurrence": max(0, min(occurrence, 40))}
    ts = now_iso()
    note_id = new_id("no")
    db.insert("notes", {
        "id": note_id, "user_id": user_id,
        "goal_id": str(payload.get("goal_id") or "").strip()[:200] or (goal["id"] if goal else None),
        "unit_id": unit_id, "kind": "inspiration",
        "quote": quote, "content": content, "anchor_json": json_dumps(anchor_out),
        "source": source,
        "created_at": ts, "updated_at": ts,
    })
    emit(db, "note.created", {"note_id": note_id, "unit_id": unit_id, "source": source,
                              "chars": len(quote) + len(content)}, user_id)
    row = db.one("SELECT n.*, u.title AS unit_title FROM notes n "
                 "LEFT JOIN units u ON u.id = n.unit_id WHERE n.id=? AND n.user_id=?",
                 (note_id, user_id))
    return {"note": _note_anchor(dict(row)) if row else None, "id": note_id}


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


def generate_mnemonic(db: Database, user_id: str, unit_id: str) -> dict[str, Any]:
    """给单元编记忆钩子。材料由服务端读取，不接受前端传来的原文。"""
    row = db.one("SELECT * FROM units WHERE id=?", (unit_id,))
    if not row:
        raise ValueError("单元不存在")
    unit = unit_from_row(row)
    if not llm.enabled():
        raise PermissionError("没有配置模型；速记仍然可以自己写")
    prompt = (llm.load_prompt("mnemonic.md")
              .replace("{title}", unit.get("title") or "")
              .replace("{essence}", _meta_field(unit.get("essence"), 600))
              .replace("{steps}", "\n".join(f"- {s}" for s in (unit.get("steps") or [])[:8]) or "-（无）")
              .replace("{boundaries}", "\n".join(f"- {b}" for b in (unit.get("boundaries") or [])[:6]) or "-（无）")
              .replace("{misconceptions}", "\n".join(f"- {m}" for m in (unit.get("misconceptions") or [])[:6]) or "-（无）"))
    try:
        data = llm.chat_json([
            {"role": "system", "content": "你只负责编记忆钩子，不复述内容、不新增知识。只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ], model=llm.model_name(), temperature=0.5, max_tokens=600)
    except Exception as exc:
        raise ValueError(f"模型没有返回可用的速记：{str(exc)[:140]}") from exc
    items = data.get("mnemonics") if isinstance(data, dict) else None
    hooks = []
    for raw in (items or []):
        text = _meta_field(raw, 60)
        if len(text) >= 4 and text not in hooks:
            hooks.append(text)
    if not hooks:
        raise ValueError("模型没有给出可用的记忆钩子，可以试一次「重新生成」或自己写")
    return {
        "mnemonics": hooks[:3],
        "note": _meta_field(data.get("note") if isinstance(data, dict) else "", 80),
        "unit_id": unit_id,
        "source": "model",
    }


CLOZE_MIN, CLOZE_MAX = 3, 8


def _rules_cards(text: str) -> list[dict[str, str]]:
    """没有模型时的确定性抽取：问答、配对、完形三种形状，只从原文取词。"""
    out: list[dict[str, str]] = []
    lines = [re.sub(r"^[\s>\-*•·]+", "", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if 4 <= len(l) <= 400]
    for line in lines:
        if len(out) >= 6:
            break
        m = re.match(r"^(.{2,60}?)[？?]\s*[:：]?\s*(.{2,300})$", line)
        if m and len(m.group(2)) >= 4:
            out.append({"front": m.group(1).strip() + "？", "back": m.group(2).strip()})
            continue
        m = re.match(r"^(.{2,40}?)\s*[:：=→]\s*(.{2,300})$", line)
        if m:
            out.append({"front": f"{m.group(1).strip()}是什么？", "back": m.group(2).strip()})
    if not out:
        for line in lines:
            words = re.findall(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_]{2,7}", line)
            if not words or len(line) < 12:
                continue
            pick = max(words, key=lambda w: (len(w) >= CLOZE_MIN and len(w) <= CLOZE_MAX, line.rfind(w)))
            if not (CLOZE_MIN <= len(pick) <= CLOZE_MAX):
                continue
            blank = line.replace(pick, "（　）", 1)
            if blank == line:
                continue
            out.append({"front": blank[:300], "back": f"{pick}（原文：{line[:160]}）"})
            if len(out) >= 3:
                break
    return out


def cards_from_text(db: Database, user_id: str, text: str,
                    unit_id: str | None = None) -> dict[str, Any]:
    """把选中的文字提炼成复习卡——只出预览，用户确认后才入库（借鉴 testlearn 的 memorize）。"""
    source = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    clipped = ""
    if len(source) < 8:
        raise ValueError("选中内容太短，至少 8 个字才能提炼卡片")
    if len(source) > 4000:
        clipped = f"选中 {len(source):,} 字太长，只送了前 4,000 字。"
        source = source[:4000]
    unit = None
    if unit_id:
        unit = db.one("SELECT id, title, essence FROM units WHERE id=?", (unit_id,))
        if not unit:
            unit = None
    cards: list[dict[str, str]] = []
    engine = "rules"
    note = "没有配置模型，以下卡片由本地规则（问答/配对/完形）生成。"
    if llm.enabled():
        try:
            prompt = (llm.load_prompt("cards_from_text.md")
                      .replace("{unit}", (unit or {}).get("title") or "（未关联单元）")
                      .replace("{text}", source))
            data = llm.chat_json([
                {"role": "system", "content": "你只把给定文字变成复习卡，不新增事实。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(), temperature=0.3, max_tokens=900)
            raw = data.get("cards") if isinstance(data, dict) else data
            seen: set[str] = set()
            for item in (raw or [])[:8]:
                if not isinstance(item, dict):
                    continue
                front = _meta_field(item.get("front"), 300)
                back = _meta_field(item.get("back"), 600)
                if len(front) < 4 or len(back) < 2 or front in seen:
                    continue
                seen.add(front)
                cards.append({"front": front, "back": back})
            engine = "model"
            note = "由模型基于选中文字提炼；确认前不会进入复习队列。"
        except Exception as exc:
            note = f"模型提炼失败，已改用本地规则：{str(exc)[:120]}"
    if not cards:
        cards = _rules_cards(source)
        engine = "rules"
    if not cards:
        raise ValueError("这段文字里没能提炼出可用的卡片，换一段有结论性的内容再试")
    if clipped:
        note = f"{note}{clipped}"
    return {
        "status": "preview",
        "cards": cards,
        "engine": engine,
        "engine_note": note,
        "unit_id": unit["id"] if unit else None,
        "unit_title": unit["title"] if unit else None,
        "tool_args": {"cards": cards, "unit_id": unit["id"] if unit else None},
    }


def confirm_cards(db: Database, user_id: str, cards: Any, unit_id: str | None) -> dict[str, Any]:
    """确认后的卡片写入复习队列：必须挂在某个单元上，否则 FSRS 无法排程。"""
    unit = None
    if unit_id:
        unit = db.one("SELECT id, title FROM units WHERE id=?", (unit_id,))
    if not unit:
        goal = active_goal(db, user_id)
        if goal:
            row = db.one("SELECT id, title FROM units WHERE goal_id=? ORDER BY order_index, id",
                         (goal["id"],))
            unit = row
    if not unit:
        raise ValueError("需要先有学习目标的单元，卡片才能被排进复习")
    ts = now_iso()
    created = []
    for item in (cards or [])[:8]:
        if not isinstance(item, dict):
            continue
        front = _meta_field(item.get("front"), 300)
        back = _meta_field(item.get("back"), 600)
        if len(front) < 4 or len(back) < 2:
            continue
        card_id = new_id("cd")
        db.insert("cards", {
            "id": card_id, "user_id": user_id, "unit_id": unit["id"], "probe_id": None,
            "front": front, "back": back, "state": "new", "step": 0, "stability": 0,
            "difficulty": 0, "due_at": ts, "last_review": None, "reps": 0, "lapses": 0,
            "created_at": ts, "updated_at": ts,
        })
        created.append(card_id)
    if not created:
        raise ValueError("没有有效的卡片可保存")
    emit(db, "cards.generated", {"unit_id": unit["id"], "count": len(created),
                                "source": "selection"}, user_id)
    return {
        "ok": True, "count": len(created), "card_ids": created,
        "unit_id": unit["id"], "unit_title": unit["title"],
        "summary": f"已加入 {len(created)} 张复习卡到「{unit['title']}」，今天就会到期一次。",
    }


def ask_about_selection(db: Database, user_id: str, question: str,
                        context: str, unit_id: str | None = None) -> dict[str, Any]:
    """对选中内容追问一句：带上锚点上下文，单轮回答，可存成灵感。"""
    if not llm.enabled():
        raise PermissionError("没有配置模型；追问需要真实模型")
    q = str(question or "").strip()[:500]
    if not q:
        raise ValueError("问题不能为空")
    anchor = re.sub(r"\r\n?", "\n", str(context or ""))[:2400]
    unit = db.one("SELECT id, title, essence FROM units WHERE id=?", (unit_id,)) if unit_id else None
    prompt = (llm.load_prompt("quick_ask.md")
              .replace("{unit}", (unit or {}).get("title") or "（未关联单元）")
              .replace("{context}", anchor or "（没有上下文）")
              .replace("{question}", q))
    try:
        answer = llm.chat([
            {"role": "system", "content": "你是学习教练，只根据给出的上下文与常识回答，简洁。"},
            {"role": "user", "content": prompt},
        ], model=llm.model_name(), temperature=0.4, max_tokens=800)
    except Exception as exc:
        raise ValueError(f"模型没有回答：{str(exc)[:140]}") from exc
    return {
        "question": q,
        "answer": answer.strip()[:2000],
        "source": "model",
        "unit_id": unit["id"] if unit else None,
    }


def save_style_test(db: Database, user_id: str, answers: Any) -> dict[str, Any]:
    """学习风格测试：5 题 → 教学人设，写进当前目标的 meta，供追问与讲解带法使用。"""
    goal = active_goal(db, user_id)
    if not goal:
        raise ValueError("还没有学习目标；先创建目标再测")
    persona = styles.compute_persona(answers)
    persona["answers"] = [int(a) for a in answers]
    persona["taken_at"] = now_iso()
    meta = dict(goal.get("meta") or {})
    meta["persona"] = persona
    db.execute("UPDATE goals SET meta_json=?, updated_at=? WHERE id=?",
               (json_dumps(meta), now_iso(), goal["id"]))
    emit(db, "style.tested", {"goal_id": goal["id"], "persona": persona["key"],
                              "secondary": persona.get("secondary")}, user_id)
    return {"goal_id": goal["id"], "persona": persona}


def persona_for_goal(db: Database, user_id: str, goal_id: str | None) -> dict[str, Any]:
    if not goal_id:
        goal = active_goal(db, user_id)
        goal_id = goal["id"] if goal else None
    if not goal_id:
        return {}
    row = db.one("SELECT meta_json FROM goals WHERE id=?", (goal_id,))
    if not row:
        return {}
    return (json_loads(row["meta_json"], {}) or {}).get("persona") or {}


MAX_UNDO_RELATIONS = 200


def _chain_target(db: Database, unit_id: str, limit: int = 12) -> str | None:
    """沿 merged_into 走到最终归宿；链太长或成环就停下，绝不静默丢内容。"""
    seen = {unit_id}
    current = unit_id
    for _ in range(limit):
        row = db.one("SELECT merged_into FROM units WHERE id=?", (current,))
        nxt = (row or {}).get("merged_into")
        if not nxt or nxt in seen:
            return current if current != unit_id else None
        seen.add(nxt)
        current = nxt
    return current


def unit_duplicates(db: Database, user_id: str, goal_id: str | None = None,
                    judge: bool = True) -> dict[str, Any]:
    """当前目标里疑似重复的存活单元对：分数只提候选，能不能合并由裁决阶梯决定。"""
    goal = active_goal(db, user_id, goal_id)
    if not goal:
        raise ValueError("还没有学习目标")
    units = [u for u in get_goal_units(db, goal["id"]) if not u.get("merged_into")]
    pairs = quality.find_near_duplicates({"units": units})
    by_id = {u["id"]: u for u in units}
    judged = 0
    for pair in pairs[:8]:
        a, b = by_id.get(pair["keep"]), by_id.get(pair["merge"])
        if not a or not b:
            continue
        verdict = None
        # 字面不够硬的候选都要模型签字，但一次请求最多判 JUDGE_LIMIT 对：
        # 剩下的标 pending，用户真要点合并时再判（同样命中缓存），别让打开地图变成烧钱
        need_model = not pair["auto"]
        if judge and need_model and judged < quality.JUDGE_LIMIT:
            verdict = quality.judge_pair(a, b)
            judged += 1
        decision = quality.merge_decision(a, b, verdict=verdict, semantic=pair["semantic"])
        pair["pending_judgement"] = bool(need_model and verdict is None)
        pair["same"] = None if verdict is None else verdict.get("same")
        pair["model_reason"] = (verdict or {}).get("reason") or ""
        pair["allowed"] = decision["allowed"]
        pair["basis"] = decision["basis"]
        pair["decision_reason"] = decision["reason"]
    semantic_used = any(p.get("semantic") is not None for p in pairs)
    return {"goal_id": goal["id"], "pairs": pairs,
            "engine": "semantic" if semantic_used else "rules",
            "checked": len(units), "judged": judged,
            "thresholds": {"lexical": quality.MERGE_LEXICAL_FLOOR,
                           "candidate": quality.SEMANTIC_CANDIDATE_RATIO,
                           "list": quality.DUPLICATE_TITLE_RATIO,
                           "judge_limit": quality.JUDGE_LIMIT}}


def merge_units(db: Database, user_id: str, from_id: str, into_id: str,
                reason: str = "", override: bool = False,
                confirmed: bool = False) -> dict[str, Any]:
    """合并单元：被吸收的单元不删除，只改指向归宿，卡片与笔记都还在原处。

    裁决用三级阶梯（见 quality.merge_decision）：字面近乎逐字、或语义过自动线，
    代码直接放行；两者之间的中间带必须有模型「是同一件事」的判定才放行。
    模型会翻，所以**非字面证据的合并一律要求 `confirmed: true`**（前端确认框），
    否则就出现「页面写不可合并、端点自己改判真合了库」这种事故；人被分数挡住但坚持要合的，
    必须写明理由（basis="user" 记账），不能让一次点击冒充裁决。
    前置关系会重连到归宿上，其中自环与重复边会被删掉——所以图结构改动当前不可撤销。
    """
    from_id, into_id = (from_id or "").strip(), (into_id or "").strip()
    if not from_id or not into_id:
        raise ValueError("需要 from 与 into 两个单元")
    if from_id == into_id:
        raise ValueError("不能把单元合并到自己")
    source = db.one("SELECT * FROM units WHERE id=?", (from_id,))
    target = db.one("SELECT * FROM units WHERE id=?", (into_id,))
    if not source or not target:
        raise ValueError("单元不存在")
    if source["goal_id"] != target["goal_id"]:
        raise ValueError("只能合并同一目标下的单元")
    hop = _chain_target(db, into_id)
    if hop:
        into_id = hop
        target = db.one("SELECT * FROM units WHERE id=?", (into_id,))
    if into_id == from_id:
        raise ValueError("这两个单元已经指向同一个归宿，不能再互相合并")
    # 裁决要对准**最终归宿**，不是用户点的那个中间单元
    semantic = quality.semantic_pair(source, target)
    decision = quality.merge_decision(source, target, semantic=semantic)
    if (not decision["allowed"] and decision["basis"] == "none" and semantic is not None
            and semantic >= quality.SEMANTIC_CANDIDATE_RATIO):
        decision = quality.merge_decision(
            source, target, verdict=quality.judge_pair(source, target), semantic=semantic)
    if decision["allowed"] and decision["basis"] != "lexical" and not confirmed:
        # 非字面的合并必须由人点头：模型两次能给出相反答案，缓存关掉时更是如此。
        # 少了这个闸，页面写「不可合并」而端点自己改判并真的合了库（真实模拟抓到过）。
        raise ValueError(f"需要你先确认：{decision['reason']}。"
                         "这一步会重写前置关系且当前不能撤销")
    if not decision["allowed"]:
        if override and len(str(reason or "").strip()) >= 4:
            decision = {"allowed": True, "basis": "user",
                        "reason": f"学习者确认：{_meta_field(reason, 120)}",
                        "signals": decision["signals"]}
        else:
            raise ValueError(f"不能合并：{decision['reason']}。"
                             "请保留两个单元，或写明理由后坚持合并")
    similarity = max(decision["signals"]["lexical"], decision["signals"]["semantic"] or 0.0)

    ts = now_iso()
    note = reason or decision["reason"]
    stored_reason = _meta_field(
        f"学习者确认：{note}" if decision["basis"] == "user" else note, 200)
    changes: list[dict[str, Any]] = []
    with db.transaction():
        db.execute("UPDATE units SET merged_into=?, merged_reason=?, updated_at=? WHERE id=?",
                   (into_id, stored_reason, ts, from_id))
        # 前置关系重连：指向被合并单元的边改指归宿，自己指向自己的边删掉。
        # 每一条的实际改动都记下来，撤销合并时按记录回滚（否则图就再也回不去了）。
        for rel in db.query("SELECT * FROM relations WHERE from_unit=? OR to_unit=?",
                            (from_id, from_id)):
            new_from = into_id if rel["from_unit"] == from_id else rel["from_unit"]
            new_to = into_id if rel["to_unit"] == from_id else rel["to_unit"]
            record = {"id": rel["id"], "goal_id": rel["goal_id"], "kind": rel["kind"],
                      "old_from": rel["from_unit"], "old_to": rel["to_unit"]}
            if new_from == new_to:
                db.execute("DELETE FROM relations WHERE id=?", (rel["id"],))
                changes.append({**record, "action": "delete"})
                continue
            exists = db.one("SELECT id FROM relations WHERE goal_id=? AND from_unit=? "
                            "AND to_unit=? AND kind=?",
                            (rel["goal_id"], new_from, new_to, rel["kind"]))
            if exists:
                db.execute("DELETE FROM relations WHERE id=?", (rel["id"],))
                changes.append({**record, "action": "delete"})
            else:
                db.execute("UPDATE relations SET from_unit=?, to_unit=? WHERE id=?",
                           (new_from, new_to, rel["id"]))
                changes.append({**record, "action": "update",
                                "new_from": new_from, "new_to": new_to})
        # 卡片不动：它们仍指向被合并的单元，复习照常发生，只是地图上不再单独出现
        event = emit(db, "unit.merged", {
            "from": from_id, "into": into_id,
            "similarity": round4(similarity),
            "basis": decision["basis"],
            "signals": decision["signals"],
            "undo": {"relations": changes[:MAX_UNDO_RELATIONS],
                     "truncated": len(changes) > MAX_UNDO_RELATIONS},
        }, user_id)
        db.execute("UPDATE units SET merged_op=? WHERE id=?", (event["id"], from_id))
    return {
        "from": from_id, "into": into_id,
        "from_title": source["title"], "into_title": target["title"],
        "similarity": round4(similarity),
        "basis": decision["basis"], "signals": decision["signals"],
        "decision_reason": decision["reason"],
        "cards_kept": db.scalar("SELECT COUNT(*) FROM cards WHERE unit_id=?", (from_id,), 0) or 0,
    }


def _unmerge_blockers(db: Database, unit: dict[str, Any]) -> str:
    """撤销合并前的检查：归宿还活着、自己没吸收过别人、记录还在。

    合并不删内容，但关系边是按记录重写的；只要有一环被别人动过，
    与其猜一个「大概对」的图，不如说清楚为什么现在不能撤。
    """
    into_id = unit.get("merged_into")
    if not into_id:
        return "这个单元没有被合并"
    target = db.one("SELECT id, merged_into FROM units WHERE id=?", (into_id,))
    if not target:
        return "归宿单元已经不存在了"
    if target.get("merged_into"):
        return "归宿本身也被并进了别的单元，先撤销那一次"
    absorbed = db.scalar("SELECT COUNT(*) FROM units WHERE merged_into=?", (unit["id"],), 0) or 0
    if absorbed:
        return f"这个单元在被吸收后又收了 {absorbed} 个单元，先撤销那几次"
    return ""


def unmerge_unit(db: Database, user_id: str, unit_id: str) -> dict[str, Any]:
    """撤销一次合并：指针复原，关系边按合并时记下的改动回滚。"""
    row = db.one("SELECT * FROM units WHERE id=?", (unit_id,))
    if not row:
        raise ValueError("单元不存在")
    unit = unit_from_row(row)
    blocked = _unmerge_blockers(db, unit)
    if blocked:
        raise ValueError(blocked)
    into_id = unit["merged_into"]
    op_id = unit.get("merged_op")
    undo: dict[str, Any] = {}
    record_found = False
    if op_id:
        ev = db.one("SELECT payload_json FROM events WHERE id=? AND type='unit.merged'", (op_id,))
        if ev:
            payload = json_loads(ev["payload_json"], {}) or {}
            undo = payload.get("undo") or {}
            record_found = isinstance(payload.get("undo"), dict)
    changes = undo.get("relations") or []
    restored = skipped = 0
    ts = now_iso()
    with db.transaction():
        db.execute("UPDATE units SET merged_into=NULL, merged_reason='', merged_op=NULL, "
                   "updated_at=? WHERE id=?", (ts, unit_id))
        for ch in changes[:MAX_UNDO_RELATIONS]:
            rid = str(ch.get("id") or "")
            old_from, old_to = ch.get("old_from"), ch.get("old_to")
            if not rid or not old_from or not old_to:
                skipped += 1
                continue
            if not db.one("SELECT id FROM units WHERE id=?", (old_from,)) or \
                    not db.one("SELECT id FROM units WHERE id=?", (old_to,)):
                skipped += 1          # 端点已经不在了，不复活一条悬空边
                continue
            if ch.get("action") == "update":
                cur = db.one("SELECT * FROM relations WHERE id=?", (rid,))
                if (cur and cur["from_unit"] == ch.get("new_from")
                        and cur["to_unit"] == ch.get("new_to")):
                    db.execute("UPDATE relations SET from_unit=?, to_unit=? WHERE id=?",
                               (old_from, old_to, rid))
                    restored += 1
                else:
                    skipped += 1      # 合并没有那样改过，或者之后又被改过
            else:
                dup = db.one("SELECT id FROM relations WHERE goal_id=? AND from_unit=? "
                             "AND to_unit=? AND kind=?",
                             (ch.get("goal_id"), old_from, old_to, ch.get("kind")))
                if dup:
                    skipped += 1
                    continue
                db.insert("relations", {"id": rid, "goal_id": ch.get("goal_id"),
                                        "from_unit": old_from, "to_unit": old_to,
                                        "kind": ch.get("kind") or "prerequisite"})
                restored += 1
        emit(db, "unit.unmerged", {"from": unit_id, "into": into_id, "op": op_id,
                                   "restored": restored, "skipped": skipped}, user_id)
    target = db.one("SELECT title FROM units WHERE id=?", (into_id,)) or {}
    if not record_found:
        note = "这次合并没有留下可回滚的记录（多半来自旧版本或导入），只撤销了指向，关系边保持现状"
    elif changes:
        note = f"已恢复 {restored} 条关系边" + (f"，跳过 {skipped} 条" if skipped else "")
    else:
        note = "这次合并没有改动任何前置关系，已直接撤销指向"
    return {"from": unit_id, "into": into_id,
            "from_title": unit["title"], "into_title": target.get("title", ""),
            "relations_restored": restored, "relations_skipped": skipped,
            "degraded": not record_found, "note": note}


def section_of(pack: dict[str, Any] | None, section_id: str) -> dict[str, Any] | None:
    """按编号找到那一节（切片编号 sec-3#1 归回 sec-3），返回标题与原文。"""
    if not pack or not section_id:
        return None
    want = str(section_id).split("#", 1)[0]
    for table in pack.get("sections") or []:
        if table.get("id") == want:
            return table
    return None


def material_of(unit: dict[str, Any]) -> dict[str, Any]:
    """这一条是从材料的哪一节、哪一句长出来的。

    锚点在编译时就逐字核过才写进包（`kind=section`）；这里不重新发明一套判断，
    只做两件事：确认那个节号在这份包的登记表里还存在，以及把标题带回去。
    演示包与第三方包没有登记表，就如实返回 available=False，
    界面显示"没有可核对的出处"，而不是假装它来自某段原文。
    """
    ref = unit.get("source_ref") or {}
    anchor = str(ref.get("anchor") or "")
    out = {"available": False, "section_id": str(ref.get("id") or ""),
           "title": str(ref.get("title") or ""), "anchor": anchor,
           "via": str(ref.get("via") or ""), "kind": str(ref.get("kind") or ""),
           "section_chars": None}
    if not anchor:
        return out
    pack = find_pack(unit.get("pack_id") or "")
    table = section_of(pack, out["section_id"])
    if table:
        out["title"] = table.get("title") or out["title"]
        out["section_chars"] = table.get("chars")
        # 登记表里若真带着正文（第三方包），就地再核一次字面是否命中
        body = str(table.get("body") or "")
        out["available"] = bool(decompose.verify(body, anchor)) if body else True
    elif out["section_id"]:
        out["available"] = out["kind"] == "section"     # 旧包没有登记表，但不等于锚点是编的
    return out


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
        attach_mistake_tags(d, d)
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
    # 复习时写下的回忆单独取一份：它和探测题作答不是一回事，混在一个列表里两头都看不清
    recalls = db.query(
        "SELECT id, response, score, created_at FROM attempts "
        "WHERE unit_id=? AND user_id=? AND kind='review_recall' "
        "ORDER BY created_at DESC LIMIT 6",
        (unit_id, user_id),
    )
    h = heat.heat_for_unit(db, user_id, unit_id)
    pn = heat.pn_state(db, user_id, unit)
    records = heat.history(db, user_id, unit_id=unit_id, limit=6)
    all_units = get_goal_units(db, unit["goal_id"])
    related = embeddings.related_units(unit, all_units)
    merged_into = None
    if unit.get("merged_into"):
        row = db.one("SELECT id, title FROM units WHERE id=?", (unit["merged_into"],))
        merged_into = {"id": row["id"], "title": row["title"]} if row else None
    absorbed = db.query("SELECT id, title, merged_reason FROM units "
                        "WHERE goal_id=? AND merged_into=? ORDER BY title",
                        (unit["goal_id"], unit_id))
    unmerge_blocked = _unmerge_blockers(db, unit)
    return {"unit": unit, "mastery": m, "status": st[unit_id],
            "heat": h, "heat_label": heat.heat_label(h), "pn": pn,
            "merged_into": merged_into, "absorbed": absorbed,
            "can_unmerge": not unmerge_blocked,
            "unmerge_blocked_reason": unmerge_blocked,
            "records": records, "notes": get_unit_notes(db, user_id, unit_id),
            "related": related,
            "probes": probes, "cards": cards, "sessions": sessions,
            "relations": relations, "attempts": attempts, "recalls": recalls,
            "material": material_of(unit),
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
        attach_mistake_tags(d, d)
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
            "mistake_tags": [{"key": k, "label": v} for k, v in MISTAKE_TAGS],
            "stats": _study_stats(db, user_id)}


def list_cards(db: Database, user_id: str, q: str | None = None,
               unit_id: str | None = None, state: str | None = None,
               limit: int = 60) -> dict[str, Any]:
    """全量卡片清单：复习中途「提前加卡」与按单元筛选用。"""
    limit = int(clamp(int(limit or 60), 1, 200))
    sql = ("SELECT c.*, u.title AS unit_title FROM cards c "
           "JOIN units u ON u.id=c.unit_id WHERE c.user_id=?")
    params: list[Any] = [user_id]
    if unit_id:
        sql += " AND c.unit_id=?"
        params.append(unit_id)
    if state in ("new", "learning", "review", "relearning"):
        sql += " AND c.state=?"
        params.append(state)
    keyword = (q or "").strip()
    if keyword:
        sql += " AND (c.front LIKE ? OR c.back LIKE ? OR u.title LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    sql += " ORDER BY CASE WHEN c.state='new' THEN 0 ELSE 1 END, c.due_at, c.created_at LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    now_s = now_iso()
    cards = []
    for r in rows:
        d = _public_card(dict(r))
        d["unit_title"] = r.get("unit_title") or ""
        d["due_label"] = _due_label(r)
        d["is_due"] = bool(r.get("due_at") and r["due_at"] <= now_s)
        attach_mistake_tags(d, dict(r))
        cards.append(d)
    return {"cards": cards, "total": db.scalar(
        "SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,), 0) or 0}


def _elapsed_days(card: dict[str, Any]) -> float:
    last = card.get("last_review")
    if not last:
        return 0.0
    return max(0.0, (now() - parse_iso(last)).total_seconds() / 86400.0)


MISTAKE_TAGS: tuple[tuple[str, str], ...] = (
    ("concept", "概念没吃透"),
    ("boundary", "条件/边界漏了"),
    ("step", "步骤缺一环"),
    ("confusion", "跟别的记混了"),
    ("carelessness", "粗心笔误"),
    ("blank", "完全没印象"),
)
MISTAKE_KEYS = {k for k, _ in MISTAKE_TAGS}
MISTAKE_LABELS = dict(MISTAKE_TAGS)


def normalize_mistake_tags(raw: Any) -> list[str]:
    """固定 taxonomy：只认已知标签，去重、保序、最多 4 个。"""
    out: list[str] = []
    for item in (raw or []):
        key = str(item or "").strip()[:20]
        if key in MISTAKE_KEYS and key not in out:
            out.append(key)
    return out[:4]


def rate_card(db: Database, user_id: str, card_id: str, rating: int,
              elapsed_ms: int = 0, recall: str = "",
              mistake_tags: Any = None) -> dict[str, Any]:
    """评分 = 读卡片 → FSRS 计算 → 写回，整个链条必须在一个写事务里。

    裸着跑会出现两种真会发生的坏情况：连点两下时两个线程都从"复习前"的状态算，
    后写的把先写的覆盖掉（reps 少记一半）；中途异常则留下"日志有、卡片没动"的半截复习。
    """
    with db.transaction():
        return _rate_card(db, user_id, card_id, rating, elapsed_ms, recall, mistake_tags)


def _rate_card(db: Database, user_id: str, card_id: str, rating: int,
               elapsed_ms: int = 0, recall: str = "",
               mistake_tags: Any = None) -> dict[str, Any]:
    card = db.one("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id))
    if not card:
        raise ValueError("卡片不存在")
    rating = int(clamp(int(rating), 1, 4))
    before_heat = heat.heat_for_unit(db, user_id, card["unit_id"])
    before = dict(card)
    mastery_before = db.one(
        "SELECT * FROM mastery WHERE user_id=? AND unit_id=? AND facet='recall'",
        (user_id, card["unit_id"]))
    result = scheduler.schedule(card_from_row(card), rating)
    ts = now_iso()
    tags = normalize_mistake_tags(mistake_tags)
    # 答对了就不继续挂着上一次的错因；答错/模糊时记下这次的标签
    logged_tags = tags if rating <= 2 else []
    card_tags = logged_tags
    db.execute(
        "UPDATE cards SET state=?, step=?, stability=?, difficulty=?, due_at=?, "
        "last_review=?, reps=?, lapses=?, mistake_tags_json=?, updated_at=? WHERE id=?",
        (result.state, result.step, result.stability, result.difficulty,
         result.due_iso(), ts, result.reps, result.lapses, json_dumps(card_tags), ts, card_id),
    )
    log_id = new_id("rv")
    db.insert("review_logs", {
        "id": log_id,
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
        "mistake_tags_json": json_dumps(logged_tags),
        "missed_points": 1 if rating <= 2 else 0,
        "source": "web",
    })
    score = {1: 0.1, 2: 0.45, 3: 0.8, 4: 0.95}[rating]
    said = re.sub(r"\r\n?", "\n", str(recall or "")).strip()[:4000]
    attempt_id = None
    if said:
        # kind=review_recall、probe_id=None：探测题的「N 次作答」按 probe_id 统计，
        # 复习时写的回忆不是作答那道题，混进去计数就成谎话了。
        attempt_id = new_id("at")
        db.insert("attempts", {
            "id": attempt_id,
            "user_id": user_id,
            "unit_id": card["unit_id"],
            "probe_id": None,
            "kind": "review_recall",
            "response": said,
            "score": score,
            "confidence": 0.0,
            "hints_used": 0,
            "feedback_json": json_dumps({"source": "review", "card_id": card_id,
                                         "probe_id": card.get("probe_id")}),
            "created_at": ts,
        })
    add = mastery.add_evidence(db, user_id, card["unit_id"], "recall", score,
                               confidence=0.8, weight=0.9, source="review")
    after_heat = heat.heat_for_unit(db, user_id, card["unit_id"])
    boil_id = heat.record_boil(db, user_id, card["unit_id"], "recall", 3,
                               {"rating": rating, "interval_days": round4(result.interval_days)},
                               before_heat, after_heat, [])
    touch_study_day(db, user_id, minutes=max(1, int(elapsed_ms / 60000)) if elapsed_ms else 1,
                    sessions=0, reviews=1)
    snapshot = {k: before.get(k) for k in CARD_STATE_FIELDS}
    event = emit(db, "review.rated", {
        "card_id": card_id, "unit_id": card["unit_id"],
        "rating": rating, "interval_days": round4(result.interval_days),
        "undo": {"card": snapshot,
                 "mastery": dict(mastery_before) if mastery_before else None,
                 "log_id": log_id, "attempt_id": attempt_id,
                 "evidence_id": add.get("evidence_id"), "boil_id": boil_id,
                 "day": today_str()},
    }, user_id)
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
        "mistake_tags": logged_tags,
        "remaining": review_queue(db, user_id, limit=1)["due_total"],
        "undo": {
            "op": event["id"],
            "rating": rating,
            "interval_label": interval_label(result.interval_seconds),
            "due_before": before.get("due_at"),
            "state_before": before.get("state") or "new",
            "mastery_before": (round4(float(mastery_before.get("score") or 0))
                               if mastery_before else None),
            "mastery_after": add.get("score"),
        },
    }


CARD_STATE_FIELDS = ("state", "step", "stability", "difficulty", "due_at",
                     "last_review", "reps", "lapses", "mistake_tags_json")


def undo_review(db: Database, user_id: str, op_id: str) -> dict[str, Any]:
    """撤销最近一次评分：卡片状态、掌握度、日志、回忆、火候记录一起回到评分前。

    掌握度是状态量（EMA），不能「减掉一条证据」——所以评分时就把快照存进事件，
    撤销时整行还原。只允许撤最近一次：同一单元之后若又复习过，快照已经作废。
    """
    op_id = str(op_id or "").strip()
    if not op_id:
        raise ValueError("缺少撤销凭据")
    # 检查"这是不是最近一次评分"和回滚必须在同一个写事务里：
    # 分开写的话，中间插进来一次评分，撤销就会把那次评分一起抹掉（卡片计数从此对不上日志）。
    with db.transaction():
        ev = db.one("SELECT * FROM events WHERE id=? AND type='review.rated' AND user_id=?",
                    (op_id, user_id))
        if not ev:
            raise ValueError("找不到这次评分的记录")
        payload = json_loads(ev["payload_json"], {}) or {}
        undo = payload.get("undo") or {}
        if not undo.get("log_id") or not undo.get("card"):
            raise ValueError("这次评分来自旧版本，没有留下可回滚的快照")
        log = db.one("SELECT * FROM review_logs WHERE id=?", (undo["log_id"],))
        card = db.one("SELECT * FROM cards WHERE id=? AND user_id=?",
                      (payload.get("card_id"), user_id))
        if not log or not card:
            raise ValueError("这次评分或卡片已经不在了")
        # 用 rowid 而不是 reviewed_at：时间戳只到秒，同一秒里的两次复习会分不出先后
        later = db.scalar(
            "SELECT COUNT(*) FROM review_logs l JOIN cards c ON c.id=l.card_id "
            "WHERE c.unit_id=? AND c.user_id=? AND l.rowid > "
            "(SELECT rowid FROM review_logs WHERE id=?)",
            (payload.get("unit_id"), user_id, log["id"]), 0) or 0
        if later:
            raise ValueError(f"这个单元在那之后又复习了 {later} 次；"
                             "撤掉这一条会把后面的掌握度一起抹掉，所以不允许")
        snap = undo["card"]
        ts = now_iso()
        db.execute(
            "UPDATE cards SET state=?, step=?, stability=?, difficulty=?, due_at=?, "
            "last_review=?, reps=?, lapses=?, mistake_tags_json=?, updated_at=? WHERE id=?",
            (snap.get("state") or "new", int(snap.get("step") or 0),
             float(snap.get("stability") or 0), float(snap.get("difficulty") or 0),
             snap.get("due_at"), snap.get("last_review"),
             int(snap.get("reps") or 0), int(snap.get("lapses") or 0),
             snap.get("mistake_tags_json") or "[]", ts, card["id"]),
        )
        db.execute("DELETE FROM review_logs WHERE id=?", (log["id"],))
        for key, table in (("attempt_id", "attempts"), ("evidence_id", "evidence"),
                           ("boil_id", "boil_records")):
            if undo.get(key):
                db.execute(f"DELETE FROM {table} WHERE id=?", (undo[key],))
        m = undo.get("mastery")
        if m:
            db.upsert("mastery", {k: m.get(k) for k in (
                "user_id", "unit_id", "facet", "score", "confidence", "reps",
                "lapses", "last_evidence_at", "updated_at")}, "user_id,unit_id,facet")
        else:
            db.execute("DELETE FROM mastery WHERE user_id=? AND unit_id=? AND facet='recall'",
                       (user_id, payload.get("unit_id")))
        if undo.get("day") == today_str():
            db.execute("UPDATE study_days SET reviews=MAX(0, reviews-1) "
                       "WHERE user_id=? AND day=?", (user_id, undo["day"]))
        emit(db, "review.undone", {"op": op_id, "card_id": card["id"],
                                   "rating": payload.get("rating")}, user_id)
    restored = db.one("SELECT * FROM cards WHERE id=?", (card["id"],))
    return {"card": _public_card(restored), "rating": payload.get("rating"),
            "note": "已撤销这次评分；卡片与掌握度都回到了评分前"}


def _public_card(card: dict[str, Any]) -> dict[str, Any]:
    out = {k: card.get(k) for k in ("id", "unit_id", "front", "back", "state", "due_at",
                                    "reps", "lapses", "stability", "difficulty")}
    attach_mistake_tags(out, card)
    return out


def attach_mistake_tags(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """把 mistake_tags_json 解析成前端好用的两个字段；原始 JSON 列不外泄。"""
    raw = source.get("mistake_tags_json")
    tags = [t for t in json_loads(raw or "[]", []) if t in MISTAKE_KEYS]
    target["mistake_tags"] = tags
    target["mistake_labels"] = [MISTAKE_LABELS.get(t, t) for t in tags]
    target.pop("mistake_tags_json", None)
    return target


# ---------------------------------------------------------------------------
# 错题本与考前速背（借鉴 kaobuddy 的两处设计，落到 Boil 自己的数据结构上）
# ---------------------------------------------------------------------------
def mistake_book(db: Database, user_id: str, days: int = 30, limit: int = 80) -> dict[str, Any]:
    """答错过的东西，按"错了几次 / 最近什么时候 / 错在哪"聚合出来。

    这些数据一直都在（每条复习日志都带错因与失分标记），但从来没有任何视图把它们端到人面前：
    洞察页那根错因柱子只能告诉你"概念没吃透最多"，答不出"是哪一张卡"。错题本的价值
    恰恰在于能指着具体某一张卡说"这里你错过三次"。
    """
    days = int(clamp(days, 1, 365))
    limit = int(clamp(limit, 1, 200))
    since = iso_after(-86400 * days)
    # 按 rowid 判先后，不按 reviewed_at：时间戳只到秒，同一秒里"先错后对"会分不出来，
    # 于是答对了的卡一直赖在错题本上。
    logs = db.query(
        "SELECT rowid AS rid, card_id, mistake_tags_json, reviewed_at, rating FROM review_logs "
        "WHERE user_id=? AND reviewed_at>=? ORDER BY rid LIMIT 4000", (user_id, since))
    per_card: dict[str, dict[str, Any]] = {}
    tag_totals: dict[str, int] = {}
    for row in logs:
        entry = per_card.setdefault(row["card_id"], {"wrongs": 0, "last_wrong_at": "", "tags": {},
                                                     "last_rid": 0, "last_rating": 4})
        entry["last_rid"] = int(row["rid"])
        entry["last_rating"] = int(row["rating"])
        if int(row["rating"]) > 2:
            continue
        entry["wrongs"] += 1
        entry["last_wrong_at"] = row["reviewed_at"]
        for tag in json_loads(row["mistake_tags_json"] or "[]", []):
            if tag in MISTAKE_KEYS:
                entry["tags"][tag] = entry["tags"].get(tag, 0) + 1
                tag_totals[tag] = tag_totals.get(tag, 0) + 1
    # 还留在本子里的只有"最近一次仍然答错"的：错题本是现在的状态，不是历史档案
    ids = [cid for cid, e in per_card.items() if e["last_rating"] <= 2]
    items: list[dict[str, Any]] = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = db.query(
            "SELECT c.*, u.title AS unit_title FROM cards c JOIN units u ON u.id=c.unit_id "
            f"WHERE c.user_id=? AND c.id IN ({placeholders})", (user_id, *ids))
        for row in rows:
            card = dict(row)
            meta = per_card[card["id"]]
            out = _public_card(card)
            out["unit_title"] = card.get("unit_title") or ""
            out["wrongs"] = meta["wrongs"]
            out["last_wrong_at"] = meta["last_wrong_at"]
            out["tag_counts"] = [{"key": k, "label": MISTAKE_LABELS.get(k, k), "count": v}
                                 for k, v in sorted(meta["tags"].items(), key=lambda x: -x[1])]
            out["due_label"] = _due_label(card)
            out["is_due"] = bool(card.get("due_at") and card["due_at"] <= now_iso())
            items.append(out)
    items.sort(key=lambda x: (-x["wrongs"], x["last_wrong_at"]), reverse=False)
    items = items[:limit]
    exam_misses = _exam_mistakes(db, user_id, since)
    due_now = sum(1 for i in items if i["is_due"])
    return {
        "days": days,
        "items": items,
        "exam_items": exam_misses,
        "totals": {
            "cards": len(items), "wrongs": sum(i["wrongs"] for i in items),
            "due_now": due_now, "exam_misses": len(exam_misses),
            "tags": [{"key": k, "label": MISTAKE_LABELS.get(k, k), "count": tag_totals.get(k, 0)}
                     for k, _ in MISTAKE_TAGS if tag_totals.get(k)],
        },
        "mistake_tags": [{"key": k, "label": v} for k, v in MISTAKE_TAGS],
    }


def _exam_mistakes(db: Database, user_id: str, since: str) -> list[dict[str, Any]]:
    """自测卷里判错的那些题：没有对应的卡，单独成列，重练时靠探测题本身。"""
    out: list[dict[str, Any]] = []
    for exam in db.query(
            "SELECT id, title, questions_json, answers_json, result_json, submitted_at "
            "FROM exams WHERE user_id=? AND status='graded' AND submitted_at>=? "
            "ORDER BY submitted_at DESC LIMIT 20", (user_id, since)):
        results = json_loads(exam["result_json"], {}) or {}
        questions = {q.get("id"): q for q in json_loads(exam["questions_json"], []) or []}
        answers = {a.get("id"): a.get("text", "") for a in json_loads(exam["answers_json"], []) or []}
        for verdict in results.get("items") or []:
            if verdict.get("verdict") == "correct":
                continue
            question = questions.get(verdict.get("id")) or {}
            out.append({
                "exam_id": exam["id"], "exam_title": exam["title"], "probe_id": verdict.get("id"),
                "prompt": question.get("prompt", ""), "unit_title": question.get("unit_title", ""),
                "answer": question.get("answer", ""), "yours": answers.get(verdict.get("id"), ""),
                "verdict": verdict.get("verdict"), "score": verdict.get("score"),
                "missed": verdict.get("missed") or [], "at": exam["submitted_at"],
            })
    return out


def review_by_ids(db: Database, user_id: str, card_ids: list[str]) -> dict[str, Any]:
    """错题本/自测卷的「就练这些」：跳过到期判断，按给定 id 出卡。

    复习队列本来只给"到期的卡"，但错题的意义恰恰是"还没到期也该再看一眼"。
    """
    # 卡片 id 里含单元标题与包名，长度不可假设：不能截断，截断就查不到还谎报"找到了"
    ids = [s for s in (str(i or "").strip() for i in (card_ids or [])) if 0 < len(s) <= 120][:60]
    if not ids:
        return {"cards": [], "picked_from": "mistakes", "requested": 0, "missing": 0}
    placeholders = ",".join("?" for _ in ids)
    rows = db.query(
        "SELECT c.*, u.title AS unit_title, u.essence AS unit_essence FROM cards c "
        f"JOIN units u ON u.id=c.unit_id WHERE c.user_id=? AND c.id IN ({placeholders})",
        (user_id, *ids))
    by_id = {r["id"]: r for r in rows}
    cards = []
    for card_id in ids:
        row = by_id.get(card_id)
        if not row:
            continue
        card = dict(row)
        card["preview"] = scheduler.preview_labels(card_from_row(card))
        card["due_label"] = "错题重练"
        card["retention"] = round4(scheduler.retrievability(float(card.get("stability") or 0),
                                                            _elapsed_days(card)))
        card["mnemonic"] = ""
        attach_mistake_tags(card, card)
        cards.append(card)
    return {"cards": cards, "picked_from": "mistakes", "requested": len(ids),
            "missing": len(ids) - len(cards)}


def cram_plan(db: Database, user_id: str, limit: int = 12) -> dict[str, Any]:
    """考前速背：离考试越近、越重要、越没掌握，越先出现在清单上；背一个斩一个。

    与 FSRS 队列是两套东西，故意不做成一套：间隔重复的目标是"长期不忘"，而考前冲刺的
    目标是"这两天别再丢分"。冲刺清单是一次性的，斩掉就不再出现，也不写回排程。
    """
    goal = active_goal(db, user_id)
    if not goal:
        return {"has_goal": False, "items": [], "days_left": None, "shot": 0, "total": 0}
    deadline = goal.get("deadline")
    days_left = int(round(days_between(now(), parse_iso(deadline)))) if deadline else None
    units = db.query(
        "SELECT * FROM units WHERE goal_id=? AND merged_into IS NULL ORDER BY order_index, id",
        (goal["id"],))
    statuses = _unit_status_map(db, user_id, units) if units else {}
    shot_rows = {r["target_id"] for r in db.query(
        "SELECT target_id FROM cram_shots WHERE user_id=? AND kind='unit'", (user_id,))}
    mnemonic_rows = {r["unit_id"]: r["content"] for r in db.query(
        "SELECT unit_id, content FROM unit_notes WHERE user_id=? AND kind='mnemonic'", (user_id,))}
    mistakes: dict[str, int] = {}
    for row in db.query(
            "SELECT c.unit_id AS unit_id, COUNT(*) AS n FROM review_logs l JOIN cards c ON c.id=l.card_id "
            "WHERE l.user_id=? AND l.rating<=2 GROUP BY c.unit_id", (user_id,)):
        mistakes[row["unit_id"]] = int(row["n"] or 0)
    items = []
    for unit in units:
        if unit["id"] in shot_rows:
            continue
        state = statuses.get(unit["id"]) or {}
        mastered = float(state.get("average") or 0.0)
        importance = int(unit.get("importance") or 3)
        urgency = 0.0
        if days_left is not None:
            urgency = clamp(1.0 - max(0, days_left) / 21.0, 0.0, 1.0)   # 三周内开始加压
        probes = db.query(
            "SELECT id, kind, prompt, answer, rubric_json FROM probes WHERE unit_id=? "
            "ORDER BY created_at, id LIMIT 3", (unit["id"],))
        score = (importance / 5.0) * 0.4 + (1.0 - mastered) * 0.35 + urgency * 0.25 \
            + min(mistakes.get(unit["id"], 0), 5) / 5.0 * 0.15
        items.append({
            "unit_id": unit["id"], "title": unit["title"],
            "essence": unit.get("essence") or "",
            "key_points": json_loads(unit.get("key_points_json") or "[]", [])[:4],
            "mnemonic": mnemonic_rows.get(unit["id"], ""),
            "probes": [{"id": p["id"], "kind": p["kind"], "prompt": p["prompt"],
                        "answer": p.get("answer") or ""} for p in probes],
            "misconceptions": json_loads(unit.get("misconceptions_json") or "[]", [])[:2],
            "mastery": round4(mastered), "mastery_label": state.get("status_label", ""),
            "heat": state.get("heat"), "due_count": int(state.get("due_count") or 0),
            "mistakes": mistakes.get(unit["id"], 0), "importance": importance,
            "estimated_minutes": int(unit.get("estimated_minutes") or 0),
            "score": round4(score),
            "reason": _cram_reason(importance, mastered, urgency, mistakes.get(unit["id"], 0)),
        })
    items.sort(key=lambda x: -x["score"])
    return {
        "has_goal": True, "goal": {"id": goal["id"], "title": goal["title"], "deadline": deadline},
        "days_left": days_left,
        "items": items[:int(clamp(limit, 1, 40))],
        "shot": len(shot_rows & {u["id"] for u in units}),
        "total": len(units), "left": len(items),
    }


def _cram_reason(importance: int, mastered: float, urgency: float, mistakes: int) -> str:
    parts = []
    if importance >= 4:
        parts.append("重要")
    if mastered < 0.4:
        parts.append("掌握度低")
    if urgency > 0.5:
        parts.append("时间紧")
    if mistakes:
        parts.append(f"错过 {mistakes} 次")
    return "、".join(parts) or "按计划该过一遍"


def cram_shot(db: Database, user_id: str, unit_id: str, undo: bool = False) -> dict[str, Any]:
    """斩掉一个速背项（或撤销这一斩）。速背是"背一个斩一个"，不是打勾打卡。"""
    unit_id = str(unit_id or "").strip()
    # id 里带单元标题的 slug，中文标题实测能到 96 字符：按 64 截断会把刚发出去的 id
    # 变成一个查不到的 id，然后报"不属于你的目标"。宁可拒绝，不要截断。
    if not unit_id or len(unit_id) > 200:
        raise ValueError("缺少或过长的 unit_id")
    if not db.one("SELECT id FROM units WHERE id=? AND goal_id IN "
                  "(SELECT id FROM goals WHERE user_id=?)", (unit_id, user_id)):
        raise ValueError("这个单元不在你的目标里")
    if undo:
        db.execute("DELETE FROM cram_shots WHERE user_id=? AND kind='unit' AND target_id=?",
                   (user_id, unit_id))
        emit(db, "cram.unshot", {"unit_id": unit_id}, user_id)
        return {"unit_id": unit_id, "shot": False}
    db.execute(
        "INSERT INTO cram_shots (id, user_id, kind, target_id, shot_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(user_id, kind, target_id) DO NOTHING",
        (new_id("cs"), user_id, "unit", unit_id, now_iso()))
    emit(db, "cram.shot", {"unit_id": unit_id}, user_id)
    return {"unit_id": unit_id, "shot": True}


def cram_reset(db: Database, user_id: str) -> dict[str, Any]:
    removed = db.execute("DELETE FROM cram_shots WHERE user_id=? AND kind='unit'", (user_id,)).rowcount
    emit(db, "cram.reset", {"removed": removed}, user_id)
    return {"removed": int(removed or 0)}


# ---------------------------------------------------------------------------
# 限时自测卷：跨单元组卷 + 判分 + 错题进本子
# ---------------------------------------------------------------------------
EXAM_MINUTES = (5, 180)
EXAM_COUNT = (3, 25)


def exam_build(db: Database, user_id: str, minutes: int = 30, count: int = 8,
               unit_ids: Any = None) -> dict[str, Any]:
    """从已有探测题里组一份跨单元卷子。题目一律来自材料编译的产物，不现编。

    没学过的单元不出题：一卷全是没见过的题，除了打击人没有任何诊断价值。
    """
    goal = active_goal(db, user_id)
    if not goal:
        raise ValueError("还没有目标：先开一门再组卷")
    minutes = int(clamp(int(minutes or 30), EXAM_MINUTES[0], EXAM_MINUTES[1]))
    count = int(clamp(int(count or 8), EXAM_COUNT[0], EXAM_COUNT[1]))
    wanted = {str(u).strip() for u in (unit_ids or []) if str(u).strip()}
    statuses = _unit_status_map(db, user_id, db.query(
        "SELECT * FROM units WHERE goal_id=? AND merged_into IS NULL", (goal["id"],))) or {}
    touched = {uid for uid, st in statuses.items() if (st or {}).get("status") != "new"}
    rows = db.query(
        "SELECT p.id, p.unit_id, p.kind, p.prompt, p.answer, p.rubric_json, u.title AS unit_title "
        "FROM probes p JOIN units u ON u.id=p.unit_id "
        "WHERE u.goal_id=? AND u.merged_into IS NULL ORDER BY u.order_index, p.created_at, p.id",
        (goal["id"],))
    pool = [r for r in rows if (not wanted or r["unit_id"] in wanted)]
    fresh = [r for r in pool if r["unit_id"] in touched]
    picked_units: dict[str, int] = {}
    questions: list[dict[str, Any]] = []
    # 学过的单元排前面（一卷全是没学过的题没有诊断价值），但不够 count 时用其余单元补满，
    # 否则一门只学了两个单元就永远出不了 8 张题
    ordered = [r for r in pool if r["unit_id"] in touched] + [r for r in pool if r["unit_id"] not in touched]
    for source in ordered:
        if picked_units.get(source["unit_id"], 0) >= 2:      # 每个单元最多 2 题，逼出跨单元覆盖
            continue
        if any(q["prompt"] == source["prompt"] for q in questions):
            continue
        picked_units[source["unit_id"]] = picked_units.get(source["unit_id"], 0) + 1
        questions.append({
            "id": source["id"], "unit_id": source["unit_id"], "unit_title": source["unit_title"],
            "kind": source["kind"], "prompt": source["prompt"],
            "answer": source.get("answer") or "",
            "rubric": json_loads(source.get("rubric_json") or "[]", []),
        })
        if len(questions) >= count:
            break
    if not questions:
        raise ValueError("这些单元里还没有可用的探测题：先去学一遍，让系统把题面生成出来")
    exam_id = new_id("exam")
    ts = now_iso()
    key = [{"id": q["id"], "answer": q["answer"], "rubric": q["rubric"]} for q in questions]
    public = [{k: q[k] for k in ("id", "unit_id", "unit_title", "kind", "prompt")} for q in questions]
    title = f"{goal['title']} · 自测卷"
    with db.transaction():
        db.insert("exams", {
            "id": exam_id, "user_id": user_id, "goal_id": goal["id"], "title": title,
            "minutes": minutes, "questions_json": json_dumps(public),
            "answer_key_json": json_dumps(key), "status": "open",
            "graded_by": "rules", "started_at": ts,
        })
        emit(db, "exam.started", {"exam_id": exam_id, "count": len(public),
                                  "minutes": minutes, "cold_start": not bool(fresh)}, user_id)
    return {
        "exam_id": exam_id, "title": title, "minutes": minutes, "questions": public,
        "deadline": goal.get("deadline"),
        "note": "" if fresh else "这些单元都还没开始学：这份卷子当练习用，别当诊断。",
    }


def exam_grade(db: Database, user_id: str, exam_id: str, answers: Any) -> dict[str, Any]:
    """按评分要点判分：能规则判的就规则判，没有要点的题如实标"待自己对答案"。

    不拿"看起来像"冒充"判过了"：判不动的题必须让人自己看答案，这是自测卷唯一的诚信要求。
    """
    exam = db.one("SELECT * FROM exams WHERE id=? AND user_id=?", (str(exam_id or "").strip(), user_id))
    if not exam:
        raise ValueError("找不到这份卷子")
    if exam["status"] == "graded":
        raise ValueError("这份卷子已经交过了：重新组一份新的")
    questions = json_loads(exam["questions_json"], []) or []
    key = {k["id"]: k for k in json_loads(exam["answer_key_json"], []) or []}
    given = {}
    for item in (answers or []):
        if isinstance(item, dict):
            given[str(item.get("id"))] = str(item.get("text") or "")
    ts = now_iso()
    items = []
    counts = {"correct": 0, "partial": 0, "wrong": 0, "blank": 0, "ungraded": 0}
    for question in questions:
        entry = key.get(question["id"]) or {}
        rubric = [str(x) for x in (entry.get("rubric") or []) if str(x).strip()]
        text = (given.get(question["id"]) or "").strip()[:4000]
        if not text:
            verdict, score, missed = "blank", 0.0, list(rubric)
        elif not rubric:
            verdict, score, missed = "ungraded", None, []
        else:
            ratio, _hits, missed = coverage(text, rubric)
            score = round4(ratio)
            verdict = "correct" if ratio >= 0.75 else ("partial" if ratio >= 0.35 else "wrong")
        counts[verdict] += 1
        items.append({
            "id": question["id"], "verdict": verdict, "score": score,
            "missed": [m for m in missed][:6],
            "answer": entry.get("answer") or "", "prompt": question.get("prompt", ""),
            "unit_title": question.get("unit_title", ""),
        })
    gradable = [i for i in items if i["score"] is not None]
    total = round4(sum(float(i["score"]) for i in gradable) / len(gradable)) if gradable else None
    result = {
        "items": items, "counts": counts, "score": total,
        "gradable": len(gradable), "needs_self_check": counts["ungraded"],
        "graded_by": "rules",
        "hint": ("评分要点来自资料；" + (f"{counts['ungraded']} 道题没有要点，需要你自己对答案。"
                                        if counts["ungraded"] else "全部题目都按要点判过。")),
    }
    with db.transaction():
        db.execute(
            "UPDATE exams SET status='graded', answers_json=?, result_json=?, submitted_at=?, "
            "graded_by='rules' WHERE id=?",
            (json_dumps([{"id": q["id"], "text": given.get(q["id"], "")} for q in questions]),
             json_dumps(result), ts, exam["id"]))
        for item in items:
            question = next((q for q in questions if q["id"] == item["id"]), None)
            if not question:
                continue
            text = (given.get(question["id"]) or "").strip()
            if not text:
                continue
            db.insert("attempts", {
                "id": new_id("at"), "user_id": user_id, "unit_id": question["unit_id"],
                "probe_id": question["id"], "kind": "exam", "response": text[:4000],
                "score": float(item["score"]) if item["score"] is not None else 0.0,
                "confidence": 0.0, "hints_used": 0,
                "feedback_json": json_dumps({"source": "exam", "exam_id": exam["id"],
                                             "verdict": item["verdict"], "missed": item["missed"]}),
                "created_at": ts,
            })
        used = int(clamp((now() - parse_iso(exam["started_at"])).total_seconds() / 60.0, 0, 600))
        touch_study_day(db, user_id, minutes=used)
        emit(db, "exam.graded", {"exam_id": exam["id"], "score": total, "counts": counts}, user_id)
    return {"exam_id": exam["id"], "title": exam["title"], "result": result,
            "answers": [{"id": q["id"], "text": given.get(q["id"], ""),
                         "prompt": q.get("prompt", ""), "unit_title": q.get("unit_title", ""),
                         "answer": (key.get(q["id"]) or {}).get("answer", "")} for q in questions]}


def exam_list(db: Database, user_id: str, limit: int = 20) -> dict[str, Any]:
    rows = db.query(
        "SELECT id, title, minutes, status, started_at, submitted_at, result_json, questions_json "
        "FROM exams WHERE user_id=? ORDER BY started_at DESC LIMIT ?",
        (user_id, int(clamp(limit, 1, 50))))
    out = []
    for row in rows:
        result = json_loads(row["result_json"], {}) or {}
        out.append({
            "id": row["id"], "title": row["title"], "minutes": row["minutes"],
            "status": row["status"], "started_at": row["started_at"],
            "submitted_at": row["submitted_at"],
            "count": len(json_loads(row["questions_json"], []) or []),
            "score": result.get("score"), "counts": result.get("counts"),
        })
    return {"exams": out}


# ---------------------------------------------------------------------------
# 洞察
# ---------------------------------------------------------------------------
CALIBRATION_BUCKETS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
CALIBRATION_MIN = 20          # 少于这个样本量谈「准不准」是自欺欺人


def calibration(db: Database, user_id: str, limit: int = 3000) -> dict[str, Any]:
    """模型预测「此刻你还想得起来吗」和实际想得起来吗，对得上吗。

    只统计 `state_before='review'` 且 `retention_before>0` 的复习：新卡和学习步里模型
    压根没做预测（内容还在眼前），混进来会让曲线虚高，看起来非常准。
    这是本项目对「学习效果」能给出的第一份真数据 —— 在此之前，所有保留率都是公式自己说的。
    """
    rows = db.query(
        "SELECT rating, retention_before FROM review_logs "
        "WHERE user_id=? AND state_before='review' AND retention_before>0 "
        "ORDER BY reviewed_at DESC LIMIT ?", (user_id, limit))
    total = len(rows)
    buckets = []
    for index, (low, high) in enumerate(CALIBRATION_BUCKETS):
        last = index == len(CALIBRATION_BUCKETS) - 1
        picked = [r for r in rows
                  if float(r["retention_before"]) >= low
                  and (float(r["retention_before"]) < high or (last and float(r["retention_before"]) <= 1.0))]
        n = len(picked)
        predicted = sum(float(r["retention_before"]) for r in picked) / n if n else None
        actual = (sum(1 for r in picked if int(r["rating"]) >= 3) / n) if n else None
        buckets.append({
            "label": f"{int(low * 100)}–{int(high * 100)}%",
            "samples": n,
            "predicted": round4(predicted) if n else None,
            "actual": round4(actual) if n else None,
            "gap": round4(actual - predicted) if n >= 3 else None,
        })
    if not total:
        mean_predicted = mean_actual = bias = brier = None
    else:
        mean_predicted = sum(float(r["retention_before"]) for r in rows) / total
        mean_actual = sum(1 for r in rows if int(r["rating"]) >= 3) / total
        bias = mean_predicted - mean_actual        # 正数＝模型偏乐观
        brier = sum((float(r["retention_before"]) - (1 if int(r["rating"]) >= 3 else 0)) ** 2
                    for r in rows) / total
    enough = total >= CALIBRATION_MIN
    if not total:
        verdict = "还没有可比的复习：每次复习前模型都会预测「你想得起来的概率」，答完就有了一条样本。"
    elif not enough:
        verdict = f"再攒 {CALIBRATION_MIN - total} 次复习才谈得上准不准（现在 {total} 次，波动太大）。"
    elif bias > 0.12:
        verdict = "模型偏乐观：它以为你想得起来，实际想不起来 —— 间隔排得太长了，该把目标保留率调高或别拖复习。"
    elif bias < -0.12:
        verdict = "模型偏保守：你以为会忘的其实还记得 —— 可以把间隔拉长，少复习几次。"
    else:
        verdict = "预测和实际基本对得上：这套间隔对你的记忆曲线是可信的。"
    return {
        "samples": total,
        "enough": enough,
        "min_samples": CALIBRATION_MIN,
        "buckets": buckets,
        "predicted": round4(mean_predicted) if total else None,
        "actual": round4(mean_actual) if total else None,
        "bias": round4(bias) if total else None,
        "brier": round4(brier) if total else None,
        "verdict": verdict,
        "requested_retention": round4(scheduler.request_retention),
    }


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
    # 记忆健康度：全部已学卡片「此刻」的平均可提取性，与 30 天保留率（历史成功率）不同
    learned = db.query("SELECT stability, last_review FROM cards "
                       "WHERE user_id=? AND state != 'new' AND stability > 0", (user_id,))
    memory_health = round4(sum(
        scheduler.retrievability(float(c["stability"] or 0), _elapsed_days(c))
        for c in learned
    ) / len(learned)) if learned else None
    # 刚复习完的卡 R 必然接近 1，于是「记忆健康度 100%」可以在人啥都没记住的当天刷出来。
    # 再给一个把当天复习过的卡剔掉的口径：它不漂亮，但更接近"放一晚上还剩多少"。
    day_ago = now() - timedelta(days=1)
    settled = [c for c in learned
               if c["last_review"] and parse_iso(str(c["last_review"])) < day_ago]
    memory_health_settled = round4(sum(
        scheduler.retrievability(float(c["stability"] or 0), _elapsed_days(c))
        for c in settled
    ) / len(settled)) if settled else None
    # 错因分布：最近 200 次复习里主动标记的错因，按标签聚合并带上样本量
    tag_rows = db.query("SELECT mistake_tags_json FROM review_logs WHERE user_id=? "
                        "ORDER BY reviewed_at DESC LIMIT 200", (user_id,))
    tally: dict[str, int] = {}
    tagged_reviews = 0
    for r in tag_rows:
        keys = [t for t in json_loads(r.get("mistake_tags_json") or "[]", []) if t in MISTAKE_KEYS]
        if not keys:
            continue
        tagged_reviews += 1
        for k in keys:
            tally[k] = tally.get(k, 0) + 1
    mistake_summary = [{"key": k, "label": MISTAKE_LABELS[k], "count": tally[k]}
                       for k, _ in MISTAKE_TAGS if tally.get(k)]
    lapses = db.scalar("SELECT COALESCE(SUM(missed_points), 0) FROM review_logs "
                       "WHERE user_id=? AND rating<=2", (user_id,), 0) or 0
    return {
        "goal": goal,
        "mistakes": {"tags": mistake_summary, "tagged_reviews": tagged_reviews,
                     "missed_total": int(lapses), "sample": len(tag_rows)},
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
        "memory_health": memory_health,
        "memory_health_settled": memory_health_settled,
        "settled_cards": len(settled),
        "calibration": calibration(db, user_id),
        "learned_cards": len(learned),
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
                    documents: list[Any] | None = None,
                    coverage: str = "standard") -> dict[str, Any]:
    merged, docs = merge_material_documents(documents, text)
    if len(merged) < 60:
        raise ValueError("材料太短，至少提供 60 个字符，才能切出可学习的单元。")
    pack, warnings = compile_material(title, subject, merged, use_llm=use_llm,
                                      use_embeddings=use_embeddings, coverage=coverage)
    qa = quality.qa_pack(pack)
    warnings = warnings + qa["warnings"]
    if docs:
        pack.setdefault("source", {})["documents"] = [
            {"name": d["name"], "chars": d["chars"]} for d in docs
        ]
        pack["source"]["material_chars"] = len(merged)
    save_user_pack(pack)
    return {"pack": pack, "summary": pack_summary(pack), "warnings": warnings,
            # 覆盖档只在模型路径生效：本地编译器按小节一条一节，拧档位不改变任何东西，
            # 所以要把"请求的档位"和"真正生效的档位"分开返回，界面才不能说谎
            "coverage": {"requested": coverage,
                         "applied": str((pack.get("source") or {}).get("coverage_mode") or "")},
            "quality": {"issues": qa["issues"], "repaired": qa["repaired"],
                        "near_duplicates": qa["near_duplicates"],
                        "engine": qa["engine"]}}


# ---------------------------------------------------------------------------
# 搜索 / 导出 / 重置
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 专注（番茄钟）：开始不落库，结束才记；完成才计入学习时长
# ---------------------------------------------------------------------------
FOCUS_MINUTES = (15, 25, 45)


def focus_start(minutes: int = 25, unit_id: str | None = None) -> dict[str, Any]:
    minutes = int(clamp(int(minutes or 25), 5, 120))
    return {"session": new_id("fs"), "minutes": minutes,
            "unit_id": unit_id or None, "started_at": now_iso()}


def focus_finish(db: Database, user_id: str, session: str, minutes: int,
                 unit_id: str | None = None, outcome: str = "completed") -> dict[str, Any]:
    """结束一次专注。会话 id 同时是事件 id，重复提交不会把时长记两遍。

    只有 completed 才计入 study_days：把 15 分钟的半截算成 25 分钟，热力图就成假的了。
    """
    session = str(session or "").strip()[:200]
    if not session:
        raise ValueError("缺少专注会话标识")
    outcome = "completed" if outcome == "completed" else "abandoned"
    minutes = int(clamp(int(minutes or 0), 1, 240))
    eid = f"fo_{session}"
    if db.one("SELECT id FROM events WHERE id=?", (eid,)):
        return {"duplicate": True, **focus_today(db, user_id)}
    with db.transaction():
        if outcome == "completed":
            touch_study_day(db, user_id, minutes=minutes, sessions=1)
        db.insert("events", {
            "id": eid, "user_id": user_id, "type": "focus.session",
            "payload_json": json_dumps({"session": session, "minutes": minutes,
                                        "outcome": outcome, "unit_id": unit_id or None}),
            "created_at": now_iso(),
        })
    return {"duplicate": False, "outcome": outcome, "minutes": minutes,
            **focus_today(db, user_id)}


def focus_today(db: Database, user_id: str) -> dict[str, Any]:
    start = _day_start_iso(_local_today())
    rows = db.query("SELECT payload_json FROM events WHERE user_id=? AND type='focus.session' "
                    "AND created_at>=?", (user_id, start))
    done = []
    for r in rows:
        p = json_loads(r["payload_json"], {}) or {}
        if p.get("outcome") == "completed":
            done.append({"minutes": int(p.get("minutes") or 0),
                         "unit_id": p.get("unit_id")})
    return {"sessions": len(done), "minutes": sum(d["minutes"] for d in done),
            "recent": done[-5:]}


def search(db: Database, user_id: str, q: str) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"units": [], "packs": [], "engine": embeddings.MODEL}
    goal = active_goal(db, user_id)
    units = get_goal_units(db, goal["id"]) if goal else []
    qlow = q.lower()
    lexical: list[str] = []
    for u in units:
        hay = (str(u.get("title") or "") + " " + str(u.get("essence") or "") + " " +
               " ".join(str(x) for x in (u.get("tags") or []))).lower()
        if qlow in hay:
            lexical.append(u["id"])
    semantic = embeddings.rank(q, units, embeddings.unit_text)
    engine = embeddings.last_engine()
    min_score = 0.20 if engine != embeddings.LOCAL_MODEL else 0.06
    kept = [(score, u) for score, u in semantic if score >= min_score]
    sem_ids = [u["id"] for _, u in kept]
    sem_by_id = {u["id"]: score for score, u in kept}
    # 分级排序：双命中 > 只字面命中 > 只语义相近；同级再按语义分与字面命中先后排。
    # 没用 RRF（testlearn 给模型检索片段时用它是合适的）：给人的搜索框里，
    # 标题精确命中的东西不该被「只是有点像」的结果挤到后面。
    lex_rank = {uid: i for i, uid in enumerate(lexical)}
    sem_rank = {uid: i for i, uid in enumerate(sem_ids)}
    by_id = {u["id"]: u for u in units}
    candidates = sorted(set(lexical) | set(sem_ids),
                        key=lambda uid: (
                            0 if (uid in lex_rank and uid in sem_rank) else
                            (1 if uid in lex_rank else 2),
                            -sem_by_id.get(uid, 0.0),
                            lex_rank.get(uid, 10_000), sem_rank.get(uid, 10_000)))[:20]
    found = []
    for uid in candidates:
        u = by_id.get(uid)
        if not u:
            continue
        matched = [m for m, hit in (("lexical", uid in lex_rank),
                                    ("semantic", uid in sem_rank)) if hit]
        score = max(sem_by_id.get(uid, 0.0), 1.0 if uid in lex_rank else 0.0)
        found.append({
            "id": u["id"], "title": u.get("title"), "essence": u.get("essence"),
            "tags": u.get("tags") or [], "goal_id": u["goal_id"],
            "score": round(score, 3),
            "matched_by": matched,
        })
    packs = [pack_summary(p) for p in all_available_packs()
             if q.lower() in (p.get("name", "") + p.get("description", "") + " ".join(p.get("tags", []))).lower()]
    return {"units": found, "packs": packs[:8], "fusion": "tiered",
            "engine": engine if units else embeddings.active_model()}


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


RESET_USER_TABLES = ("sessions", "explanations", "attempts", "evidence", "mastery",
                     "review_logs", "cards", "boil_records", "unit_notes", "notes",
                     "events", "study_days")


def reset_user(db: Database, user_id: str = config.DEFAULT_USER,
               commit: bool = True) -> None:
    """清空这个用户的数据。

    commit=False 供替换导入使用：清库和新写入必须同生共死，否则导入中途报错就
    只剩下一个被清空的库（快照躺在 data/backups 里，但没有任何一键回来的入口）。
    """
    goal_ids = [r["id"] for r in db.query("SELECT id FROM goals WHERE user_id=?", (user_id,))]

    def apply() -> None:
        db.execute("DELETE FROM messages WHERE session_id IN "
                   "(SELECT id FROM sessions WHERE user_id=?)", (user_id,))
        if goal_ids:
            ph = ",".join("?" for _ in goal_ids)
            db.execute(f"DELETE FROM probes WHERE unit_id IN "
                       f"(SELECT id FROM units WHERE goal_id IN ({ph}))", tuple(goal_ids))
            for table in ("relations", "units", "plans", "goal_packs"):
                db.execute(f"DELETE FROM {table} WHERE goal_id IN ({ph})", tuple(goal_ids))
            db.execute(f"DELETE FROM goals WHERE id IN ({ph})", tuple(goal_ids))
        for table in RESET_USER_TABLES:
            db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))

    if commit:
        with db.transaction():
            apply()
    else:
        apply()
    emit(db, "data.reset", {}, user_id)
