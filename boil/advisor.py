"""Advisor v0：在强度阶梯上推荐“下一档”，并解释为什么。

规则版目标不是“最优策略”，而是：
- 每场只给一个下一步；
- 理由必须能被人验证；
- 可一键否决，否决/采纳都进入事件日志，未来用于策略学习。
"""
from __future__ import annotations

from typing import Any

from . import heat
from .db import Database
from .events import emit
from .packs import get_goal_relations, get_goal_units
from .utils import clamp, new_id, now_iso, round4

MODE_META = {
    "audio": {"level": 0, "label": "听讲", "feature": "音频浸泡", "cta": "听一耳", "cost": 5,
               "desc": "建立熟悉感，不指望记住"},
    "digest": {"level": 1, "label": "阅读/视听", "feature": "预消化", "cta": "开始预消化", "cost": 8,
                "desc": "粉末化、知识地图、类比、FAQ"},
    "socratic": {"level": 2, "label": "讨论", "feature": "苏格拉底对话", "cta": "开始对话", "cost": 12,
                  "desc": "AI 问，你答；把石头打散"},
    "recall": {"level": 3, "label": "实际演练", "feature": "高频回测 + FSRS", "cta": "开始复习", "cost": 8,
                "desc": "同一档重复的时刻表"},
    "feynman": {"level": 4, "label": "教别人", "feature": "费曼对话", "cta": "开始讲解", "cost": 10,
                 "desc": "你讲，AI 装不懂；金字塔顶"},
    "benchmark": {"level": 4, "label": "非档位", "feature": "极致对标", "cta": "开始对标", "cost": 8,
                   "desc": "完整复现标准步骤，过了才算烧开"},
}

LADDER = [
    {"level": 0, "mode": "audio", "label": "听讲", "feature": "音频浸泡"},
    {"level": 1, "mode": "digest", "label": "阅读/视听", "feature": "预消化"},
    {"level": 2, "mode": "socratic", "label": "讨论", "feature": "苏格拉底对话"},
    {"level": 3, "mode": "recall", "label": "实际演练", "feature": "高频回测"},
    {"level": 4, "mode": "feynman", "label": "教别人", "feature": "费曼对话"},
]


