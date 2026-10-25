"""编译产物的质检：确定性检查优先，只有需要判断力的残差才交给模型。

理由：单元里的空字段、重复标题、把具体数字写进"方法步骤"这类问题，用规则就能判定，
没必要花一次模型调用；反过来，"这个单元和那个单元是不是一回事"才需要模型裁决。
"""
from __future__ import annotations

import re
from typing import Any

from . import decompose, embeddings, llm
from .compiler import PROBE_TEMPLATES

DUPLICATE_TITLE_RATIO = 0.82
# 阈值来自实测（BAAI/bge-m3，1024 维）：真同义改写 0.75–0.81；相关但不同的
# 「蒸发 / 沸腾」0.79；同一目标库里五个不同单元两两最高 0.72。
MERGE_LEXICAL_FLOOR = 0.62        # 字面到这个数直接放行（近乎逐字）
SEMANTIC_CANDIDATE_RATIO = 0.74   # 语义进候选带的下限；只提候选，不自动放行
JUDGE_LIMIT = 3                   # 一次请求最多请模型判几对（其余标为待判定）
NUMBER_RUN = re.compile(r"\d+(?:\.\d+)?")


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def lint_pack(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """返回问题清单。每条含 code / level / unit_id / detail / 可选 patch。"""
    issues: list[dict[str, Any]] = []
    units = pack.get("units") or []
    if not units:
        issues.append({"code": "no_units", "level": "error", "unit_id": None,
                       "detail": "内容包没有任何单元"})
    seen_titles: dict[str, str] = {}
    titles = [str(u.get("title") or "") for u in units]
    section_titles = [str(s.get("title") or "") for s in (pack.get("sections") or [])]

    def pool_of(unit: dict[str, Any]) -> list[str]:
        """候选名里必须剔掉自己：否则任何标题都能"匹配到自己"，归一化就永远不出建议。"""
        own = norm(unit.get("title"))
        return [t for t in titles if norm(t) != own] + section_titles

    for unit in units:
        title = norm(unit.get("title"))
        schedule = decompose.schedule_like(unit.get("title"))
        if schedule:
            back = decompose.canonical_title(unit.get("title"), pool_of(unit))
            issues.append({"code": "schedule_like_title", "level": "warn",
                           "unit_id": str(unit.get("id") or "") or None,
                           "detail": f"标题「{title[:24]}」不像知识点：{schedule}"
                                      + (f"；材料里更合适的名字是「{back[0]}」" if back else "")})
        else:
            # 「…的实现」「…判断问题」这类标题考的是同一个知识点，只是挂了题型尾巴
            tail = decompose.strip_type_tail(unit.get("title"))
            if tail != title:
                back = decompose.canonical_title(unit.get("title"), pool_of(unit))
                if back and norm(back[0]) != title:
                    issues.append({"code": "type_tail_title", "level": "info",
                                   "unit_id": str(unit.get("id") or "") or None,
                                   "detail": f"标题「{title[:24]}」带着题型尾巴；"
                                             f"材料里更合适的名字是「{back[0]}」（{back[1]}）"})

        uid = norm(unit.get("id")) or title
        essence = norm(unit.get("essence"))
        if not title:
            issues.append({"code": "no_title", "level": "error", "unit_id": uid,
                           "detail": "单元没有标题"})
        if title and title in seen_titles:
            issues.append({"code": "duplicate_title", "level": "warn", "unit_id": uid,
                           "detail": f"与「{seen_titles[title]}」标题完全相同",
                           "duplicate_of": seen_titles[title]})
        elif title:
            seen_titles[title] = uid
        if not essence:
            issues.append({"code": "no_essence", "level": "warn", "unit_id": uid,
                           "detail": "缺少一句话本质", "patch": {"essence": title}})
        if len(essence) > 320:
            issues.append({"code": "long_essence", "level": "info", "unit_id": uid,
                           "detail": f"本质有 {len(essence)} 字，超出建议长度",
                           "patch": {"essence": essence[:300]}})
        steps = unit.get("steps") or []
        if not steps:
            issues.append({"code": "no_steps", "level": "info", "unit_id": uid,
                           "detail": "没有步骤，探针的 explain 题会缺乏依据"})
        numeric = [s for s in steps if len(NUMBER_RUN.findall(norm(s))) >= 2]
        if numeric:
            issues.append({"code": "numeric_steps", "level": "warn", "unit_id": uid,
                           "detail": f"{len(numeric)} 条步骤里带具体数字，像是把例题当方法写了"})
        kinds = {p.get("kind") for p in (unit.get("probes") or [])}
        missing = set(PROBE_TEMPLATES) - kinds
        if missing:
            issues.append({"code": "probe_kinds", "level": "warn", "unit_id": uid,
                           "detail": "缺少探针类型：" + "、".join(sorted(missing))})
        if not unit.get("point_kind"):
            issues.append({"code": "kind_missing", "level": "info", "unit_id": uid,
                           "detail": "没有标出这是概念/原理/方法/事实里的哪一类",
                           "patch": {"point_kind": decompose.classify_kind(
                               f'{title} {essence} {" ".join(str(s) for s in steps)}')[0]}})
        ref = unit.get("source_ref") or {}
        if not (ref.get("anchor") and ref.get("id")):
            issues.append({"code": "ungrounded", "level": "info", "unit_id": uid,
                           "detail": "这条指不回材料里的具体句子，界面上会标成未溯源"})
        cards = unit.get("cards") or []
        if not cards:
            issues.append({"code": "no_cards", "level": "info", "unit_id": uid,
                           "detail": "没有复习卡，这个单元不会进入复习队列"})
        for c in cards:
            if norm(c.get("front")) == norm(c.get("back")):
                issues.append({"code": "card_same", "level": "warn", "unit_id": uid,
                               "detail": "有卡片正反面完全一样，复习时无意义"})
    return issues


def apply_safe_repairs(pack: dict[str, Any], issues: list[dict[str, Any]]
                       ) -> tuple[dict[str, Any], int]:
    """只应用确定安全的补丁；补丁等于原文时跳过（避免版本号与缓存键无意义地翻动）。"""
    by_id = {norm(u.get("id")): u for u in (pack.get("units") or [])}
    applied = 0
    for issue in issues:
        patch = issue.get("patch")
        if not patch:
            continue
        unit = by_id.get(norm(issue.get("unit_id")))
        if not unit:
            continue
        for key, value in patch.items():
            if norm(unit.get(key)) == norm(value):
                continue          # qa-skip：没有实际变化就不动，也不记为修改
            unit[key] = value
            applied += 1
    return pack, applied


def similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    overlap = len(set_a & set_b) / max(1, len(set_a | set_b))
    prefix = a[:40] == b[:40]
    return 0.99 if prefix else overlap


def lexical_score(a: dict[str, Any], b: dict[str, Any]) -> float:
    return similar(norm(a.get("title")) + norm(a.get("essence")),
                   norm(b.get("title")) + norm(b.get("essence")))


def dup_text(unit: dict[str, Any]) -> str:
    """重复判定只看标题 + 一句话本质。

    实测：把步骤/边界/要点也算进去（`unit_text`），同一主题的所有单元会挤到
    0.76–0.81，真重复（0.795）反而排在假候选（0.813）后面——分数就废了。
    只用标题+本质时：真重复 0.870，最危险的假对 0.797，中间带才有意义。
    """
    return norm(unit.get("title")) + "。" + norm(unit.get("essence"))


def semantic_pair(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """一对单元的语义相似度；云端向量不可用时返回 None。

    绝不拿本地哈希向量充当语义分：那套向量在无关文本上也能到 0.3，
    按云端的阈值判会把不相干的东西说成重复。
    """
    if not embeddings.enabled():
        return None
    vecs = embeddings.embed_texts([dup_text(a), dup_text(b)])
    if not vecs or len(vecs) != 2 or not all(vecs):
        return None
    return embeddings.cosine(vecs[0], vecs[1])


def signals(a: dict[str, Any], b: dict[str, Any],
            semantic: float | None = None) -> dict[str, Any]:
    lex = lexical_score(a, b)
    sem = semantic_pair(a, b) if semantic is None else semantic
    return {"lexical": round(lex, 3), "semantic": None if sem is None else round(sem, 3)}


def judge_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """就一对单元问一次模型：是不是同一件事。没配置模型返回 None，失败不猜。

    提示词只由这两条 title+essence 决定，所以候选列表渲染过一次之后，
    用户真的点「并入」时的复查会命中内容寻址缓存——裁决不额外花钱。
    """
    if not llm.enabled():
        return None
    # A/B 按 id 定序：候选列表和点「并入」时的复查必须用同一个问法，
    # 否则同一对会被问两次（还可能出现两种说法）。
    left, right = sorted((a, b), key=lambda u: str(u.get("id") or u.get("title") or ""))
    prompt = (f"A「{norm(left.get('title'))}」：{norm(left.get('essence'))[:200]}\n\n"
              f"B「{norm(right.get('title'))}」：{norm(right.get('essence'))[:200]}\n\n"
              "同一概念的不同说法算同一件事；一个讲原理、一个讲应用不算。\n"
              '只输出 JSON：{"same": true, "reason": "15 字内"}')
    try:
        data = llm.chat_json([
            {"role": "system", "content": "你只做重复性判断，不重写内容。只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ], model=llm.model_name(), temperature=0.1, max_tokens=200)
    except Exception as exc:
        return {"same": None, "reason": "", "error": str(exc)[:120]}
    if not isinstance(data, dict) or "same" not in data:
        return {"same": None, "reason": "", "error": "模型没有给出判定"}
    return {"same": bool(data.get("same")), "reason": norm(data.get("reason"))[:60]}


def merge_decision(known: dict[str, Any], other: dict[str, Any],
                   verdict: dict[str, Any] | None = None,
                   semantic: float | None = None) -> dict[str, Any]:
    """合并裁决：字面近乎逐字才由代码放行，其余必须模型判定，都不够就拒绝。

    实测（BAAI/bge-m3，只看标题+本质）：真重复 0.870，而「提取练习 / 测试即学习」
    这种相关但不同的能到 0.797——分数差只有 0.07，一条阈值线分不开；
    而合并会重写关系边且当前无法撤销，所以语义分只提候选，不签字。
    """
    sig = signals(known, other, semantic=semantic)
    lex, sem = sig["lexical"], sig["semantic"]
    if lex >= MERGE_LEXICAL_FLOOR:
        return {"allowed": True, "basis": "lexical", "reason": f"字面重合 {lex:.2f}", "signals": sig}
    if sem is not None and sem >= SEMANTIC_CANDIDATE_RATIO:
        if verdict and verdict.get("same") is True:
            return {"allowed": True, "basis": "model",
                    "reason": f"语义 {sem:.2f} + 模型判定同一件事：{norm(verdict.get('reason'))[:60]}",
                    "signals": sig}
        if verdict and verdict.get("same") is False:
            return {"allowed": False, "basis": "model",
                    "reason": f"模型判定不是同一件事：{norm(verdict.get('reason'))[:60]}", "signals": sig}
        trouble = norm((verdict or {}).get("error"))[:80]
        return {"allowed": False, "basis": "none",
                "reason": (f"语义 {sem:.2f} 落在需要模型确认的区间，"
                           + (f"但模型判定失败：{trouble}" if trouble else "但还没有判定结果")),
                "signals": sig}
    have = f"字面 {lex:.2f}、语义 {sem:.2f}" if sem is not None else f"字面 {lex:.2f}"
    lines = (f"字面 {MERGE_LEXICAL_FLOOR} / 语义 {SEMANTIC_CANDIDATE_RATIO}" if sem is not None
             else f"字面 {MERGE_LEXICAL_FLOOR}")
    return {"allowed": False, "basis": "none",
            "reason": f"{have} 都低于合并线（{lines}）", "signals": sig}


def find_near_duplicates(pack: dict[str, Any],
                         threshold: float = DUPLICATE_TITLE_RATIO,
                         semantic_threshold: float | None = None) -> list[dict[str, Any]]:
    """疑似重复检测：字面近乎逐字，或云端语义进入候选带。

    字面分数便宜且高精度，但抓不到同义改写；语义分数能抓到改写，代价是一次批量
    向量调用。两者都只负责**提出候选**，是不是真重复由 merge_decision 裁决。
    """
    units = pack.get("units") or []
    semantic_threshold = (SEMANTIC_CANDIDATE_RATIO if semantic_threshold is None
                          else semantic_threshold)
    sems = _pair_semantics(units) if len(units) > 1 else {}
    out = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            lex = lexical_score(units[i], units[j])
            sem = sems.get((i, j))
            if not (lex >= threshold or (sem is not None and sem >= semantic_threshold)):
                continue
            out.append({
                "keep": norm(units[i].get("id")), "merge": norm(units[j].get("id")),
                "similarity": round(max(lex, sem or 0.0), 3),
                "lexical": round(lex, 3),
                "semantic": None if sem is None else round(sem, 3),
                "engine": "semantic" if (sem or 0) >= (lex or 0) else "rules",
                "auto": lex >= MERGE_LEXICAL_FLOOR,
                "titles": [norm(units[i].get("title")), norm(units[j].get("title"))],
            })
    out.sort(key=lambda p: p["similarity"], reverse=True)
    return out


def _pair_semantics(units: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    """一次批量取全部向量再算两两余弦；云端不可用就返回空（纯字面模式）。"""
    if not embeddings.enabled():
        return {}
    vecs = embeddings.embed_texts([dup_text(u) for u in units])
    if not vecs or len(vecs) != len(units) or not all(vecs):
        return {}
    return {(i, j): embeddings.cosine(vecs[i], vecs[j])
            for i in range(len(units)) for j in range(i + 1, len(units))}


def judge_residual(pack: dict[str, Any], suspects: list[dict[str, Any]]) -> dict[str, Any]:
    """只把疑似重复交给模型判断；模型提方案，是否合并由代码按阈值裁决。"""
    if not suspects or not llm.enabled():
        return {"engine": "rules", "verdicts": [], "note": "没有需要模型判断的疑似重复"}
    by_id = {norm(u.get("id")): u for u in (pack.get("units") or [])}
    blocks = []
    for index, pair in enumerate(suspects[:12]):
        keep = by_id.get(pair["keep"]) or {}
        merge = by_id.get(pair["merge"]) or {}
        blocks.append(
            f"配对 {index + 1}（相似度 {pair['similarity']}）：\n"
            f"  A「{norm(keep.get('title'))}」：{norm(keep.get('essence'))[:160]}\n"
            f"  B「{norm(merge.get('title'))}」：{norm(merge.get('essence'))[:160]}"
        )
    prompt = (
        "下面是一份学习材料里疑似重复的知识单元配对。逐对判断 A 和 B 是不是同一件事"
        "（同一概念的不同说法算同一件事；一个讲原理、一个讲应用不算）。\n\n"
        + "\n\n".join(blocks)
        + "\n\n只输出 JSON：{\"verdicts\": [{\"index\": 1, \"same\": true, \"reason\": \"15 字内\"}]}"
    )
    try:
        data = llm.chat_json([
            {"role": "system", "content": "你只做重复性判断，不重写内容。只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ], model=llm.model_name(), temperature=0.1, max_tokens=700)
    except Exception as exc:
        return {"engine": "rules", "verdicts": [], "note": f"模型判断失败，保留现状：{str(exc)[:120]}"}
    verdicts = [v for v in (data.get("verdicts") or []) if isinstance(v, dict)] if isinstance(data, dict) else []
    return {"engine": "model", "verdicts": verdicts, "note": ""}


def qa_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """跑一遍质检：返回问题、安全修复数、疑似重复与建议。不修改 pack 之外的事物。"""
    issues = lint_pack(pack)
    pack, repaired = apply_safe_repairs(pack, issues)
    residual_issues = lint_pack(pack)
    suspects = find_near_duplicates(pack)
    judgement = judge_residual(pack, suspects) if suspects else {"engine": "rules", "verdicts": [], "note": ""}
    warnings = [f"[{i['code']}] {i['detail']}" for i in residual_issues if i["level"] == "warn"]
    return {
        "issues": residual_issues,
        "repaired": repaired,
        "near_duplicates": suspects,
        "judge": judgement,
        "warnings": warnings,
        "engine": judgement["engine"],
    }
