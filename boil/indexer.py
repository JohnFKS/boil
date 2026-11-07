"""方向 6：教材资料的轻量索引与按需深化。

旧路径在导入时把整本教材喂给模型、串行分批生成全部单元（实测 58k 字符 /
23 分钟 / 10 批 × 9k 输出 token），可用户往往只学到前几章。这里改成：

1. 导入期：按小节切块、算一次向量、存索引；用本地规则给每个小节生成
   「骨架单元」保证内容包合法、学习计划与地图完整。全程 0 次模型调用。
2. 学习期：用户真的打开某个单元时，用它的编号 + 向量检索取回相关原文，
   只针对这一个单元调一次模型（约 2k 输出 token），结果写回内容包与 DB。

骨架单元沿用 `packs.validate_pack` 的既有结构，因此 dashboard / map /
planner / SRS 全都不用改；深化状态记在 `unit.source_ref.deepen`，会随
`source_ref_json` 一起落库。
"""
from __future__ import annotations

import json
from typing import Any

from . import compiler, config, decompose, embeddings
from .packs import PackError, normalize_pack, validate_pack
from .utils import new_id, now_iso, slugify

INDEX_DIR_NAME = "indexes"
MAX_INDEX_UNITS = 90          # validate_pack 上限 100，留余量
EMBED_CHARS = 1200            # 与 embeddings.EMBED_CHARS 对齐
DEFAULT_TOP_K = 3
MAX_CONTEXT_CHARS = 6000      # 单次深化送进模型的原文上限

DEEPEN_SYSTEM = "你是严谨的学习材料编辑。只依据给定原文，输出一个合法 JSON 对象，不要 Markdown 代码块。"
DEEPEN_PROMPT = """把教材的一个小节整理成「一个」高质量学习单元。

学科：__SUBJECT__
单元标题：__TITLE__
当前骨架摘要：__ESSENCE__

只依据下面的原文片段，输出一个 JSON 对象：
{
  "title": "小节标题",
  "essence": "150 字以内讲清本质",
  "steps": ["标准步骤或推导关键"],
  "boundaries": ["适用条件或失效情形"],
  "misconceptions": ["常见误区"],
  "key_points": ["关键词"],
  "probes": [{"kind":"recall|explain|apply|boundary","prompt":"问题","answer":"参考答案","rubric":["采分点"]}],
  "cards": [{"front":"卡面","back":"卡背"}]
}

要求：不要编造原文里没有的数字、结论或例子；最多 6 个 steps、4 个 probes、
3 张卡片；总字数控制在 900 字以内，宁可精炼也不要铺陈。

原文片段：
__CONTEXT__
"""


def index_path(pack_id: str):
    return config.DATA_DIR / INDEX_DIR_NAME / f"{slugify(pack_id)}.json"


