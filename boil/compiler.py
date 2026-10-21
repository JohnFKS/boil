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


def compile_heuristic(title: str, subject: str, text: str, use_embeddings: bool = True) -> dict[str, Any]:
    sections = _split_sections(text, use_embeddings=use_embeddings)
    if not sections:
        raise PackError("材料为空或无法切分", ["请至少提供一段可读文本"])
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


def _normalize_llm_units(data: dict[str, Any], title: str, subject: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        raise PackError("模型返回的 Pack 结构不正确", ["缺少 units 数组"])
    units = []
    for i, raw in enumerate(data["units"][:10]):
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


def compile_material(title: str, subject: str, text: str,
                     use_llm: bool = True,
                     use_embeddings: bool = True) -> tuple[dict[str, Any], list[str]]:
    title = (title or "未命名材料").strip()
    subject = (subject or "").strip()
    warnings: list[str] = []
    if use_llm and llm.enabled():
        prompt = llm.load_prompt("compiler_extract.md")
        prompt = prompt.replace("{title}", title).replace("{subject}", subject or "未指定")
        prompt = prompt.replace("{text}", text[:24000])
        try:
            data = llm.chat_json([
                {"role": "system", "content": "你是严谨的学习内容编译器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], model=llm.model_name(deep=True), temperature=0.2, max_tokens=12000)
            pack = _normalize_llm_units(data, title, subject)
            errors = validate_pack(pack)
            if errors:
                raise PackError("模型生成的 Pack 未通过校验", errors)
            return pack, warnings
        except Exception as exc:
            warnings.append(f"真实模型编译失败，已回退本地编译器：{str(exc)[:180]}")
    pack = compile_heuristic(title, subject, text, use_embeddings=use_embeddings)
    errors = validate_pack(pack)
    if errors:
        raise PackError("本地编译器生成的内容包未通过校验", errors)
    return pack, warnings
