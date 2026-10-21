"""苏格拉底对话引擎。

不是“让模型自由聊天”，而是一个有阶段、有证据、有收束的对话状态机：
诊断 → 追问理由 → 边界 → 应用 → 综合 → 报告。
真实模型负责生成语言，策略层负责约束（一次一个问题、先答后示、提示阶梯）。
"""
from __future__ import annotations

import json
from typing import Any, Generator

from . import heat, llm
from .db import Database, json_dumps, json_loads
from .events import emit, touch_study_day
from .mastery import FACETS, add_evidence
from .packs import unit_from_row
from .utils import new_id, now_iso, parse_iso, truncate

STAGES = ["diagnose", "deepen", "boundary", "apply", "integrate", "done"]
STAGE_LABELS = {
    "diagnose": "诊断起点",
    "deepen": "追问理由",
    "boundary": "边界与反例",
    "apply": "应用迁移",
    "integrate": "综合收束",
    "done": "已收束",
}
MOCK_REPORT_NOTE = "演示模式采用关键词覆盖评估；配置真实模型后会得到更细的语义判断。"


def _unit_context(unit: dict[str, Any]) -> str:
    steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(unit.get("steps") or [])) or "（材料未给出步骤）"
    bounds = "\n".join(f"- {b}" for b in (unit.get("boundaries") or [])) or "（材料未给出边界）"
    misconceptions = "\n".join(f"- {m}" for m in (unit.get("misconceptions") or [])) or "（材料未给出）"
    key_points = "；".join(unit.get("key_points") or [])
    return (
        f"单元：{unit.get('title')}\n"
        f"一句话本质：{unit.get('essence')}\n"
        f"标准步骤：\n{steps}\n"
        f"边界：\n{bounds}\n"
        f"常见误解：\n{misconceptions}\n"
        f"评分关键点：{key_points}\n"
        f"来源：{json.dumps(unit.get('source_ref') or {}, ensure_ascii=False)}"
    )


def _stage_for_turn(turn: int) -> str:
    if turn <= 0:
        return "diagnose"
    if turn <= 2:
        return "deepen"
    if turn == 3:
        return "boundary"
    if turn == 4:
        return "apply"
    return "integrate"


