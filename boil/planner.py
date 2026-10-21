"""学习路径规划：前置 DAG + 时间预算 + 最少必要路径。

原则：
- 先保证前置关系，再谈顺序；
- 时间不够时，优先保留高权重单元，明确“暂存”而不是假装能学完；
- 计划是活的：每次重排从今天开始，不把错过的任务堆成山。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .utils import clamp, human_minutes, iso, now, today_str

DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _unit_minutes(unit: dict[str, Any]) -> int:
    try:
        m = int(unit.get("estimated_minutes") or 45)
    except (TypeError, ValueError):
        m = 45
    return int(clamp(m, 10, 180))


def _topo_sort(units: list[dict[str, Any]], relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {u["id"]: u for u in units}
    indeg = {uid: 0 for uid in by_id}
    edges: dict[str, list[str]] = {uid: [] for uid in by_id}
    for rel in relations:
        if (rel.get("kind") or "prerequisite") != "prerequisite":
            continue
        a, b = rel.get("from_unit"), rel.get("to_unit")
        if a in by_id and b in by_id and a != b:
            edges[a].append(b)
            indeg[b] += 1
    order_index = {uid: int(u.get("order_index") or 0) for uid, u in by_id.items()}
    ready = sorted([uid for uid, d in indeg.items() if d == 0],
                   key=lambda x: (order_index[x], str(x)))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    while ready:
        uid = ready.pop(0)
        if uid in seen:
            continue
        seen.add(uid)
        out.append(by_id[uid])
        for nxt in edges[uid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=lambda x: (order_index[x], str(x)))
    # 环：按顺序补在末尾，不让计划直接失败
    for u in sorted(units, key=lambda x: (order_index[x["id"]], str(x["id"]))):
        if u["id"] not in seen:
            out.append(u)
    return out


def _select_units(units: list[dict[str, Any]], relations: list[dict[str, Any]],
                  available_minutes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    ordered = _topo_sort(units, relations)
    required = sum(_unit_minutes(u) for u in ordered)
    if required <= available_minutes or not ordered:
        return ordered, [], "full"

    # 时间不够：先按重要度挑，再把被选中单元的前置补进来
    by_id = {u["id"]: u for u in ordered}
    prereq: dict[str, set[str]] = {u["id"]: set() for u in ordered}
    for rel in relations:
        if (rel.get("kind") or "prerequisite") != "prerequisite":
            continue
        a, b = rel.get("from_unit"), rel.get("to_unit")
        if a in by_id and b in by_id:
            prereq[b].add(a)
    priority = sorted(ordered, key=lambda u: (-(int(u.get("importance") or 3)),
                                              int(u.get("order_index") or 0)))
    chosen: set[str] = set()
    used = 0
    for u in priority:
        need = {u["id"]} | prereq[u["id"]]
        add_ids = need - chosen
        add_minutes = sum(_unit_minutes(by_id[x]) for x in add_ids)
        if used + add_minutes <= available_minutes:
            chosen |= add_ids
            used += add_minutes
    if not chosen:
        chosen = {ordered[0]["id"]}
    selected = [u for u in ordered if u["id"] in chosen]
    parked = [u for u in ordered if u["id"] not in chosen]
    return selected, parked, "minimum_viable"


def build_plan(units: list[dict[str, Any]], relations: list[dict[str, Any]],
               daily_minutes: int = 40, start: date | None = None,
               deadline: date | None = None, version: int = 1,
               goal_id: str = "", plan_id: str = "") -> dict[str, Any]:
    daily_minutes = int(clamp(int(daily_minutes or 40), 10, 240))
    start = start or datetime.now().astimezone().date()
    if deadline and deadline >= start:
        total_days = (deadline - start).days + 1
    else:
        total_days = 56
    rest_days = max(1, total_days // 7)
    study_days = max(1, total_days - rest_days)
    review_minutes = int(clamp(round(daily_minutes * 0.3), 10, 30))
    buffer_minutes = max(5, int(round(daily_minutes * 0.1)))
    new_minutes_per_day = max(15, daily_minutes - review_minutes - buffer_minutes)
    available_new_minutes = study_days * new_minutes_per_day

    selected, parked, mode = _select_units(units, relations, available_new_minutes)
    required = sum(_unit_minutes(u) for u in selected)

    days: list[dict[str, Any]] = []
    cursor = start
    idx = 0
    day_counter = 0
    while idx < len(selected) and day_counter < max(total_days, 1):
        is_rest = (day_counter % 7 == 6)
        if is_rest:
            days.append({
                "date": cursor.isoformat(),
                "weekday": DAY_NAMES[cursor.weekday()],
                "is_rest": True,
                "new_units": [],
                "new_minutes": 0,
                "review_minutes": 0,
                "total_minutes": 0,
                "focus": "休息也是学习的一部分",
            })
            cursor += timedelta(days=1)
            day_counter += 1
            continue
        day_units = []
        minutes = 0
        while idx < len(selected) and len(day_units) < 2:
            u = selected[idx]
            um = _unit_minutes(u)
            if day_units and minutes + um > new_minutes_per_day:
                break
            day_units.append({"id": u["id"], "title": u["title"],
                              "minutes": um, "importance": u.get("importance", 3)})
            minutes += um
            idx += 1
        days.append({
            "date": cursor.isoformat(),
            "weekday": DAY_NAMES[cursor.weekday()],
            "is_rest": False,
            "new_units": day_units,
            "new_minutes": minutes,
            "review_minutes": review_minutes,
            "total_minutes": minutes + review_minutes,
            "focus": day_units[0]["title"] if day_units else "复习与巩固",
        })
        cursor += timedelta(days=1)
        day_counter += 1
        if not day_units and idx < len(selected):
            # 单个单元超过日预算：仍然安排它，避免死循环
            u = selected[idx]
            days[-1]["new_units"] = [{"id": u["id"], "title": u["title"],
                                      "minutes": _unit_minutes(u),
                                      "importance": u.get("importance", 3)}]
            days[-1]["new_minutes"] = _unit_minutes(u)
            days[-1]["total_minutes"] = _unit_minutes(u) + review_minutes
            days[-1]["focus"] = u["title"]
            idx += 1

    milestones = []
    for i in range(0, len(selected), 4):
        group = selected[i:i + 4]
        if not group:
            continue
        target_id = group[-1]["id"]
        mdate = next((d["date"] for d in days
                      if any(x["id"] == target_id for x in d["new_units"])), start.isoformat())
        milestones.append({
            "title": f"阶段检查点 {i // 4 + 1}",
            "date": mdate,
            "unit_ids": [u["id"] for u in group],
            "description": "用费曼法复述本阶段核心，再做一次迁移探针。",
        })

    total_planned = sum(d["total_minutes"] for d in days)
    summary = (
        f"预计 {len(days)} 天内覆盖 {len(selected)} 个单元，"
        f"每天约 {daily_minutes} 分钟（其中复习 {review_minutes} 分钟）。"
    )
    if mode == "minimum_viable":
        summary += f" 时间只够高权重路径，已暂存 {len(parked)} 个单元；加时或延长期限可解锁。"
    if deadline:
        summary += f" 目标日期：{deadline.isoformat()}。"

    return {
        "id": plan_id or "",
        "goal_id": goal_id,
        "version": version,
        "generated_at": iso(now()),
        "strategy": {
            "mode": mode,
            "daily_minutes": daily_minutes,
            "review_minutes": review_minutes,
            "new_minutes_per_day": new_minutes_per_day,
            "required_new_minutes": required,
            "available_new_minutes": available_new_minutes,
            "planned_minutes": total_planned,
            "deadline": deadline.isoformat() if deadline else None,
            "parked": [{"id": u["id"], "title": u["title"],
                        "importance": u.get("importance", 3),
                        "minutes": _unit_minutes(u)} for u in parked],
            "summary": summary,
        },
        "days": days,
        "milestones": milestones,
    }


def today_plan(plan: dict[str, Any], day: str | None = None) -> dict[str, Any] | None:
    day = day or today_str()
    days = plan.get("days") or []
    for d in days:
        if d.get("date") == day:
            return d
    for d in days:
        if d.get("date", "") >= day:
            return d
    return days[-1] if days else None


def progress(plan: dict[str, Any], mastered_ids: set[str]) -> dict[str, Any]:
    all_units = [x["id"] for d in plan.get("days", []) for x in d.get("new_units", [])]
    done = sum(1 for uid in all_units if uid in mastered_ids)
    return {
        "total": len(all_units),
        "done": done,
        "percent": round(done / len(all_units) * 100, 1) if all_units else 0.0,
        "days": len(plan.get("days", [])),
    }


def fmt_minutes(minutes: int) -> str:
    return human_minutes(int(minutes))