def save_index(index: dict[str, Any]):
    path = index_path(index["pack_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_index(pack_id: str) -> dict[str, Any] | None:
    path = index_path(pack_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("chunks"), list) else None
    except (OSError, ValueError):
        return None


def index_summary(index: dict[str, Any] | None) -> dict[str, Any]:
    if not index:
        return {"chunks": 0, "engine": "", "dim": 0, "model_calls": 0, "available": False}
    return {"chunks": len(index.get("chunks") or []),
            "engine": str(index.get("engine") or ""),
            "dim": int(index.get("dim") or 0),
            "model_calls": 0,
            "available": True}


def build(title: str, subject: str, merged: str,
          docs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """秒级建包：本地切分 + 向量索引，不调用任何生成模型。"""
    warnings: list[str] = []
    # use_llm=False：超长小节按段落本地切分，导入期一次生成模型都不调。
    sections = compiler._expand_sections(merged, use_embeddings=False, use_llm=False)
    all_tables = compiler._section_tables(sections)
    if not all_tables:
        raise PackError("材料为空或无法切分", ["请至少提供一段可读文本"])
    # 单元数受 validate_pack 的 100 上限约束，但原文索引不该跟着被砍：
    # 后面的小节仍要能被检索回来，作为相邻单元的深化上下文。
    unit_tables = all_tables
    if len(all_tables) > MAX_INDEX_UNITS:
        warnings.append(f"小节数 {len(all_tables)} 超过 {MAX_INDEX_UNITS}，"
                        f"骨架单元只覆盖前 {MAX_INDEX_UNITS} 个；"
                        f"原文索引仍保留全部 {len(all_tables)} 个小节")
        unit_tables = all_tables[:MAX_INDEX_UNITS]

    units: list[dict[str, Any]] = []
    for i, t in enumerate(unit_tables):
        unit = compiler._heuristic_unit(t["title"], t["body"], i, subject)
        unit["id"] = f"idx-{i + 1}-{slugify(t['title'], 'sec')}"[:60]
        unit["source_ref"] = {
            "system": "boil-index", "kind": "section", "id": t["id"],
            "title": t["title"], "anchor": str(t.get("first") or "")[:120],
            "deepen": {"state": "pending"},
        }
        units.append(unit)

    # 向量只需要认出「这一节讲什么」，不需要把整节公式和例题都编码一遍：
    # 完整正文留在 chunk.text 里供深化时引用，短文本让冷启动的云端请求更快。
    texts = [f"{t['title']}\n{t.get('first') or ''}\n{t['body'][:200]}"[:EMBED_CHARS]
             for t in all_tables]
    vectors = embeddings.embed_texts(texts)
    if vectors is None or len(vectors) != len(texts):
        vectors = [embeddings.local_embed(t) for t in texts]
        engine = embeddings.LOCAL_MODEL
        warnings.append("云端向量不可用，索引已降级为本地哈希向量")
    else:
        engine = str(embeddings.active_model() or "remote")
    dim = len(vectors[0]) if vectors else 0
    chunks = [{"id": t["id"], "title": t["title"], "chars": int(t["chars"]),
               "text": t["body"], "vector": v} for t, v in zip(all_tables, vectors)]

    pid = f"user.{slugify(subject or title, 'material')}.{new_id('')[:6]}"
    pack: dict[str, Any] = {
        "schema_version": "1.0",
        "id": pid,
        "version": "1.0.0",
        "kind": "subject",
        "name": title or f"{subject or '自定义'} 内容包",
        "description": f"从材料「{title or '未命名'}」建立的 {len(chunks)} 个小节索引",
        "subject": subject,
        "language": "zh-CN",
        "license": "user-provided",
        "source": {
            "system": "boil-index", "kind": "material", "mode": "index",
            "generated_at": now_iso(), "title": title,
            "documents": [{"name": d["name"], "chars": d["chars"]} for d in docs],
            "material_chars": len(merged),
            "index": {"chunks": len(chunks), "engine": engine, "dim": dim,
                      "model_calls": 0},
        },
        "tags": [subject] if subject else [],
        "cover": "steam",
        "units": units,
    }
    pack.update(compiler._fallback_plan_fields(units))
    pack["sections"] = compiler._registry(all_tables)
    pack["chapters"] = decompose.chapterize(all_tables)
    pack["generated_by"] = "index"
    pack = normalize_pack(pack, source="compiler")
    errors = validate_pack(pack)
    if errors:
        raise PackError("索引内容包校验失败", errors)

    index = {"pack_id": pid, "engine": engine, "dim": dim,
             "created_at": now_iso(), "chunks": chunks}
    return pack, index, warnings


def search(index: dict[str, Any] | None, query: str = "", section_id: str = "",
           top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    """取回本小节原文 + 向量最相近的邻近小节，且不把整本教材塞进 prompt。"""
    chunks = (index or {}).get("chunks") or []
    if not chunks:
        return []
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_id = {c.get("id"): c for c in chunks}
    primary = by_id.get(section_id)
    if primary:
        picked.append(primary)
        seen.add(str(primary.get("id")))

    dim = int((index or {}).get("dim") or 0)
    if dim and query:
        try:
            qvec = embeddings.embed(query)
        except Exception:
            qvec = []
        if len(qvec) == dim:
            scored = []
            for c in chunks:
                vec = c.get("vector") or []
                if len(vec) == dim:
                    scored.append((embeddings.cosine(qvec, vec), c))
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, c in scored:
                key = str(c.get("id"))
                if key in seen:
                    continue
                picked.append(c)
                seen.add(key)
                if len(picked) >= max(1, top_k) + (1 if primary else 0):
                    break

    out, used = [], 0
    for c in picked:
        room = MAX_CONTEXT_CHARS - used
        if room <= 0:
            break
        item = {k: v for k, v in c.items() if k != "vector"}
        item["text"] = str(item.get("text") or "")[:room]
        out.append(item)
        used += len(item["text"])
    return out


def deepen_prompt(unit: dict[str, Any], chunks: list[dict[str, Any]], subject: str) -> str:
    context = "\n\n".join(
        f"### [{c.get('id')}] {c.get('title')}\n{c.get('text')}" for c in chunks)
    return (DEEPEN_PROMPT
            .replace("__SUBJECT__", subject or "未指定")
            .replace("__TITLE__", str(unit.get("title") or ""))
            .replace("__ESSENCE__", str(unit.get("essence") or "")[:300])
            .replace("__CONTEXT__", context or "（没有取到原文片段）"))
