"""费曼讲解评估。

费曼不是“总结”，而是一次有对象、有评分标准、有缺口报告的公开输出。
真实模型做语义评分；演示模式做关键词覆盖 + 结构/边界/类比启发式评分，
并明确标注“演示模式”，不伪装成语义判断。
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import heat, llm
from .db import Database
from .events import emit, touch_study_day
from .mastery import add_evidence
from .utils import coverage, new_id, now_iso, split_cn

MODES = {
    "child": {"label": "讲给 10 岁小孩", "hint": "不用术语，用生活里的东西打比方。"},
    "peer": {"label": "讲给同学", "hint": "说清因果、步骤和关键条件。"},
    "examiner": {"label": "讲给阅卷人", "hint": "定义、条件、步骤、边界都要精确。"},
}
DIMENSIONS = [
    ("correctness", "正确性", 0.35),
    ("completeness", "完整性", 0.25),
    ("structure", "结构", 0.15),
    ("simplicity", "通俗度", 0.10),
    ("boundary", "边界", 0.10),
    ("analogy", "类比", 0.05),
]
DIM_LABELS = {k: label for k, label, _ in DIMENSIONS}
DIM_WEIGHTS = {k: w for k, _, w in DIMENSIONS}


def _unit_context(unit: dict[str, Any]) -> str:
    return (
        f"单元：{unit.get('title')}\n"
        f"本质：{unit.get('essence')}\n"
        f"步骤：{json.dumps(unit.get('steps') or [], ensure_ascii=False)}\n"
        f"边界：{json.dumps(unit.get('boundaries') or [], ensure_ascii=False)}\n"
        f"常见误解：{json.dumps(unit.get('misconceptions') or [], ensure_ascii=False)}\n"
        f"关键点：{json.dumps(unit.get('key_points') or [], ensure_ascii=False)}"
    )


def _key_points(unit: dict[str, Any]) -> list[str]:
    kps = [k for k in (unit.get("key_points") or []) if k and len(k) >= 2]
    if kps:
        return kps[:8]
    raw = [unit.get("essence", "")] + (unit.get("steps") or []) + (unit.get("boundaries") or [])
    out = []
    for r in raw:
        out.extend(split_cn(str(r), 3))
    seen = set()
    result = []
    for x in out:
        if 2 <= len(x) <= 30 and x not in seen:
            seen.add(x)
            result.append(x)
    return result[:8]


def _sentence_stats(text: str) -> tuple[list[str], list[str], float]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if s.strip()]
    avg_len = sum(len(s) for s in sentences) / len(sentences) if sentences else 0.0
    return paragraphs, sentences, avg_len


def _jargon(text: str, key_points: list[str]) -> list[str]:
    out = []
    for k in key_points:
        if len(k) < 3 or k not in text:
            continue
        idx = text.find(k)
        window = text[max(0, idx - 8): idx + len(k) + 24]
        explained = any(w in window for w in ("就是", "是指", "意思是", "也就是说", "换句话说", "即"))
        if not explained and len(out) < 5:
            out.append(k)
    return out


def _heuristic(unit: dict[str, Any], text: str, mode: str) -> dict[str, Any]:
    kps = _key_points(unit)
    cov, hits, missed = coverage(text, kps)
    paragraphs, sentences, avg_len = _sentence_stats(text)
    causal = sum(1 for w in ("因为", "所以", "因此", "由于", "导致", "意味着",
                             "首先", "然后", "最后", "如果", "但是") if w in text)
    boundary_terms = ("如果", "条件", "不适用", "反例", "边界", "除非", "前提", "失效", "只有")
    boundary_hits = sum(1 for w in boundary_terms if w in text)
    analogy_hit = any(w in text for w in ("就像", "好比", "如同", "类比", "就像一", "像一"))
    length_factor = min(0.12, len(text) / 1600)
    correctness = max(0.0, min(1.0, 0.35 + cov * 0.6 + length_factor))
    completeness = max(0.0, min(1.0, cov))
    structure = max(0.0, min(1.0, 0.35 + causal * 0.07 + min(len(paragraphs), 4) * 0.05))
    simplicity = max(0.0, min(1.0, 1.0 - max(0.0, avg_len - 26) / 70))
    if mode == "child" and any(k in text for k in ("术语", "定义", "定理")) and not analogy_hit:
        simplicity = max(0.0, simplicity - 0.12)
    boundary = max(0.0, min(1.0, 0.2 + boundary_hits * 0.16 + (0.12 if "反例" in text else 0.0)))
    analogy = 0.75 if analogy_hit else (0.45 if mode == "child" else 0.3)
    scores = {
        "correctness": round(correctness, 4),
        "completeness": round(completeness, 4),
        "structure": round(structure, 4),
        "simplicity": round(simplicity, 4),
        "boundary": round(boundary, 4),
        "analogy": round(analogy, 4),
    }
    overall = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)
    gaps = missed[:5]
    if boundary < 0.5:
        gaps.append("没有说清适用条件或失效场景")
    if not analogy_hit and mode == "child":
        gaps.append("对 10 岁小孩的讲解里缺少一个生活化类比")
    gaps = list(dict.fromkeys(gaps))[:5]
    jargon = _jargon(text, kps)
    model_answer = (
        f"本质：{unit.get('essence', '')}\n"
        + ("步骤：\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(unit.get("steps") or [])) + "\n"
           if unit.get("steps") else "")
        + ("边界：\n" + "\n".join(f"- {b}" for b in (unit.get("boundaries") or []))
           if unit.get("boundaries") else "")
    ).strip()
    followups = []
    probes = unit.get("probes") or []
    for p in probes:
        if p.get("prompt"):
            followups.append(p["prompt"])
        if len(followups) >= 3:
            break
    if not followups:
        followups = [
            "你能举一个它不适用的反例吗？",
            "如果去掉其中一个条件，会发生什么？",
            "用一句话向完全没学过的人解释它。",
        ]
    next_step = (
        f"再讲一次，重点补上「{gaps[0]}」。"
        if gaps else "把这段讲解压缩到 90 秒，并补一个边界例子。"
    )
    return {
        "overall": round(overall, 4),
        "dimensions": {k: {"score": scores[k], "reason": _reason(k, scores[k], cov, boundary_hits, causal)}
                       for k in scores},
        "gaps": gaps,
        "jargon": jargon,
        "model_answer": model_answer,
        "followups": followups,
        "next_step": next_step,
        "engine": "demo",
        "note": "演示模式：按关键点覆盖、结构与边界关键词估算；配置真实模型后会做语义评分。",
    }


def _reason(key: str, score: float, cov: float, boundary_hits: int, causal: int) -> str:
    if key == "correctness":
        return "关键点覆盖较高" if score >= 0.7 else "关键点覆盖不足，可能有遗漏或错误"
    if key == "completeness":
        return f"覆盖了约 {int(round(cov * 100))}% 的关键点"
    if key == "structure":
        return f"检测到 {causal} 个因果/结构连接词"
    if key == "simplicity":
        return "句子较短，较易理解" if score >= 0.7 else "句子偏长或术语偏多"
    if key == "boundary":
        return f"提到 {boundary_hits} 个边界/条件标志" if boundary_hits else "几乎没有谈适用条件"
    return "包含类比" if score >= 0.6 else "缺少有效类比"


def _normalize(data: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("评分结果不是对象")
    dims = {}
    scores = {}
    for key, label, _ in DIMENSIONS:
        raw = (data.get("dimensions") or {}).get(key)
        if isinstance(raw, dict):
            score = raw.get("score", 0)
            reason = raw.get("reason", "")
        else:
            score, reason = raw, ""
        try:
            score = max(0.0, min(1.0, float(score or 0)))
        except (TypeError, ValueError):
            score = 0.0
        dims[key] = {"score": round(score, 4), "reason": str(reason)[:200]}
        scores[key] = score
    overall = data.get("overall")
    try:
        overall = max(0.0, min(1.0, float(overall)))
    except (TypeError, ValueError):
        overall = sum(scores[k] * DIM_WEIGHTS[k] for k in DIM_WEIGHTS)
    def list_str(key: str, n: int = 6) -> list[str]:
        return [str(x)[:240] for x in (data.get(key) or []) if str(x).strip()][:n]
    return {
        "overall": round(overall, 4),
        "dimensions": dims,
        "gaps": list_str("gaps"),
        "jargon": list_str("jargon"),
        "model_answer": str(data.get("model_answer") or unit.get("essence", ""))[:1200],
        "followups": list_str("followups", 4),
        "next_step": str(data.get("next_step") or "")[:300],
        "engine": "live",
        "note": "",
    }


def evaluate(db: Database, user_id: str, unit: dict[str, Any], text: str,
             mode: str = "peer", advice_id: str | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if len(text) < 20:
        raise ValueError("讲解太短了，至少写 20 个字，或先说 60 秒。")
    if len(text) > 8000:
        text = text[:8000]
    mode = mode if mode in MODES else "peer"
    result = None
    fallback_reason = ""
    if llm.enabled():
        from . import styles
        prompt = llm.load_prompt("feynman_eval.md")
        prompt = prompt.replace("{mode_label}", MODES[mode]["label"])
        prompt = prompt.replace("{unit_context}", _unit_context(unit))
        prompt = prompt.replace("{explanation}", text)
        prompt += styles.persona_block_for(db, unit.get("goal_id"))
        try:
            data = llm.chat_json([
                {"role": "system", "content": "你是严格的费曼讲解评分员。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.15, max_tokens=2200)
            result = _normalize(data, unit)
        except Exception as exc:
            result = None
            fallback_reason = str(exc)[:240]
    if not result:
        result = _heuristic(unit, text, mode)
        if fallback_reason:
            result["note"] = (result.get("note") or "") + " 模型输出结构不符，已回退演示评分。"
    else:
        fallback_reason = ""
    result["mode"] = mode
    result["mode_label"] = MODES[mode]["label"]
    result["unit_id"] = unit["id"]
    result["unit_title"] = unit.get("title")
    result["text"] = text
    result["created_at"] = now_iso()

    explanation_id = new_id("exp")
    db.insert("explanations", {
        "id": explanation_id,
        "user_id": user_id,
        "unit_id": unit["id"],
        "session_id": None,
        "mode": mode,
        "text": text,
        "score_json": json.dumps(result, ensure_ascii=False),
        "created_at": now_iso(),
    })
    overall = float(result.get("overall") or 0.0)
    dims = result.get("dimensions") or {}
    explain_score = 0.5 * overall + 0.5 * float((dims.get("correctness") or {}).get("score", overall))
    transfer_score = 0.6 * float((dims.get("boundary") or {}).get("score", 0.0)) + 0.4 * overall
    heat_before = heat.heat_for_unit(db, user_id, unit["id"])
    add_evidence(db, user_id, unit["id"], "explain", explain_score,
                 confidence=0.75 if result.get("engine") == "live" else 0.45,
                 weight=0.9, source="feynman", attempt_id=explanation_id)
    add_evidence(db, user_id, unit["id"], "transfer", transfer_score,
                 confidence=0.65 if result.get("engine") == "live" else 0.4,
                 weight=0.6, source="feynman", attempt_id=explanation_id)
    heat_after = heat.heat_for_unit(db, user_id, unit["id"])
    record_mode = "benchmark" if mode == "examiner" else "feynman"
    heat.record_boil(db, user_id, unit["id"], record_mode, 4, {
        "overall": overall, "dimensions": dims, "mode": mode,
        "engine": result.get("engine"), "explanation_id": explanation_id,
    }, heat_before, heat_after, result.get("gaps") or [],
        advice_id=advice_id, accepted=1 if advice_id else 0)
    touch_study_day(db, user_id, minutes=10, sessions=1)
    emit(db, "feynman.evaluated", {
        "unit_id": unit["id"], "mode": mode, "overall": overall,
        "engine": result.get("engine"), "gaps": result.get("gaps", [])[:3],
    }, user_id)
    return result