def _get_unit(db: Database, unit_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM units WHERE id=?", (unit_id,))
    return unit_from_row(row) if row else None


def _get_probes(db: Database, unit_id: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM probes WHERE unit_id=? ORDER BY created_at, id", (unit_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["rubric"] = json_loads(d.pop("rubric_json", "[]"), [])
        d["hints"] = json_loads(d.pop("hints_json", "[]"), [])
        out.append(d)
    return out


def _probe_by_kind(probes: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((p for p in probes if p.get("kind") == kind), None)


def _insert_message(db: Database, session_id: str, role: str, content: str,
                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
    msg = {
        "id": new_id("msg"),
        "session_id": session_id,
        "role": role,
        "content": content,
        "meta_json": json_dumps(meta or {}),
        "created_at": now_iso(),
    }
    db.insert("messages", msg)
    return msg


def _session_row(db: Database, session_id: str) -> dict[str, Any] | None:
    return db.one("SELECT * FROM sessions WHERE id=?", (session_id,))


def _messages(db: Database, session_id: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM messages WHERE session_id=? ORDER BY created_at, id", (session_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json_loads(d.pop("meta_json", "{}"), {})
        out.append(d)
    return out


def _first_question(unit: dict[str, Any]) -> tuple[str, str]:
    """返回 (question, source)。source=live/demo，用于 UI 标注。"""
    if llm.enabled():
        system = llm.load_prompt("socratic_system.md")
        system += "\n\n# 当前知识单元\n" + _unit_context(unit)
        system += "\n\n# 当前阶段\n诊断起点（第 1 回合）。"
        try:
            text = llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": "现在开始。请先做一个简短的诊断：用一个问题确认学生对核心概念的理解起点。只问一个问题。"},
            ], model=llm.model_name(deep=True), temperature=0.4, max_tokens=300)
            return text.strip(), "live"
        except llm.LLMError:
            pass
    return (
        f"先不急着看结论。用你自己的话说说：你觉得「{unit.get('title')}」最核心的一句话是什么？",
        "demo",
    )


def start_session(db: Database, user_id: str, goal_id: str, unit_id: str,
                  advice_id: str | None = None) -> dict[str, Any]:
    unit = _get_unit(db, unit_id)
    if not unit or unit.get("goal_id") != goal_id:
        raise ValueError("单元不存在或不属于当前目标")
    session_id = new_id("ss")
    first, source = _first_question(unit)
    meta = {"stage": "diagnose", "hints": 0, "report": None, "evidence_added": False,
            "engine": source, "unit_title": unit["title"], "advice_id": advice_id}
    db.insert("sessions", {
        "id": session_id,
        "user_id": user_id,
        "goal_id": goal_id,
        "unit_id": unit_id,
        "kind": "socratic",
        "status": "active",
        "stage": "diagnose",
        "turn": 0,
        "meta_json": json_dumps(meta),
        "started_at": now_iso(),
        "ended_at": None,
    })
    _insert_message(db, session_id, "assistant", first, {"engine": source})
    emit(db, "socratic.started", {"session_id": session_id, "unit_id": unit_id,
                                  "engine": source}, user_id)
    return get_session(db, session_id)


def get_session(db: Database, session_id: str) -> dict[str, Any]:
    row = _session_row(db, session_id)
    if not row:
        raise ValueError("会话不存在")
    session = dict(row)
    session["meta"] = json_loads(session.pop("meta_json", "{}"), {})
    session["messages"] = _messages(db, session_id)
    unit = _get_unit(db, session["unit_id"]) if session.get("unit_id") else None
    return {
        "session": session,
        "unit": _unit_brief(unit) if unit else None,
        "report": session["meta"].get("report"),
        "stage_label": STAGE_LABELS.get(session["stage"], session["stage"]),
    }


def _unit_brief(unit: dict[str, Any]) -> dict[str, Any]:
    return {k: unit.get(k) for k in ("id", "title", "essence", "boundaries",
                                     "misconceptions", "key_points", "goal_id")}


def _build_llm_messages(db: Database, session: dict[str, Any], unit: dict[str, Any],
                        stage: str) -> list[dict[str, Any]]:
    system = llm.load_prompt("socratic_system.md")
    system += "\n\n# 当前知识单元\n" + _unit_context(unit)
    system += f"\n\n# 当前阶段\n{STAGE_LABELS.get(stage, stage)}（第 {session['turn'] + 1} 回合）。"
    system += "\n\n# 提醒\n这一回合只能输出一个问题。不要给完整答案。"
    msgs = [{"role": "system", "content": system}]
    history = _messages(db, session["id"])
    for m in history[-12:]:
        if m["role"] in ("user", "assistant") and m["content"].strip():
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def _mock_reply(db: Database, session: dict[str, Any], unit: dict[str, Any],
                user_text: str, turn: int) -> str:
    probes = _get_probes(db, unit["id"])
    stage = _stage_for_turn(turn)
    key_points = unit.get("key_points") or []
    hit = next((k for k in key_points if k and k in user_text), None)
    low = user_text.strip().lower()
    stuck = any(w in user_text for w in ("不知道", "不会", "没思路", "不懂", "想不出"))
    short = len(user_text.strip()) < 5

    if stuck:
        probe = _probe_by_kind(probes, "boundary" if stage == "boundary" else "explain") or (probes[0] if probes else None)
        hint = ""
        if probe and probe.get("hints"):
            hint = probe["hints"][0]
        elif unit.get("essence"):
            hint = unit["essence"][:70]
        intro = f"没关系，我们把步子缩小。提示：{hint}。" if hint else "没关系，我们换一个更小的问题。"
    elif short:
        intro = "信息还不够，我们再具体一点。"
    elif hit:
        intro = f"你提到了「{hit}」，这是关键。"
    else:
        intro = "我听到了你的想法，我们把它往前推一步。"

    if stage == "diagnose":
        q = f"用你自己的话说说，「{unit['title']}」最核心的一句话是什么？"
    elif stage == "deepen":
        if turn == 1:
            q = "为什么它成立？试着说出背后的原因，而不是只给结论。"
        else:
            q = "如果把这个条件改掉，它还会成立吗？"
    elif stage == "boundary":
        probe = _probe_by_kind(probes, "boundary")
        q = probe["prompt"] if probe else "举一个它不适用或会失效的场景。"
    elif stage == "apply":
        probe = _probe_by_kind(probes, "apply")
        q = probe["prompt"] if probe else "给一个具体场景，说明你会怎么使用它。"
    else:
        q = "最后，用一句话把它的本质、适用条件和边界串起来，你会怎么说？"
    return f"{intro}\n\n{q}"


def stream_reply(db: Database, user_id: str, session_id: str,
                 user_text: str) -> Generator[dict[str, Any], None, None]:
    """yield {type: delta|status|done|error, ...}，供 SSE 直接序列化。"""
    session_row = _session_row(db, session_id)
    if not session_row:
        yield {"type": "error", "message": "会话不存在"}
        return
    session = dict(session_row)
    if session["user_id"] != user_id:
        yield {"type": "error", "message": "无权访问该会话"}
        return
    if session["status"] != "active":
        yield {"type": "done", "session": get_session(db, session_id)["session"],
               "report": json_loads(session.get("meta_json"), {}).get("report")}
        return
    unit = _get_unit(db, session["unit_id"])
    if not unit:
        yield {"type": "error", "message": "知识单元不存在"}
        return

    user_text = (user_text or "").strip()
    if not user_text:
        yield {"type": "error", "message": "说点什么再继续"}
        return
    if len(user_text) > 4000:
        user_text = user_text[:4000]
    _insert_message(db, session_id, "user", user_text)
    turn = int(session["turn"]) + 1
    stage = _stage_for_turn(turn)
    meta = json_loads(session.get("meta_json"), {})
    if any(w in user_text for w in ("提示", "给点提示", "hint")):
        meta["hints"] = int(meta.get("hints") or 0) + 1

    # 第 6 次作答后不再追问：直接收束并生成证据报告
    if turn >= 6:
        engine = "live" if llm.enabled() else "demo"
        meta["stage"] = "done"
        meta["engine"] = engine
        db.execute(
            "UPDATE sessions SET turn=?, stage='done', meta_json=? WHERE id=?",
            (turn, json_dumps(meta), session_id),
        )
        yield {"type": "status", "text": "正在整理这次对话的证据…"}
        report = generate_report(db, user_id, session_id)
        closing = "这次对话先到这里。我把你说清楚的部分和还模糊的地方整理成了一份报告。"
        _insert_message(db, session_id, "assistant", closing, {"engine": engine, "kind": "closing"})
        yield {"type": "delta", "text": closing}
        touch_study_day(db, user_id, minutes=8, sessions=1)
        yield {"type": "done", "session": get_session(db, session_id)["session"],
               "report": report, "engine": report.get("engine", engine)}
        return

    collected: list[str] = []
    engine = "demo"
    if llm.enabled():
        engine = "live"
        try:
            messages = _build_llm_messages(db, session, unit, stage)
            for piece in llm.stream_chat(messages, model=llm.model_name(deep=True),
                                         temperature=0.55, max_tokens=450):
                collected.append(piece)
                yield {"type": "delta", "text": piece}
        except llm.LLMError as exc:
            detail = exc.to_dict()
            yield {"type": "status", "text": "真实模型不可用，已切换到本地演示引擎。"}
            collected = []
            engine = "demo"
            meta["llm_error"] = detail
    if not collected:
        reply = _mock_reply(db, session, unit, user_text, turn)
        engine = "demo"
        # 分片输出，前端仍能呈现打字感
        step = max(12, len(reply) // 12)
        for i in range(0, len(reply), step):
            piece = reply[i:i + step]
            collected.append(piece)
            yield {"type": "delta", "text": piece}
            import time
            time.sleep(0.012)
    reply = "".join(collected).strip()
    _insert_message(db, session_id, "assistant", reply, {"engine": engine, "stage": stage})
    meta["stage"] = stage
    meta["engine"] = engine
    db.execute(
        "UPDATE sessions SET turn=?, stage=?, meta_json=? WHERE id=?",
        (turn, stage, json_dumps(meta), session_id),
    )
    touch_study_day(db, user_id, minutes=8, sessions=1)
    yield {"type": "done", "session": get_session(db, session_id)["session"],
           "report": None, "engine": engine}


def finish_session(db: Database, user_id: str, session_id: str) -> dict[str, Any]:
    row = _session_row(db, session_id)
    if not row:
        raise ValueError("会话不存在")
    if row["user_id"] != user_id:
        raise PermissionError("无权访问该会话")
    report = generate_report(db, user_id, session_id)
    return {"session": get_session(db, session_id)["session"], "report": report}


def generate_report(db: Database, user_id: str, session_id: str) -> dict[str, Any]:
    row = _session_row(db, session_id)
    if not row:
        raise ValueError("会话不存在")
    meta = json_loads(row.get("meta_json"), {})
    if meta.get("report"):
        return meta["report"]
    unit = _get_unit(db, row["unit_id"])
    if not unit:
        raise ValueError("知识单元不存在")
    messages = _messages(db, session_id)
    conversation = "\n".join(
        f"{'学生' if m['role'] == 'user' else '教练'}：{m['content']}"
        for m in messages if m["content"].strip()
    )
    report = None
    if llm.enabled():
        prompt = llm.load_prompt("socratic_report.md")
        prompt = prompt.replace("{unit_context}", _unit_context(unit)).replace("{conversation}", conversation)
        try:
            data = llm.chat_json([
                {"role": "system", "content": "你是严格的学习证据评估员。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.15, max_tokens=1600)
            report = _normalize_report(data, unit)
        except Exception as exc:
            # JSON 合法但结构不符也降级；不静默假装 live
            meta["report_fallback"] = str(exc)[:240]
            report = None
    if not report:
        report = _mock_report(unit, messages)
        report["engine"] = "demo"
        if meta.get("report_fallback"):
            report["fallback_reason"] = meta["report_fallback"]
    else:
        report["engine"] = "live"
    meta["report"] = report
    meta["stage"] = "done"
    db.execute(
        "UPDATE sessions SET status='completed', stage='done', ended_at=?, meta_json=? WHERE id=?",
        (now_iso(), json_dumps(meta), session_id),
    )
    if not meta.get("evidence_added"):
        heat_before = heat.heat_for_unit(db, user_id, unit["id"])
        conf = float(report.get("confidence") or 0.5)
        for facet in FACETS:
            score = float((report.get("facets") or {}).get(facet) or 0.0)
            add_evidence(db, user_id, unit["id"], facet, score, confidence=conf,
                         weight=0.75, source="socratic", session_id=session_id)
        heat_after = heat.heat_for_unit(db, user_id, unit["id"])
        heat.record_boil(db, user_id, unit["id"], "socratic", 2, {
            "session_id": session_id, "summary": report.get("summary"),
            "facets": report.get("facets"), "engine": report.get("engine"),
        }, heat_before, heat_after, report.get("gaps") or [],
            advice_id=meta.get("advice_id"), accepted=1 if meta.get("advice_id") else 0)
        meta["evidence_added"] = True
        db.execute("UPDATE sessions SET meta_json=? WHERE id=?", (json_dumps(meta), session_id))
        emit(db, "socratic.completed", {
            "session_id": session_id, "unit_id": unit["id"],
            "report": {k: report.get(k) for k in ("summary", "facets", "confidence")},
        }, user_id)
    return report


def _normalize_report(data: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("报告不是对象")
    facets = {}
    for f in FACETS:
        try:
            facets[f] = max(0.0, min(1.0, float((data.get("facets") or {}).get(f) or 0.0)))
        except (TypeError, ValueError):
            facets[f] = 0.0
    def list_str(key: str, max_n: int = 5) -> list[str]:
        return [str(x)[:200] for x in (data.get(key) or []) if str(x).strip()][:max_n]
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "summary": str(data.get("summary") or "对话已结束。")[:600],
        "understood": list_str("understood"),
        "gaps": list_str("gaps"),
        "misconceptions": list_str("misconceptions"),
        "facets": facets,
        "next_step": str(data.get("next_step") or "用费曼法复述一次。")[:300],
        "confidence": confidence,
    }


def _mock_report(unit: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    from .utils import coverage
    user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
    key_points = unit.get("key_points") or []
    if not key_points:
        key_points = [unit.get("essence", "")[:30]]
    cov, hits, missed = coverage(user_text, key_points)
    boundary_words = ("如果", "条件", "不适用", "反例", "边界", "除非", "前提", "失效")
    causal_words = ("因为", "所以", "因此", "由于", "导致", "意味着", "首先", "然后", "最后")
    apply_words = ("例如", "比如", "应用", "用这个", "场景", "实际")
    boundary_bonus = 0.12 if any(w in user_text for w in boundary_words) else 0.0
    causal_bonus = 0.08 if any(w in user_text for w in causal_words) else 0.0
    apply_bonus = 0.1 if any(w in user_text for w in apply_words) else 0.0
    length_factor = min(0.1, len(user_text) / 1200)
    scores = {
        "recall": min(1.0, cov * 0.9 + length_factor),
        "explain": min(1.0, cov * 0.75 + causal_bonus + length_factor),
        "apply": min(1.0, cov * 0.6 + apply_bonus + length_factor),
        "transfer": min(1.0, cov * 0.55 + boundary_bonus + causal_bonus * 0.5),
    }
    understood = hits[:4] or ["你已经开始用自己的话组织这个单元了"]
    gaps = missed[:4] or []
    if not gaps:
        gaps = ["还没有在对话里说清适用边界，建议用费曼法补一次"]
    summary = (
        f"这次对话覆盖了 {len(hits)}/{len(key_points)} 个关键点。"
        + (f" 目前仍缺：{'、'.join(gaps[:2])}。" if gaps else "")
        + f" {MOCK_REPORT_NOTE}"
    )
    return {
        "summary": summary,
        "understood": understood,
        "gaps": gaps,
        "misconceptions": [],
        "facets": scores,
        "next_step": f"用 90 秒向一个没学过的人讲清「{unit.get('title')}」，重点补上：{gaps[0]}",
        "confidence": 0.42,
    }
