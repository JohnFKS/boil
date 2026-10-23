"""内容编译器：材料 → LearningPack。

两条路径：
1. 配置了真实模型：用 LLM 做结构化抽取，失败时显式降级到本地编译器并返回 warning；
2. 演示模式：本地确定性编译器。它不会编造事实，只做切分、抽取、出题、卡片。
"""
from __future__ import annotations

import re
from typing import Any

from . import llm
from .packs import PackError, derive_key_points, normalize_pack, validate_pack
from .embeddings import semantic_groups
from .utils import clamp, new_id, now_iso, slugify, split_cn

PROBE_TEMPLATES = {
    "recall": "不看材料，用自己的话说明「{title}」最核心的一句话是什么？",
    "explain": "为什么「{title}」成立？请解释背后的原因或机制，而不是只给结论。",
    "apply": "给一个具体场景或例子，说明你会怎样使用「{title}」。",
    "boundary": "「{title}」在什么条件下不适用？请举一个反例或边界情况。",
}

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
NUMBERED_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s+(.+)$")
SENT_END_RE = re.compile(r"[。！？!?；;\n]")

# 长材料的三道闸门：单次调用上限、每批预算、单元数量上限
BATCH_CHAR_BUDGET = 7000
# 实测（Dots 网关）：8.6k 字材料单次调用能出可用 JSON，10.2k 字就在尾部被截成
# "difficulty":4,":5,":":40"——能否修好变成掷硬币。上限按真实输出能力定，不按上下文长度定。
SINGLE_CALL_LIMIT = 6000
MAX_UNITS_SINGLE = 10
MAX_UNITS_BATCHED = 40


def _norm_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_titles(raw: Any, limit: int = 40) -> list[str]:
    out: list[str] = []
    for item in (raw or []):
        title = _norm_text(item, limit)
        if title and title not in out:
            out.append(title)
    return out[:12]


def _match_unit_titles(raw_titles: list[str], known: list[str]) -> list[str]:
    """把大纲里的标题对应回真实单元标题：先精确，再互相包含。"""
    matched: list[str] = []
    for title in raw_titles:
        if not title:
            continue
        if title in known and title not in matched:
            matched.append(title)
            continue
        hit = next((k for k in known if k and (title in k or k in title)), None)
        if hit and hit not in matched:
            matched.append(hit)
    return matched


