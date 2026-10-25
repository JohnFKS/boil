"""拆解口径、溯源与分层：把"这像不像一个知识点"变成能执行的检查。

设计来源（都在读完整实现之后取舍）：
- kaobuddy 的提示词里有三条真正懂备考的口径规则（知识点 ≠ 学习安排、题型标题要回归知识点、
  目标分数决定覆盖深度），但它只写在提示词里，模型不遵守时没有任何东西拦一下。
  这里把它们变成 lint 与匹配函数。
- GraphRAG / Tree-KG 的出处做法：每条断言都带**指向原文的引用**，可机械核对，
  而不是让模型自报一句"证据"。所以锚点必须逐字命中原文才算数。
- RAPTOR 的递归分层与软归属：一层切分对"同一节同时属于两个主题"无能为力，
  也会让"章太粗、知识点太碎"只能靠阈值赌；这里加第二层（章），并允许一节的归属不唯一。

规则优先、模型兜底、失败就说：本模块所有函数都不调用模型，可在没网时完整跑通。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import embeddings

# ---------------------------------------------------------------------------
# 口径：什么不算"知识点"
# ---------------------------------------------------------------------------
# 注意别把材料结构当学习计划：「第三章」「第2节」是出处，不能拒；「第3天」「本周任务」才是安排。
SCHEDULE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"第\s*[一二三四五六七八九十\d]+\s*[天周次课时]段?"), "按时间排的段落"),
    (re.compile(r"^\s*\d{1,2}[:：]\d{2}"), "像时刻表"),
    (re.compile(r"(每日|每天|本周|本月|今日|倒计时|冲刺日程|日程表|时间表)"), "按时间排"),
    (re.compile(r"(学习|复习|练习|训练|安排|计划|规划|任务|清单|列表|进度|安排表)的?$"), "是安排不是知识点"),
    (re.compile(r"(综合复习|总复习|阶段复习|查漏补缺|高频考点|重点突破|真题|模拟题|全真模拟)"), "复习环节而非知识点"),
    (re.compile(r"(错题|回顾|小结|总结发言|导学)"), "复习环节而非知识点"),
)

# 题型/场景尾巴：「P、V操作实现独木桥问题」真正的知识点是「P、V操作」
TAIL_RE = re.compile(
    r"(的?(实现|应用|运用|计算|判断|证明|求解|设计|分析|案例|例题|习题|练习|问答|简答|选择|"
    r"大题|小问|问题|题型|专题|考点|技巧|方法总结))+$")

# 「间隔复习」「提取练习」是被研究的学习策略，是知识点；「复习计划」「每日任务」才是要拦的安排。
# 判据：活动词前面挂着策略名 → 放行；挂着时间或什么都没有 → 拦。
_ACTIVITY_TAIL = re.compile(r"(学习|复习|练习|训练|安排|计划|规划|任务|清单|列表|进度)(表|单)?$")
_STRATEGY_PREFIX = re.compile(
    r"(间隔|分散|集中|交错|提取|检索|主动|自我|精细|复述|输出|卡片|费曼|思维导图|测试|睡眠)$")
_TIME_MARKER = re.compile(r"(第|每|周|天|日|倒计|冲刺|日程|时间)")

UNIT_KINDS: tuple[str, ...] = ("concept", "principle", "procedure", "fact", "meta")
KIND_LABELS = {
    "concept": "概念", "principle": "原理", "procedure": "方法/步骤",
    "fact": "事实/参数", "meta": "学习策略",
}
KIND_HINTS = {
    "procedure": ("怎么", "步骤", "流程", "方法", "算法", "求解", "判定", "实现", "使用", "做到"),
    "principle": ("为什么", "原理", "机制", "原因", "导致", "因为", "定律", "定理", "关系", "影响", "之所以"),
    "fact": ("分为", "包括", "等于", "至少", "最多", "不超过", "有\\d+个", "\\d+(\\.\\d+)?%?", "年份", "标准是"),
    "meta": ("复习", "记忆", "学习策略", "元认知", "自测", "输出", "刻意练习", "间隔"),
}

_SPACE_RUN = re.compile(r"\s+")


def _norm(value: Any) -> str:
    return _SPACE_RUN.sub("", str(value or ""))


def _grams(text: str) -> set[str]:
    """二元组集合：单字重合在中文里太容易撞车，整句比对又太脆。"""
    return {text[i:i + 2] for i in range(len(text) - 1)}


def schedule_like(title: Any) -> str | None:
    """标题其实是"学习安排"时返回原因，否则 None。

    被研究的学习策略（间隔复习、提取练习、交错练习）也是"复习/练习"结尾，但它们是知识点不是安排；
    这里靠词头区分，因为把「间隔复习」当计划砍掉，等于把元认知材料整节删掉。
    """
    text = str(title or "").strip()
    if not text:
        return "没有标题"
    for pattern, why in SCHEDULE_PATTERNS:
        hit = pattern.search(text)
        if not hit:
            continue
        stem = text[:hit.start()]
        if why == "是安排不是知识点" and _STRATEGY_PREFIX.search(stem) and not _TIME_MARKER.search(stem):
            continue
        return f"命中「{hit.group(0)}」：{why}"
    return None


def strip_type_tail(title: Any) -> str:
    """剥掉「…问题/…题型/…实现」这类题型尾巴，露出真正的知识点名。"""
    text = str(title or "").strip()
    cut = TAIL_RE.sub("", text).strip()
    return cut or text


def canonical_title(title: Any, pool: Iterable[Any]) -> tuple[str, str] | None:
    """把"知识点+题型"的标题归回知识池里已有的名字。

    只在**能对上已有名字**时才改写：凭空"抽象"就是让模型替我们编知识点名，那是另一类幻觉。
    返回 (归一后的标题, 依据)，对不上返回 None。
    """
    raw = str(title or "").strip()
    candidates = [str(x or "").strip() for x in pool if str(x or "").strip()]
    stripped = strip_type_tail(raw)
    probes = [(raw, "原文")] + ([] if stripped == raw else [(stripped, "剥掉题型尾巴")])
    for probe, how in probes:
        norm_probe = _norm(probe)
        if not norm_probe:
            continue
        for known in candidates:
            norm_known = _norm(known)
            if norm_probe == norm_known:
                return known, how
            if len(norm_known) >= 4 and (norm_known in norm_probe or norm_probe in norm_known):
                return known, how
    return None


def classify_kind(text: Any) -> tuple[str, str]:
    """知识点类型：规则判定优先，判不出就算概念（宁可不标也不硬套）。

    有了类型，"这份材料只拆出了概念、一条方法都没有"这种缺口才可被量化。
    """
    body = str(text or "")
    best, best_hits = "concept", 0
    for kind, words in KIND_HINTS.items():
        hits = sum(1 for w in words if re.search(w, body))
        if hits > best_hits:
            best, best_hits = kind, hits
    return (best, "rules") if best_hits else ("concept", "default")


# ---------------------------------------------------------------------------
# 溯源：锚点必须逐字命中
# ---------------------------------------------------------------------------
def locate(text: str, anchor: Any, limit: int = 120) -> tuple[int, int] | None:
    """在原文里找锚点的区间；容忍空白差异（浏览器与模型都会吞换行），不接受改写。"""
    needle = str(anchor or "").strip()
    if len(needle) < 4 or not text:
        return None
    needle = needle[:limit]
    pattern = r"\s*".join(re.escape(ch) for ch in _norm(needle))
    match = re.search(pattern, text)
    return (match.start(), match.end()) if match else None


def verify(text: str, anchor: Any) -> str:
    """命中就返回清洗后的锚点原文片段，否则空串。"""
    pos = locate(text, anchor)
    if not pos:
        return ""
    return text[pos[0]:pos[1]].strip()


def fallback_anchor(text: str, hint: Any, min_chars: int = 10) -> str:
    """模型没给可用锚点时，从原文里挑一句与它最像的话当锚点。

    这不是"造证据"：选出来的句子逐字来自原文，且只在关键词重叠足够时采纳。
    """
    hint_grams = _grams(_norm(hint))
    if not hint_grams or not text:
        return ""
    best, best_score = "", 0.0
    for sentence in re.split(r"[。！？；;\n]", text):
        sentence = sentence.strip()
        if len(sentence) < min_chars:
            continue
        overlap = len(hint_grams & _grams(_norm(sentence))) / len(hint_grams)
        if overlap > best_score:
            best, best_score = sentence, overlap
    return best if best_score >= 0.5 else ""


# ---------------------------------------------------------------------------
# 覆盖与溯源的量化
# ---------------------------------------------------------------------------
def coverage(sections: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    """小节覆盖率按**材料字数**加权，而不是按小节个数。

    按个数算会让"一句带过的小节被引用"和"三千字的核心节被引用"等价，
    那正好掩盖真正的缺口：长材料里没被读到的那部分。
    """
    used: dict[str, int] = {}
    for unit in units:
        sid = str(((unit.get("source_ref") or {}).get("id")) or "")
        if sid:
            used[sid] = used.get(sid, 0) + 1
    total_chars = sum(int(s.get("chars") or 0) for s in sections) or 0
    covered_chars = sum(int(s.get("chars") or 0) for s in sections if used.get(s["id"]))
    missing = [{"id": s["id"], "title": s["title"], "chars": s.get("chars")}
               for s in sections if not used.get(s["id"])]
    return {
        "sections": len(sections),
        "covered": len(sections) - len(missing),
        "ratio": round(covered_chars / total_chars, 3) if total_chars else 1.0,
        "section_ratio": round((len(sections) - len(missing)) / len(sections), 3) if sections else 1.0,
        "missing": missing[:20],
        "per_section": used,
    }


def grounding(units: list[dict[str, Any]]) -> dict[str, Any]:
    """有多少单元能指回原文的具体句子（并且逐字核过）。"""
    ok = sum(1 for u in units
             if (u.get("source_ref") or {}).get("kind") in ("section", "material")
             and (u.get("source_ref") or {}).get("anchor"))
    kinds = {}
    for u in units:
        kind = str((u.get("source_ref") or {}).get("kind") or "none")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"verified": ok, "total": len(units),
            "rate": round(ok / len(units), 3) if units else 0.0, "kinds": kinds}


# ---------------------------------------------------------------------------
# 第二层：章（软归属）
# ---------------------------------------------------------------------------
def chapterize(sections: list[dict[str, Any]], threshold: float = 0.55,
               max_per_chapter: int = 4) -> list[dict[str, Any]]:
    """把小节聚成章：一段可以进多个章，只要它对每个章都够像。

    硬切分（现在的语义分组）在"这节既属于 A 主题也属于 B 主题"时只能选一个，
    选错了就表现为知识点被拆到不该在的地方。云端向量不可用时退化为按顺序切块，
    并如实标 engine=order —— 不是"聚得很准"，只是"没有语义可依据"。
    """
    if not sections:
        return []
    titles = [f"{s.get('title') or ''}。{str(s.get('first') or '')[:120]}" for s in sections]
    vectors = embeddings.embed_texts(titles) if embeddings.enabled() else None
    usable = bool(vectors) and len(vectors or []) == len(sections) and all(vectors or [])
    chapters: list[dict[str, Any]] = []
    if not usable:
        for i in range(0, len(sections), max_per_chapter):
            group = sections[i:i + max_per_chapter]
            chapters.append({"title": group[0].get("title") or f"第 {i // max_per_chapter + 1} 章",
                             "section_ids": [s["id"] for s in group], "engine": "order"})
        return chapters
    for index, section in enumerate(sections):
        joined = False
        for chapter in chapters:
            if len(chapter["section_ids"]) >= max_per_chapter:
                continue
            sim = max(embeddings.cosine(vectors[index], vectors[i]) for i in chapter["members"])
            if sim >= threshold:
                chapter["section_ids"].append(section["id"])
                chapter["members"].append(index)
                joined = True
        if not joined:
            chapters.append({"title": section.get("title") or f"第 {len(chapters) + 1} 章",
                             "section_ids": [section["id"]], "members": [index], "engine": "semantic"})
    for chapter in chapters:
        chapter.pop("members", None)
    return chapters