def _goal(db: Database, user_id: str, goal_id: str | None = None) -> dict[str, Any] | None:
    if goal_id:
        row = db.one("SELECT * FROM goals WHERE id=? AND user_id=?", (goal_id, user_id))
        if row:
            return row
    return db.one(
        "SELECT * FROM goals WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )


def _due_map(db: Database, user_id: str, goal_id: str) -> dict[str, int]:
    rows = db.query(
        "SELECT u.id AS unit_id, COUNT(c.id) AS n FROM cards c "
        "JOIN units u ON u.id=c.unit_id "
        "WHERE c.user_id=? AND u.goal_id=? AND c.due_at<=? AND c.state!='new' "
        "GROUP BY u.id",
        (user_id, goal_id, now_iso()),
    )
    return {r["unit_id"]: int(r["n"]) for r in rows}


def _boiled(state: dict[str, Any]) -> bool:
    return state.get("status") == "boiled" or (
        state.get("reps", 0) >= 4
        and all((state.get("facets") or {}).get(f, {}).get("score", 0) >= 0.8 for f in
                ("recall", "explain", "apply", "transfer"))
    )


def _next_target(units: list[dict[str, Any]], states: dict[str, dict[str, Any]],
                 relations: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    by_id = {u["id"]: u for u in units}
    prereqs: dict[str, list[str]] = {}
    for rel in relations:
        if (rel.get("kind") or "prerequisite") == "prerequisite":
            prereqs.setdefault(rel["to_unit"], []).append(rel["from_unit"])
    for u in sorted(units, key=lambda x: (x.get("order_index") or 0, x["id"])):
        if _boiled(states.get(u["id"], {})):
            continue
        unmet = [p for p in prereqs.get(u["id"], []) if p in by_id and not _boiled(states.get(p, {}))]
        if unmet:
            return by_id[unmet[0]], True
        return u, False
    return None, False


def _advice(mode: str, target: dict[str, Any] | None, reason: str, minutes: int,
            state: dict[str, Any] | None = None, pn: dict[str, Any] | None = None,
            boundary: bool = False, due_total: int = 0, goal: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = MODE_META.get(mode, MODE_META["digest"])
    target_state = state or {}
    if mode == "done":
        return {
            "id": new_id("adv"), "mode": "done", "level": None, "mode_label": "完成",
            "feature": "复盘", "cta": "查看洞察", "title": "这个目标已经全部烧开",
            "reason": reason, "cost_minutes": 0, "minutes": int(minutes),
            "target": None, "heat": 0, "heat_label": "已烧开",
            "pn": pn or {"total": 0, "missing": [], "source": None, "complete": None},
            "boundary": False, "due_total": due_total, "href": "#/insights",
            "ladder": [{**x, "active": False, "done": True} for x in LADDER],
            "goal": {"id": goal.get("id"), "title": goal.get("title")} if goal else None,
            "created_at": now_iso(),
        }
    heat_value = round4(target_state.get("heat") or 0)
    href = "#/dashboard"
    if target:
        if mode in ("audio", "digest", "socratic"):
            href = f"#/unit/{target['id']}"
        elif mode == "recall":
            href = "#/review"
        elif mode == "feynman":
            href = f"#/feynman/{target['id']}"
        elif mode == "benchmark":
            href = f"#/feynman/{target['id']}?mode=examiner"
    ladder = []
    for item in LADDER:
        ladder.append({**item, "active": item["mode"] == mode,
                       "done": bool(state and state.get("heat", 0) >= 99.5)})
    if mode != "done":
        ladder.append({"level": "gate", "mode": "benchmark", "label": "非档位",
                       "feature": "极致对标", "active": mode == "benchmark", "done": False})
    return {
        "id": new_id("adv"),
        "mode": mode,
        "level": meta["level"],
        "mode_label": meta["label"],
        "feature": meta["feature"],
        "cta": meta["cta"],
        "title": f"第 {meta['level']} 档 · {meta['feature']}" if mode != "benchmark" else "极致对标 · 完整复现",
        "reason": reason,
        "cost_minutes": int(meta["cost"]),
        "minutes": int(minutes),
        "target": {
            "id": target.get("id"), "title": target.get("title"),
            "heat": heat_value, "heat_label": heat.heat_label(heat_value),
            "status": target_state.get("status", "new"),
        } if target else None,
        "heat": heat_value,
        "heat_label": heat.heat_label(heat_value),
        "pn": pn or {"total": 0, "missing": [], "source": None, "complete": None},
        "boundary": boundary,
        "due_total": due_total,
        "href": href,
        "ladder": ladder,
        "goal": {"id": goal.get("id"), "title": goal.get("title")} if goal else None,
        "created_at": now_iso(),
    }


def build_advice(db: Database, user_id: str = "local", goal_id: str | None = None,
                 minutes: int | None = None) -> dict[str, Any]:
    goal = _goal(db, user_id, goal_id)
    if not goal:
        return {
            "mode": "onboarding", "level": None, "mode_label": "开始", "feature": "选择目标",
            "title": "先架一壶水", "reason": "你还没有学习目标。选一个内容包，或把自己的材料编译成学习闭环。",
            "cta": "创建目标", "href": "#/onboarding", "cost_minutes": 0,
            "heat": 0, "heat_label": "未点火", "target": None, "due_total": 0,
            "goal": None,
            "ladder": [{**x, "active": False, "done": False} for x in LADDER],
            "pn": {"total": 0, "missing": [], "source": None, "complete": None},
            "id": new_id("adv"), "created_at": now_iso(), "minutes": minutes or 0,
        }
    minutes = int(clamp(minutes or goal.get("daily_minutes") or 30, 5, 240))
    units = get_goal_units(db, goal["id"])
    relations = get_goal_relations(db, goal["id"])
    due = _due_map(db, user_id, goal["id"])
    due_total = sum(due.values())
    states = heat.heat_bulk(db, user_id, units, due)
    if due_total > 0:
        due_unit = max(units, key=lambda u: due.get(u["id"], 0)) if units else None
        cost = min(15, max(4, due_total), max(4, minutes))
        if due_unit:
            title = due_unit["title"]
            reason = (f"有 {due_total} 张卡到期，其中「{title}」占 {due[due_unit['id']]} 张。"
                      f"不先回测，之前的记忆会继续降温；先花约 {cost} 分钟能还多少还多少，再学新的。")
            adv = _advice("recall", due_unit, reason, minutes, states.get(due_unit["id"]),
                          heat.pn_state(db, user_id, due_unit), due_total=due_total, goal=goal)
            adv["cost_minutes"] = cost
            return adv

    target, boundary = _next_target(units, states, relations)
    if not target:
        return _advice("done", None, "这个目标的所有单元都已经烧开。去洞察页复盘证据，或开启一个新目标。",
                       minutes, None, None, goal=goal)

    state = states.get(target["id"], {})
    pn = heat.pn_state(db, user_id, target)
    t = float(state.get("heat") or 0)
    name = target["title"]
    first_gap = (pn.get("missing") or [None])[0]
    if boundary:
        reason = (f"「{name}」的前置还没有烧开。硬上会在边界卡住；先降一档，把前置的知识单元预消化。"
                  "能力边界不是逃避，是先把能接住的基础长出来。")
        return _advice("digest", target, reason, minutes, state, pn, boundary=True, goal=goal)

    if minutes <= 8 and t < 40:
        reason = (f"你只有 {minutes} 分钟，塞不进一个完整闭环。先用 5 分钟听一遍「{name}」的音频稿，"
                  "建立熟悉感；正式学习时再要求自己烧开。")
        return _advice("audio", target, reason, minutes, state, pn, goal=goal)

    if t <= 0:
        reason = (f"「{name}」还没点火（T=0°）。先花约 8 分钟预消化：本质、类比、步骤、边界各过一遍，"
                  "再开始提问。不要一上来就要求自己讲给别人听。")
        return _advice("digest", target, reason, minutes, state, pn, goal=goal)

    if t < 40:
        if minutes < 12:
            reason = (f"「{name}」现在 T={round(t)}°，还没到能讲给别人听的程度；但时间只有 {minutes} 分钟，"
                      "先降一档读本质和边界，下次再进入对话。")
            return _advice("digest", target, reason, minutes, state, pn, goal=goal)
        gap_text = f"上次报告还缺「{first_gap}」。" if first_gap else "先找出你没定义的词、跳过的步骤和混淆的因果。"
        reason = (f"「{name}」现在 T={round(t)}°。{gap_text} 用苏格拉底把石头打散，"
                  "不急着上第 4 档；这一档不判烧开，只判你能不能进入实际演练。")
        return _advice("socratic", target, reason, minutes, state, pn, goal=goal)

    if t < 80:
        if pn.get("missing"):
            reason = (f"「{name}」T={round(t)}°，但上次还缺 {len(pn['missing'])} 个步骤，比如「{first_gap}」。"
                      "现在用费曼讲给一个外行听；AI 会装不懂，逼你把这一步讲圆。讲不圆就说明还没烧开。")
            return _advice("feynman", target, reason, minutes, state, pn, goal=goal)
        if t >= 65:
            reason = (f"「{name}」T={round(t)}°，四维证据已经接近达标。用阅卷人模式做一次极致对标："
                      "完整复现标准步骤，不能跳步；过了才算烧开。")
            return _advice("benchmark", target, reason, minutes, state, pn, goal=goal)
        reason = (f"「{name}」T={round(t)}°，能讲出一些，但还不够稳。先做一次苏格拉底或费曼，"
                  "把边界和反例说清；不要靠重读来假装复习。")
        return _advice("socratic", target, reason, minutes, state, pn, goal=goal)

    reason = (f"「{name}」T={round(t)}°，已经接近沸腾。用阅卷人模式完整复现标准步骤；"
              "如果还能讲出边界和反例，这壶水就烧开了。")
    return _advice("benchmark", target, reason, minutes, state, pn, goal=goal)


def feedback(db: Database, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    if action not in ("accepted", "dismissed"):
        raise ValueError("action 必须是 accepted 或 dismissed")
    advice = payload.get("advice") or {}
    if not isinstance(advice, dict):
        advice = {}
    emit(db, f"advice.{action}", {
        "advice_id": advice.get("id") or payload.get("advice_id"),
        "mode": advice.get("mode"),
        "level": advice.get("level"),
        "target": advice.get("target"),
        "reason": advice.get("reason"),
        "heat": advice.get("heat"),
        "minutes": advice.get("minutes"),
    }, user_id)
    return {"ok": True, "action": action, "advice_id": advice.get("id") or payload.get("advice_id")}