def _normalize_plan_fields(data: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
    """清洗模型给出的大纲/阶段/总览；大纲补齐所有未被覆盖的单元。"""
    known = [str(u.get("title") or "") for u in units]
    by_title = {str(u.get("title") or ""): u for u in units}
    outline: list[dict[str, Any]] = []
    placed: set[str] = set()
    for raw in (data.get("outline") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        title = _norm_text(raw.get("title"), 40)
        if not title:
            continue
        titles = _match_unit_titles(_clean_titles(raw.get("unit_titles")), known)
        placed.update(titles)
        outline.append({"title": title, "summary": _norm_text(raw.get("summary"), 140),
                        "unit_titles": titles})
    for title in known:
        if title and title not in placed:
            unit = by_title[title]
            outline.append({"title": title, "summary": _norm_text(unit.get("essence"), 140),
                            "unit_titles": [title]})
    study_plan: list[dict[str, Any]] = []
    for raw in (data.get("study_plan") or [])[:8]:
        if not isinstance(raw, dict):
            continue
        phase = _norm_text(raw.get("phase"), 24)
        if not phase:
            continue
        try:
            minutes = int(clamp(int(raw.get("minutes") or 0), 0, 6000))
        except (TypeError, ValueError):
            minutes = 0
        study_plan.append({"phase": phase, "focus": _norm_text(raw.get("focus"), 60),
                           "unit_titles": _match_unit_titles(_clean_titles(raw.get("unit_titles")), known),
                           "minutes": minutes})
    digest_raw = data.get("digest") if isinstance(data.get("digest"), dict) else {}
    overview = _norm_text(digest_raw.get("overview"), 400)
    advice = [_norm_text(a, 80) for a in (digest_raw.get("advice") or []) if _norm_text(a, 80)][:5]
    return {"outline": outline, "study_plan": study_plan,
            "digest": {"overview": overview, "advice": advice}}


def _fallback_plan_fields(units: list[dict[str, Any]]) -> dict[str, Any]:
    """本地编译器用的同构字段：只陈述材料里已有的顺序与本质，不做推断。"""
    outline = [{"title": str(u.get("title") or ""),
                "summary": _norm_text(u.get("essence"), 140),
                "unit_titles": [str(u.get("title") or "")]} for u in units[:12]]
    phases = []
    for i in range(0, len(units), 3):
        group = units[i:i + 3]
        phases.append({
            "phase": f"阶段 {i // 3 + 1}",
            "focus": _norm_text(group[0].get("title"), 60),
            "unit_titles": [str(u.get("title") or "") for u in group],
            "minutes": sum(int(u.get("estimated_minutes") or 40) for u in group),
        })
    titles = "；".join(_norm_text(u.get("title"), 30) for u in units[:4])
    return {
        "outline": outline,
        "study_plan": phases,
        "digest": {
            "overview": f"这份材料被切分为 {len(units)} 个单元：{titles}。先吃透每个单元的本质，再用探针验证。",
            "advice": ["每个单元先合书复述，再答探针", "答不出的单元第二天优先复习", "阶段末做一次费曼讲解"],
        },
    }


def _clean_line(line: str) -> str:
    return re.sub(r"^[\s>#*\-•]+", "", line).strip()


def _split_sections(text: str, use_embeddings: bool = True) -> list[tuple[str, str]]:
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    matches = list(HEADING_RE.finditer(text))
    if matches:
        sections = []
        for i, m in enumerate(matches):
            title = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if body:
                sections.append((title, body))
        if sections:
            return sections

    # 没有 Markdown 标题：按空行段落分组，每 2–4 段一个单元
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        paragraphs = lines
    if use_embeddings and len(paragraphs) >= 4:
        groups = semantic_groups(paragraphs, max_size=3, threshold=None)
    else:
        groups = []
        chunk = []
        for p in paragraphs:
            chunk.append(p)
            if len(chunk) >= 3 or len(" ".join(chunk)) > 700:
                groups.append(chunk)
                chunk = []
        if chunk:
            groups.append(chunk)
    sections = []
    for i, group in enumerate(groups[:8]):
        body = "\n\n".join(group)
        first = re.split(r"[。！？!?\n]", group[0])[0].strip()
        title = first[:28] or f"知识单元 {i + 1}"
        sections.append((title, body))
    return sections


def _extract_list_items(body: str) -> list[str]:
    items = []
    for line in body.splitlines():
        m = NUMBERED_RE.match(line)
        if m:
            val = _clean_line(m.group(1))
            if 3 <= len(val) <= 160:
                items.append(val)
    return items[:8]


def _extract_keyword_lines(body: str, keywords: tuple[str, ...]) -> list[str]:
    out = []
    for line in body.splitlines():
        clean = _clean_line(line)
        if not clean or len(clean) > 220:
            continue
        if any(k in clean for k in keywords):
            out.append(clean)
    return out[:4]


def _first_sentences(body: str, n: int = 2, limit: int = 220) -> str:
    body = re.sub(r"\s+", " ", _clean_line(body))
    parts = re.split(r"(?<=[。！？!?])\s*", body)
    text = "".join(parts[:n]).strip()
    if not text:
        text = body
    return text[:limit]


def _extract_analogy(body: str) -> str:
    for sent in re.split(r"(?<=[。！？!?])", body):
        if any(k in sent for k in ("就像", "好比", "如同", "类比", "像一")):
            return sent.strip()[:200]
    return ""


def _make_probes(unit: dict[str, Any]) -> list[dict[str, Any]]:
    probes = []
    for kind, template in PROBE_TEMPLATES.items():
        answer = unit.get("essence", "")
        if kind == "explain":
            answer = (unit.get("essence", "") + "\n" +
                      "\n".join(f"- {s}" for s in (unit.get("steps") or []))).strip()
        elif kind == "apply":
            answer = unit.get("example") or "用一个具体例子说明它如何被使用。"
        elif kind == "boundary":
            answer = "\n".join(f"- {b}" for b in (unit.get("boundaries") or [])) or \
                     "需要说明适用条件；没有材料依据时不要臆造。"
        probes.append({
            "id": f"{unit['id']}-{kind}",
            "kind": kind,
            "prompt": template.format(title=unit["title"]),
            "answer": answer,
            "rubric": unit.get("key_points") or [],
            "hints": [unit.get("essence", "")[:80]],
        })
    return probes


def _make_cards(unit: dict[str, Any]) -> list[dict[str, Any]]:
    cards = [{"front": f"「{unit['title']}」的核心本质是什么？",
              "back": unit.get("essence", "")}]
    if unit.get("boundaries"):
        cards.append({"front": f"「{unit['title']}」在什么条件下会失效？",
                      "back": "\n".join(f"- {b}" for b in unit["boundaries"])})
    return cards


def _heuristic_unit(title: str, body: str, index: int, subject: str) -> dict[str, Any]:
    essence = _first_sentences(body, 2, 240)
    steps = _extract_list_items(body)
    boundaries = _extract_keyword_lines(body, ("注意", "条件", "不适用", "失效", "例外", "前提", "除非"))
    misconceptions = _extract_keyword_lines(body, ("不是", "误区", "错误", "不要", "并非", "很多人以为"))
    analogy = _extract_analogy(body)
    example = steps[0] if steps else _first_sentences(body, 1, 160)
    key_points = []
    seen = set()
    for raw in [essence, *steps[:4], *boundaries[:2]]:
        for part in split_cn(raw, 4):
            if 2 <= len(part) <= 28 and part not in seen:
                seen.add(part)
                key_points.append(part)
    key_points = key_points[:6]
    unit = {
        "id": f"unit-{index + 1}-{slugify(title, 'unit')}",
        "title": title,
        "angle": _norm_text(f"先合书复述「{title[:12]}」的本质，再逐条验证步骤与边界", 60),
        "essence": essence,
        "analogy": analogy,
        "example": example,
        "steps": steps,
        "boundaries": boundaries,
        "misconceptions": misconceptions,
        "faq": [{"q": f"「{title}」最容易误解的地方是什么？",
                 "a": (misconceptions[0] if misconceptions else "先确认定义和适用条件，再谈应用。")}],
        "key_points": key_points,
        "difficulty": 3,
        "importance": 4 if index < 3 else 3,
        "estimated_minutes": 40,
        "order_index": index,
        "tags": [subject] if subject else [],
        "source_ref": {"system": "compiler", "kind": "material", "id": f"section-{index + 1}"},
    }
    unit["probes"] = _make_probes(unit)
    unit["cards"] = _make_cards(unit)
    return unit


SECTION_SPLIT_THRESHOLD = 3000


def _anchors_from_llm(body: str) -> list[str]:
    """让模型只给**逐字锚点**，切片仍由代码做：模型不碰原文，就不会改写内容。"""
    prompt = (
        "下面是一段没有小标题的长正文。请挑出 2–5 个话题转折点的**逐字片段**"
        "（每段 8–30 字，必须原样出现在正文里，不要改写、不要加省略号）。\n\n"
        "只输出 JSON：{\"anchors\": [\"逐字片段\"]}\n\n正文：\n" + body[:12000]
    )
    data = llm.chat_json([
        {"role": "system", "content": "你只负责定位，不改写文本。只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ], model=llm.model_name(), temperature=0.1, max_tokens=500)
    anchors = data.get("anchors") if isinstance(data, dict) else None
    out = []
    for raw in (anchors or [])[:6]:
        anchor = str(raw or "").strip()
        if 4 <= len(anchor) <= 60 and anchor in body and anchor not in out:
            out.append(anchor)
    return out


def _slice_at_anchors(body: str, anchors: list[str]) -> list[tuple[str, str]]:
    positions = sorted({body.find(a) for a in anchors if body.find(a) >= 0})
    positions = [p for p in positions if p > 0]
    if not positions:
        return []
    pieces = []
    bounds = [0, *positions, len(body)]
    for i in range(len(bounds) - 1):
        chunk = body[bounds[i]:bounds[i + 1]].strip()
        if len(chunk) < 60:
            continue
        first = re.split(r"[。！？!?\n]", chunk)[0].strip()
        pieces.append((first[:28] or f"片段 {len(pieces) + 1}", chunk))
    return pieces


def _split_oversized(title: str, body: str) -> list[tuple[str, str]]:
    """超长小节（常见于没有小标题的 PDF 粘贴）按锚点切开；失败就原样返回。"""
    if len(body) <= SECTION_SPLIT_THRESHOLD or not llm.enabled():
        return [(title, body)]
    try:
        pieces = _slice_at_anchors(body, _anchors_from_llm(body))
    except Exception:
        return [(title, body)]
    return pieces if len(pieces) >= 2 else [(title, body)]


def compile_heuristic(title: str, subject: str, text: str, use_embeddings: bool = True) -> dict[str, Any]:
    sections = _split_sections(text, use_embeddings=use_embeddings)
    if not sections:
        raise PackError("材料为空或无法切分", ["请至少提供一段可读文本"])
    expanded: list[tuple[str, str]] = []
    for sec_title, sec_body in sections:
        expanded.extend(_split_oversized(sec_title, sec_body))
    sections = expanded
    units = [_heuristic_unit(t, b, i, subject) for i, (t, b) in enumerate(sections[:8])]
    pid = f"user.{slugify(subject or title, 'material')}.{new_id('')[:6]}"
    pack = {
        "schema_version": "1.0",
        "id": pid,
        "version": "1.0.0",
        "kind": "subject",
        "name": title or f"{subject or '自定义'} 内容包",
        "description": f"从材料「{title or '未命名'}」编译的 {len(units)} 个知识单元",
        "subject": subject,
        "language": "zh-CN",
        "license": "user-provided",
        "source": {"system": "boil-compiler", "kind": "material", "generated_at": now_iso(),
                   "title": title},
        "tags": [subject] if subject else [],
        "cover": "steam",
        "units": units,
    }
    pack.update(_fallback_plan_fields(units))
    pack["generated_by"] = "rules"
    return normalize_pack(pack, source="compiler")


def _normalize_llm_units(data: Any, title: str, subject: str,
                         limit: int = MAX_UNITS_SINGLE,
                         warnings: list[str] | None = None) -> dict[str, Any]:
    if isinstance(data, list):        # 模型偶尔直接回一个单元数组，而不是包对象
        data = {"units": data}
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        raise PackError("模型返回的 Pack 结构不正确", ["缺少 units 数组"])
    if len(data["units"]) > limit and warnings is not None:
        warnings.append(f"模型返回 {len(data['units'])} 个单元，只留下前 {limit} 个；"
                        "这份材料建议拆成两次导入")
    units = []
    for i, raw in enumerate(data["units"][:limit]):
        if not isinstance(raw, dict):
            continue
        u = {
            "id": f"unit-{i + 1}-{slugify(raw.get('title', f'unit-{i + 1}'), 'unit')}",
            "title": str(raw.get("title") or f"知识单元 {i + 1}"),
            "angle": _norm_text(raw.get("angle"), 80),
            "essence": str(raw.get("essence") or ""),
            "analogy": str(raw.get("analogy") or ""),
            "example": str(raw.get("example") or ""),
            "steps": [str(x) for x in (raw.get("steps") or []) if str(x).strip()][:10],
            "boundaries": [str(x) for x in (raw.get("boundaries") or []) if str(x).strip()][:6],
            "misconceptions": [str(x) for x in (raw.get("misconceptions") or []) if str(x).strip()][:6],
            "faq": [x for x in (raw.get("faq") or []) if isinstance(x, dict)][:5],
            "key_points": [str(x) for x in (raw.get("key_points") or []) if str(x).strip()][:8],
            "difficulty": int(raw.get("difficulty") or 3),
            "importance": int(raw.get("importance") or 4),
            "estimated_minutes": int(raw.get("estimated_minutes") or 40),
            "order_index": i,
            "tags": [str(x) for x in (raw.get("tags") or [])][:8],
            "source_ref": {"system": "compiler", "kind": "llm", "id": f"unit-{i + 1}"},
        }
        if not u["essence"]:
            u["essence"] = u["title"]
        if not u["key_points"]:
            u["key_points"] = derive_key_points(u)
        raw_probes = raw.get("probes") or []
        probes = []
        for j, p in enumerate(raw_probes[:6]):
            if not isinstance(p, dict) or not p.get("prompt"):
                continue
            probes.append({
                "id": f"{u['id']}-{p.get('kind', 'recall')}-{j + 1}",
                "kind": p.get("kind") if p.get("kind") in PROBE_TEMPLATES else "recall",
                "prompt": str(p["prompt"]),
                "answer": str(p.get("answer") or ""),
                "rubric": [str(x) for x in (p.get("rubric") or [])][:6],
                "hints": [str(x) for x in (p.get("hints") or [])][:4],
            })
        if not probes:
            probes = _make_probes(u)
        else:
            # 模型只给了部分类型时补齐缺的那几种：四个维度缺一，掌握度就少一路证据
            have = {p.get("kind") for p in probes}
            missing = [k for k in PROBE_TEMPLATES if k not in have]
            for k in missing:
                probes.extend(p for p in _make_probes(u) if p["kind"] == k)
            probes = probes[:8]
        u["probes"] = probes
        raw_cards = raw.get("cards") or []
        cards = []
        for c in raw_cards[:4]:
            if isinstance(c, dict) and c.get("front") and c.get("back"):
                cards.append({"front": str(c["front"]), "back": str(c["back"])})
        u["cards"] = cards or _make_cards(u)
        units.append(u)
    if not units:
        raise PackError("模型没有生成任何单元", ["请重试或使用本地编译器"])
    pid = f"user.{slugify(subject or title, 'material')}.{new_id('')[:6]}"
    pack = {
        "schema_version": "1.0",
        "id": pid,
        "version": "1.0.0",
        "kind": "subject",
        "name": str(data.get("name") or title or "自定义内容包"),
        "description": str(data.get("description") or f"从「{title}」编译"),
        "subject": str(data.get("subject") or subject or ""),
        "language": "zh-CN",
        "license": "user-provided",
        "source": {"system": "boil-compiler", "kind": "llm", "generated_at": now_iso(),
                   "title": title},
        "tags": [subject] if subject else [],
        "cover": "steam",
        "units": units,
    }
    pack.update(_normalize_plan_fields(data, units))
    pack["generated_by"] = "model"
    return normalize_pack(pack, source="compiler")


def _cut_at_sentence(text: str, limit: int) -> str:
    """在前 limit 个字里找最后一个句子结尾；找不到就硬切，保证一定有进展。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    best = -1
    for m in SENT_END_RE.finditer(window):
        best = m.end()
    cut = best if best >= limit // 3 else limit
    return text[:cut]


def _chunk_section(title: str, body: str, budget: int) -> list[tuple[str, str]]:
    """无损切开超长小节：先按空行拆段，超长段再按句号切，然后把小块装回预算内。

    这里以前写的是 body[:budget]——后半篇被静默丢掉，而产品对用户承诺的是
    「模型读完了全部材料」，截断不吭声就是在说谎。
    """
    if len(body) <= budget:
        return [(title, body)]
    atoms: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        while len(block) > budget:
            head = _cut_at_sentence(block, budget)
            atoms.append(head)
            block = block[len(head):].lstrip()
        if block:
            atoms.append(block)
    pieces: list[str] = []
    for atom in atoms:
        if pieces and len(pieces[-1]) + len(atom) + 2 <= budget:
            pieces[-1] = pieces[-1] + "\n\n" + atom
        else:
            pieces.append(atom)
    out = [(title if i == 0 else f"{title}（续 {i}）", piece)
           for i, piece in enumerate(pieces) if piece.strip()]
    return out or [(title, _cut_at_sentence(body, budget))]


def _batch_sections(sections: list[tuple[str, str]],
                    budget: int | None = None) -> list[list[tuple[str, str]]]:
    """按预算把小节装成批次；超长小节先无损切开，绝不丢字。

    budget 在调用时才读全局值，写成默认参数会把常量冻结在定义时刻，测试就无法调整。
    """
    budget = budget or BATCH_CHAR_BUDGET
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0
    for title, body in sections:
        for piece_title, piece in _chunk_section(title, body, budget):
            size = len(piece_title) + len(piece)
            if current and used + size > budget:
                batches.append(current)
                current, used = [], 0
            current.append((piece_title, piece))
            used += size
    if current:
        batches.append(current)
    return batches


def _skeleton(sections: list[tuple[str, str]]) -> str:
    """全篇骨架：标题 + 首句。让模型在第一批之前就看见整份材料的形状。"""
    lines = []
    for title, body in sections:
        first = _first_sentences(body, 1, 90)
        lines.append(f"- {title[:40]}：{first}" if first else f"- {title[:40]}")
    return "\n".join(lines)[:12000]


def _outline_pass(title: str, subject: str, sections: list[tuple[str, str]],
                  char_total: int) -> dict[str, Any]:
    prompt = (llm.load_prompt("compiler_outline.md")
              .replace("{title}", title).replace("{subject}", subject or "未指定")
              .replace("{section_total}", str(len(sections)))
              .replace("{char_total}", f"{char_total:,}")
              .replace("{skeleton}", _skeleton(sections)))
    data = llm.chat_json([
        {"role": "system", "content": "你是严谨的学习内容规划器。只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ], model=llm.model_name(deep=True), temperature=0.2, max_tokens=4000)
    return data if isinstance(data, dict) else {}


def _outline_titles(outline: dict[str, Any]) -> str:
    entries = (outline.get("outline") or [])[:12]
    if not entries:
        return "（未提供大纲，按材料原顺序处理）"
    return "；".join(str(e.get("title") or "") for e in entries if isinstance(e, dict))


def compile_material_batched(title: str, subject: str, text: str,
                             use_embeddings: bool = True
                             ) -> tuple[dict[str, Any], list[str]]:
    """长材料分批编译：先全篇骨架出大纲，再逐批只针对本批出单元。

    单次调用截断到 24k 会让模型只看得到开头——那不等于"分析完所有素材"。
    """
    warnings: list[str] = []
    sections = _split_sections(text, use_embeddings=use_embeddings)
    if not sections:
        raise PackError("材料为空或无法切分", ["请至少提供一段可读文本"])
    outline_data = _outline_pass(title, subject, sections, len(text))
    outline_hint = _outline_titles(outline_data)
    batches = _batch_sections(sections)
    collected: list[dict[str, Any]] = []
    for i, batch in enumerate(batches):
        # 不再二次截断：_batch_sections 已保证每批在预算内，多余的切片只会静默丢内容
        body = "\n\n".join(f"## {t}\n{b}" for t, b in batch)
        prompt = (llm.load_prompt("compiler_batch.md")
                  .replace("{title}", title).replace("{subject}", subject or "未指定")
                  .replace("{outline}", outline_hint)
                  .replace("{batch_index}", str(i + 1)).replace("{batch_total}", str(len(batches)))
                  .replace("{text}", body))
        try:
            data = llm.chat_json([
                {"role": "system", "content": "你是严谨的学习内容编译器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.2, max_tokens=9000)
        except Exception as exc:
            warnings.append(f"第 {i + 1}/{len(batches)} 批编译失败，已跳过：{str(exc)[:140]}")
            continue
        for unit in (data.get("units") or []) if isinstance(data, dict) else []:
            if isinstance(unit, dict):
                collected.append(unit)
    if not collected:
        raise PackError("分批编译没有产出任何单元", ["请重试或使用本地编译器"])
    merged = {"name": outline_data.get("name") or title,
              "description": outline_data.get("description") or "",
              "subject": outline_data.get("subject") or subject,
              "outline": outline_data.get("outline") or [],
              "study_plan": outline_data.get("study_plan") or [],
              "digest": outline_data.get("digest") or {},
              "units": collected}
    pack = _normalize_llm_units(merged, title, subject,
                                limit=MAX_UNITS_BATCHED, warnings=warnings)
    if len(batches) > 1:
        pack["source"]["batches"] = len(batches)
        pack["source"]["material_chars"] = len(text)
    errors = validate_pack(pack)
    if errors:
        raise PackError("分批编译的 Pack 未通过校验", errors)
    return pack, warnings


def compile_material(title: str, subject: str, text: str,
                     use_llm: bool = True,
                     use_embeddings: bool = True) -> tuple[dict[str, Any], list[str]]:
    title = (title or "未命名材料").strip()
    subject = (subject or "").strip()
    warnings: list[str] = []
    if use_llm and llm.enabled():
        if len(text) > SINGLE_CALL_LIMIT:
            try:
                return compile_material_batched(title, subject, text, use_embeddings=use_embeddings)
            except Exception as exc:
                warnings.append(f"分批编译失败，已回退单次抽取或本地编译器：{str(exc)[:160]}")
        prompt = llm.load_prompt("compiler_extract.md")
        prompt = prompt.replace("{title}", title).replace("{subject}", subject or "未指定")
        body = text if len(text) <= SINGLE_CALL_LIMIT else text[:SINGLE_CALL_LIMIT]
        if body != text:
            warnings.append(f"只送进了前 {SINGLE_CALL_LIMIT:,} 字（材料共 {len(text):,} 字），"
                            "后半篇没有被模型读到；建议缩短材料或重试分批编译")
        prompt = prompt.replace("{text}", body)
        try:
            data = llm.chat_json([
                {"role": "system", "content": "你是严谨的学习内容编译器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.2, max_tokens=12000)
            trimmed: list[str] = []
            pack = _normalize_llm_units(data, title, subject, warnings=trimmed)
            errors = validate_pack(pack)
            if errors:
                raise PackError("模型生成的 Pack 未通过校验", errors)
            warnings.extend(trimmed)
            return pack, warnings
        except Exception as exc:
            warnings.append(f"真实模型编译失败，已回退本地编译器：{str(exc)[:180]}")
    pack = compile_heuristic(title, subject, text, use_embeddings=use_embeddings)
    errors = validate_pack(pack)
    if errors:
        raise PackError("本地编译器生成的内容包未通过校验", errors)
    return pack, warnings
