"""全局 agent：回答关于本系统的一切问题，并在用户点「执行」后代为操作界面。

三条硬规矩，写在代码里而不是写在提示词里：

1. **只能做白名单里的事。** 模型返回的每个动作都要在 `CAPABILITIES` 里查得到、参数过校验，
   否则直接丢弃并如实告诉前端"这个动作我不认识，没排进去"。提示词里的叮嘱不是安全边界。
2. **agent 永远不执行任何东西。** 服务端只产出「提议」，真正发请求的是前端，而且每一步都要人点
   「执行」；`risk='danger'` 的动作（清空数据、替换导入）还要再输一次确认词。用户始终看得见
   界面在被谁、往哪儿驱动。
3. **不碰密钥。** 能力清单里没有 `settings/llm`、`settings/embeddings` 的任何写操作；
   规划器拿到的系统快照也不包含 key 字段（用 `public_config` 那套掩码）。

没有模型时它照样能用：`plan_offline()` 用规则识别几类常见请求（查状态、问概念、要跳转），
问概念的答桉直接来自仓库里的文档并带出处 —— 演示模式不许假装是模型回答的。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import config, embeddings, events, llm, releases, services
from .db import Database

MAX_ACTIONS = 6                 # 一轮最多提议几步：再多用户就看不懂自己在批准什么了
MAX_TURNS = 4                   # 一次提问允许连续推进几轮
MIN_WHY = 4                     # 「为什么做这一步」的最短长度
DOC_FILES = ("README.md", "API.md", "ARCHITECTURE.md", "CHANGELOG.md",
             "CONTRIBUTING.md", "SECURITY.md", "PACK_SPEC.md", "IMPROVEMENT_PLAN.md")
DOC_CHUNK_CHARS = 1400
DOC_TOP_K = 4

# ---------------------------------------------------------------------------
# 能力清单
# ---------------------------------------------------------------------------
# risk: read 只读 / write 会改数据（点一次执行）/ danger 破坏性（点一次 + 输确认词）
CAPABILITIES: tuple[dict[str, Any], ...] = (
    # —— 看：全部是现成的 GET 接口，agent 靠它们获取事实，不靠猜 ——
    {"id": "view.dashboard", "kind": "api", "method": "GET", "path": "/api/dashboard",
     "risk": "read", "args": {}, "desc": "读今日闭环：进度、到期数、下一步建议"},
    {"id": "view.review", "kind": "api", "method": "GET", "path": "/api/review?limit=40",
     "risk": "read", "args": {}, "desc": "读复习队列（哪些卡到期、哪些是新卡）"},
    {"id": "view.cards", "kind": "api", "method": "GET", "path": "/api/cards?limit=40",
     "risk": "read", "args": {"q": "str?"}, "desc": "按关键词查卡片"},
    {"id": "view.map", "kind": "api", "method": "GET", "path": "/api/map",
     "risk": "read", "args": {}, "desc": "读知识地图：单元与前置关系"},
    {"id": "view.unit", "kind": "api", "method": "GET", "path": "/api/units/{unit_id}",
     "risk": "read", "args": {"unit_id": "str"}, "desc": "读一个知识单元的完整内容"},
    {"id": "view.insights", "kind": "api", "method": "GET", "path": "/api/insights",
     "risk": "read", "args": {}, "desc": "读学习证据：掌握度、火候、记忆健康度、模型校准"},
    {"id": "view.notes", "kind": "api", "method": "GET", "path": "/api/notes",
     "risk": "read", "args": {}, "desc": "读灵感库"},
    {"id": "view.events", "kind": "api", "method": "GET", "path": "/api/events?limit=30",
     "risk": "read", "args": {}, "desc": "读最近发生了什么（追加式事件日志）"},
    {"id": "view.search", "kind": "api", "method": "GET", "path": "/api/search?q={q}",
     "risk": "read", "args": {"q": "str"}, "desc": "全站搜索单元与内容包"},
    {"id": "view.releases", "kind": "api", "method": "GET", "path": "/api/releases",
     "risk": "read", "args": {}, "desc": "读当前版本与更新内容"},
    {"id": "view.health", "kind": "api", "method": "GET", "path": "/api/health",
     "risk": "read", "args": {}, "desc": "读运行状态：模型模式、内容包数量、数据目录"},
    {"id": "view.duplicates", "kind": "api", "method": "GET", "path": "/api/units/duplicates",
     "risk": "read", "args": {}, "desc": "读疑似重复的单元对（带字面/语义两条证据）"},
    # —— 去：只改前端路由/焦点，不动数据 ——
    {"id": "go.route", "kind": "ui", "action": "navigate", "risk": "read",
     "args": {"route": "route"}, "desc": "把用户带到某个页面（today/map/review/library/insights/notes/settings）"},
    {"id": "go.unit", "kind": "ui", "action": "open_unit", "risk": "read",
     "args": {"unit_id": "str"}, "desc": "打开一个知识单元的页面"},
    {"id": "go.search", "kind": "ui", "action": "open_search", "risk": "read",
     "args": {"q": "str?"}, "desc": "打开搜索框并填好关键词"},
    # —— 做：会改学习数据，前端点一次「执行」才发生 ——
    {"id": "review.rate", "kind": "api", "method": "POST", "path": "/api/review/{card_id}",
     "risk": "write", "args": {"card_id": "str", "rating": "rating", "recall": "str?"},
     "desc": "给一张卡评分（1 忘了 / 2 模糊 / 3 记得 / 4 很简单），会改变下次复习时间"},
    {"id": "review.undo", "kind": "api", "method": "POST", "path": "/api/reviews/undo",
     "risk": "write", "args": {"op": "str"}, "desc": "撤销刚才那次评分"},
    {"id": "focus.start", "kind": "api", "method": "POST", "path": "/api/focus/start",
     "risk": "write", "args": {"minutes": "minutes"}, "desc": "开始一个番茄钟（15/25/45 分钟）"},
    {"id": "focus.finish", "kind": "api", "method": "POST", "path": "/api/focus/finish",
     "risk": "write", "args": {"session": "str", "completed": "bool"},
     "desc": "结束番茄钟；只有 completed=true 才计入学习时长"},
    {"id": "note.create", "kind": "api", "method": "POST", "path": "/api/notes",
     "risk": "write", "args": {"quote": "str?", "content": "str", "unit_id": "str?"},
     "desc": "存一条灵感"},
    {"id": "unit.mnemonic", "kind": "api", "method": "POST", "path": "/api/units/{unit_id}/mnemonic",
     "risk": "write", "args": {"unit_id": "str"}, "desc": "让模型给一个单元编口诀"},
    {"id": "insights.synthesize", "kind": "api", "method": "POST", "path": "/api/insights/synthesize",
     "risk": "write", "args": {}, "desc": "生成一段本周学习解读"},
    {"id": "plan.rebuild", "kind": "api", "method": "POST", "path": "/api/plan/rebuild",
     "risk": "write", "args": {"daily_minutes": "minutes?", "deadline": "str?"},
     "desc": "按新的每日投入重排学习路径（已烧开的单元不会退回）"},
    {"id": "units.merge", "kind": "api", "method": "POST", "path": "/api/units/{from_id}/merge",
     "risk": "write", "args": {"from_id": "str", "into_id": "str", "reason": "str", "confirmed": "bool"},
     "desc": "把一个单元并进另一个（会重写前置关系，可撤销但要写明理由）"},
    {"id": "units.unmerge", "kind": "api", "method": "POST", "path": "/api/units/{unit_id}/unmerge",
     "risk": "write", "args": {"unit_id": "str"}, "desc": "撤销一次合并"},
    # —— 危险：agent 可以提议，但前端必须输确认词，且永远不替用户输 ——
    {"id": "data.reset", "kind": "api", "method": "POST", "path": "/api/reset",
     "risk": "danger", "args": {"confirm": "confirm_reset"},
     "desc": "清空本机全部学习数据（不可撤销）"},
    {"id": "data.export", "kind": "api", "method": "DOWNLOAD", "path": "/api/export",
     "risk": "read", "args": {"filename": "str?"}, "desc": "导出全部学习数据为 JSON 文件"},
    {"id": "data.export_anki", "kind": "api", "method": "DOWNLOAD", "path": "/api/export/anki",
     "risk": "read", "args": {"filename": "str?"}, "desc": "导出 Anki 文本"},
)

BY_ID = {c["id"]: c for c in CAPABILITIES}

ROUTES = {"today": "#/dashboard", "dashboard": "#/dashboard", "map": "#/map", "review": "#/review",
          "library": "#/library", "insights": "#/insights", "notes": "#/notes",
          "settings": "#/settings", "onboarding": "#/onboarding"}
RATINGS = {1, 2, 3, 4}
MINUTES = {15, 25, 45}
_ARG_NAME = re.compile(r"^[a-z][a-z0-9_]{0,24}$")


def capabilities() -> list[dict[str, Any]]:
    """给前端渲染权限说明用；不含任何路径拼装细节之外的信息。"""
    return [{"id": c["id"], "risk": c["risk"], "desc": c["desc"], "kind": c["kind"],
             "args": {k: v for k, v in c["args"].items()}} for c in CAPABILITIES]


def _check_arg(name: str, spec: str, value: Any) -> tuple[bool, Any, str]:
    """参数校验：类型 + 取值范围。返回 (是否通过, 清洗后的值, 失败原因)。"""
    optional = spec.endswith("?")
    base = spec[:-1] if optional else spec
    if base == "confirm_reset":
        # 确认词永远由用户在界面上亲手输入：模型填不填、填什么都被忽略（见 validate_action）
        return True, "", ""
    if value is None or value == "":
        if optional:
            return True, None, ""
        return False, None, f"缺少参数 {name}"
    if base == "str":
        text = str(value).strip()
        return (True, text[:600], "") if text else (False, None, f"{name} 是空的")
    if base == "int":
        try:
            return True, int(value), ""
        except (TypeError, ValueError):
            return False, None, f"{name} 不是整数"
    if base == "bool":
        if isinstance(value, bool):
            return True, value, ""
        if str(value).lower() in ("true", "1", "yes"):
            return True, True, ""
        if str(value).lower() in ("false", "0", "no"):
            return True, False, ""
        return False, None, f"{name} 不是布尔值"
    if base == "rating":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return False, None, f"{name} 不是 1–4 的评分"
        return (True, n, "") if n in RATINGS else (False, None, f"{name} 只能是 1–4")
    if base == "minutes":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return False, None, f"{name} 不是分钟数"
        return (True, n, "") if n in MINUTES else (False, None, f"{name} 只能是 15/25/45")
    if base == "route":
        key = str(value).strip().lower().lstrip("#/")
        return (True, key, "") if key in ROUTES else (False, None, f"没有这个页面：{value}")
    return False, None, f"{name} 的类型定义不认识：{base}"


def _safe_id(value: Any) -> str:
    """参数值只做长度截断；真正防注入的是前端填路径时的 encodeURIComponent。"""
    return str(value or "").strip()[:120]


def validate_action(raw: Any) -> dict[str, Any]:
    """把一个模型给出的动作变成可执行/可拒绝的结论。"""
    if not isinstance(raw, dict):
        return {"ok": False, "id": "?", "reason": "动作不是一个对象"}
    cid = str(raw.get("id") or "").strip()
    cap = BY_ID.get(cid)
    if not cap:
        return {"ok": False, "id": cid[:40] or "?",
                "reason": "这个动作不在能力清单里，我不会执行它"}
    args_raw = raw.get("args")
    if args_raw is None:
        args_raw = {}
    if not isinstance(args_raw, dict):
        return {"ok": False, "id": cid, "reason": f"{cid} 的参数必须是对象"}
    clean: dict[str, Any] = {}
    for name, spec in cap["args"].items():
        ok, value, reason = _check_arg(name, spec, args_raw.get(name))
        if not ok:
            return {"ok": False, "id": cid, "reason": reason}
        if spec == "confirm_reset":
            continue                       # 确认词不进提议里：它只能来自用户亲手输入
        if value is not None:
            clean[name] = _safe_id(value) if spec == "str" else value
    extra = set(args_raw) - set(cap["args"])
    suppressed = [name for name, spec in cap["args"].items()
                  if spec == "confirm_reset" and str(args_raw.get(name) or "").strip()]
    why = str(raw.get("why") or "").strip()
    out: dict[str, Any] = {
        "ok": True, "id": cid, "risk": cap["risk"], "args": clean,
        "desc": cap["desc"], "why": why[:200], "kind": cap["kind"],
        "ignored_args": sorted(extra),
    }
    if cap["kind"] == "api":
        path = cap["path"]
        names = re.findall(r"\{(\w+)\}", path)
        for name in names:
            if name not in clean:
                return {"ok": False, "id": cid, "reason": f"{cid} 缺少路径参数 {name}"}
        out["method"] = cap["method"]
        out["path"] = path                      # 模板原样交给前端，参数由它 encodeURIComponent 填
        out["path_args"] = names
    else:
        out["action"] = cap["action"]
    if suppressed:
        # 模型想替用户按下确认键 —— 值被抹掉，动作仍然提议，但解锁只能由人来做
        out["confirm_suppressed"] = suppressed
    if cap["risk"] == "danger":
        out["requires_typed_confirm"] = True
        out["confirm_hint"] = "这一步会毁掉数据，必须你亲手确认；我只是提议，不会替你点。"
    if cap["risk"] != "read" and len(why) < MIN_WHY:
        out["why_missing"] = "请让它说明为什么要做这一步"
    return out


# ---------------------------------------------------------------------------
# 文档索引：回答「关于系统的问题」时给得出处
# ---------------------------------------------------------------------------
_INDEX: dict[str, Any] = {"stamp": "", "chunks": []}


def _doc_stamp() -> str:
    parts = []
    for name in DOC_FILES:
        path = config.ROOT / name
        try:
            stat = path.stat()
            parts.append(f"{name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{name}:missing")
    return "|".join(parts)


def _chunk_document(name: str, text: str) -> list[dict[str, Any]]:
    """按标题切块，保留 `API.md · GET /api/health` 这样的定位，回答时能报出处。"""
    chunks: list[dict[str, Any]] = []
    section = name
    buffer: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            if buffer:
                chunks.append({"source": name, "section": section, "text": "\n".join(buffer).strip()})
                buffer = []
            section = f"{name} · {heading.group(2).strip()[:80]}"
            continue
        buffer.append(line)
        if sum(len(x) for x in buffer) > DOC_CHUNK_CHARS:
            chunks.append({"source": name, "section": section, "text": "\n".join(buffer).strip()})
            buffer = []
    if buffer:
        chunks.append({"source": name, "section": section, "text": "\n".join(buffer).strip()})
    return [c for c in chunks if len(c["text"]) > 40]


def doc_chunks() -> list[dict[str, Any]]:
    stamp = _doc_stamp()
    if _INDEX["stamp"] != stamp:
        chunks: list[dict[str, Any]] = []
        for name in DOC_FILES:
            text = config.read_text(config.ROOT / name)
            if text:
                chunks.extend(_chunk_document(name, text))
        _INDEX["stamp"] = stamp
        _INDEX["chunks"] = chunks
    return _INDEX["chunks"]


# 问句里的功能词不该参与匹配：「记忆健康度是怎么算的」真正要检索的是「记忆健康度」
STOP_PATTERNS = re.compile(
    r"(是怎么|怎么|怎样|如何|为什么|为何|是什么|什么意思|请问|解释一下|的|了|吗|呢|啊|吧|要|会|能|可以)")


def _grams(term: str) -> list[str]:
    stripped = STOP_PATTERNS.sub("", term).strip() or term
    words = [w for w in re.split(r"[\s,，。、；;:：()（）`#*|？?！!]+", stripped) if len(w) >= 2]
    if any(" " in stripped for _ in words) and len(words) > 1:
        return words
    if len(words) == 1 and re.search(r"[a-z]", stripped):
        return words                                   # 纯英文/接口名，按词匹配
    core = "".join(words) if words else stripped
    return [core[i:i + 2] for i in range(len(core) - 1)] if len(core) >= 2 else [core]


def _lexical(needle: str, chunk: dict[str, Any]) -> float:
    """关键词命中：标题命中比正文命中值钱得多 —— 问「合并单元」时该先命中
    「单元合并（合并不是删除）」那一节，而不是正文里恰好出现过「单元」的段落。"""
    term = needle.strip().lower()
    if not term:
        return 0.0
    head, body = chunk["section"].lower(), chunk["text"].lower()
    grams = _grams(term)
    score = 4.0 if term in head else (3.0 if term in body else 0.0)
    score += 2.5 * sum(1 for g in grams if g in head) / len(grams)
    score += 1.5 * sum(1 for g in grams if g in body) / len(grams)
    return score


def search_docs(question: str, limit: int = DOC_TOP_K) -> list[dict[str, Any]]:
    """文档检索：字面为主，语义只在云端向量可用时加分（不拿本地哈希向量冒充语义分）。"""
    chunks = doc_chunks()
    if not chunks or not question.strip():
        return []
    lex_scores = [_lexical(question, c) for c in chunks]
    semantic: dict[int, float] = {}
    if embeddings.enabled():
        # 254 个块一起问一次：embed_texts 内部按文本做磁盘缓存，第二次只有问题是新的
        vectors = embeddings.embed_texts([question] + [c["text"][:900] for c in chunks])
        if vectors and len(vectors) == len(chunks) + 1:
            for i, chunk_vec in enumerate(vectors[1:]):
                semantic[i] = embeddings.cosine(vectors[0], chunk_vec)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for i, chunk in enumerate(chunks):
        lex = lex_scores[i]
        sem = semantic.get(i)
        # 门槛要够硬：短问题靠零星二元组也能蹭到一堆无关段落，宁可不引。
        # 完全没有字面信号时更严 —— 实测 bge-m3 在中文小语料上对无意义输入也能给到 0.4 上下，
        # 放它进来的结果是"胡说八道 → 系统认真引用一段文档"。
        if lex >= 1.2:
            pass
        elif lex > 0 and (sem or 0) >= 0.42:
            pass
        elif lex == 0 and (sem or 0) >= 0.55:
            pass
        else:
            continue
        score = lex + (sem or 0) * (1.5 if lex > 0 else 3.0)
        ranked.append((score, {**chunk, "lexical": round(lex, 3),
                               "semantic": round(sem, 3) if sem is not None else None}))
    ranked.sort(key=lambda x: -x[0])
    out = []
    for _score, chunk in ranked[:limit]:
        out.append({**chunk, "text": chunk["text"][:1200]})
    return out


# ---------------------------------------------------------------------------
# 系统快照：让 agent 用事实回答，而不是背说明书
# ---------------------------------------------------------------------------
def snapshot(db: Database, user_id: str = "local") -> dict[str, Any]:
    """压缩过的实时状态。刻意不带任何 key/base_url 字段。"""
    out: dict[str, Any] = {"version": config.VERSION, "mode": llm.mode()}
    try:
        dash = services.dashboard(db, user_id)
        counts = dash.get("counts") or {}
        out["goal"] = (dash.get("goal") or {}).get("title")
        out["counts"] = {k: counts.get(k) for k in ("units", "boiled", "due", "new", "learning", "rusting")}
        out["progress_percent"] = ((dash.get("plan") or {}).get("progress") or {}).get("percent")
        out["next_units"] = [u.get("title") for u in (dash.get("next_units") or [])[:3]]
        out["today_minutes"] = (dash.get("stats") or {}).get("today_minutes")
        out["streak"] = (dash.get("stats") or {}).get("streak")
    except Exception as exc:      # 没目标 / 数据不全都不该让 agent 失声
        out["dashboard_error"] = str(exc)[:120]
    try:
        ins = services.insights(db, user_id)
        out["mastery_average"] = (ins.get("mastery") or {}).get("average")
        out["memory_health"] = ins.get("memory_health")
        out["memory_health_settled"] = ins.get("memory_health_settled")
        cal = ins.get("calibration") or {}
        out["calibration"] = {"samples": cal.get("samples"), "bias": cal.get("bias"),
                              "enough": cal.get("enough")}
    except Exception:
        pass
    try:
        recent = events.recent(db, user_id, limit=6)
        out["recent_events"] = [f"{e['type']} @ {e['created_at'][:16]}" for e in recent]
    except Exception:
        pass
    try:
        emb = embeddings.public_config()
        out["embeddings"] = emb.get("mode")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 规划
# ---------------------------------------------------------------------------
def _history_block(history: list[dict[str, Any]] | None) -> str:
    lines = []
    for item in (history or [])[-6:]:
        role = "用户" if item.get("role") == "user" else "助手"
        lines.append(f"{role}：{str(item.get('content') or '')[:400]}")
    return "\n".join(lines)


def plan(db: Database, user_id: str, question: str,
         history: list[dict[str, Any]] | None = None,
         observations: list[dict[str, Any]] | None = None,
         turn: int = 1) -> dict[str, Any]:
    """把一句话变成一个回答 + 一串待批准的动作。"""
    question = str(question or "").strip()[:2000]
    if not question and not observations:
        raise ValueError("先告诉我你想做什么")
    docs = search_docs(question) if question else []
    snap = snapshot(db, user_id)
    if not llm.enabled():
        return plan_offline(db, user_id, question, docs, snap, turn)
    prompt = llm.load_prompt("agent_plan.md")
    system = (prompt
              .replace("{capabilities}", json.dumps(
                  [{"id": c["id"], "risk": c["risk"], "desc": c["desc"], "args": c["args"]}
                   for c in CAPABILITIES], ensure_ascii=False))
              .replace("{snapshot}", json.dumps(snap, ensure_ascii=False))
              .replace("{docs}", "\n\n".join(
                  f"[{d['source']} · {d['section']}]\n{d['text']}" for d in docs) or "（没有检索到相关文档）")
              .replace("{turn}", str(turn))
              .replace("{max_turns}", str(MAX_TURNS)))
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for item in (history or [])[-6:]:
        role = item.get("role") if item.get("role") in ("user", "assistant") else "user"
        messages.append({"role": role, "content": str(item.get("content") or "")[:1500]})
    if observations:
        messages.append({"role": "user", "content": "刚才那些动作的执行结果：\n"
                         + json.dumps(observations[-8:], ensure_ascii=False)[:4000]})
    messages.append({"role": "user", "content": question or "（继续上一步，看看结果）"})
    try:
        data = llm.chat_json(messages, temperature=0.2, max_tokens=1200)
    except llm.LLMError as exc:
        fallback = plan_offline(db, user_id, question, docs, snap, turn)
        fallback["engine"] = "rules"
        fallback["reply"] = f"模型这次没答上（{exc}）。下面是本地规则能给的部分：\n\n{fallback['reply']}"
        fallback["ai_note"] = str(exc)[:200]
        return fallback
    return _finalize(data, docs, snap, turn, engine="model")


def _finalize(data: Any, docs: list[dict[str, Any]], snap: dict[str, Any],
              turn: int, engine: str) -> dict[str, Any]:
    if isinstance(data, str):
        data = {"reply": data}
    if not isinstance(data, dict):
        data = {}
    actions = []
    rejected = []
    for raw in (data.get("actions") or [])[:MAX_ACTIONS * 2]:
        item = validate_action(raw)
        (actions if item.get("ok") else rejected).append(item)
    actions = actions[:MAX_ACTIONS]
    known_sources = {d["source"] for d in docs}
    citations = [str(c)[:120] for c in (data.get("citations") or []) if str(c).strip()]
    kept = [c for c in citations if c.split(" · ")[0] in known_sources]
    invented = [c for c in citations if c not in kept]
    if invented:
        # 引用不许编：出处不在检索到的文档里，就明说而不是悄悄丢掉
        rejected.append({"id": "?", "ok": False,
                         "reason": "这几个出处在仓库文档里找不到，已剔除：" + "、".join(invented[:3])})
    return {
        "reply": str(data.get("reply") or "").strip()[:4000],
        "actions": actions,
        "rejected": rejected,
        "citations": kept[:6],
        "engine": engine,
        "turn": turn,
        "can_continue": bool(actions) and turn < MAX_TURNS,
        "snapshot": snap,
        "docs_consulted": [{"source": d["source"], "section": d["section"]} for d in docs],
    }


# ---------------------------------------------------------------------------
# 演示模式：没有模型也要能用，而且不许假装是模型回答的
# ---------------------------------------------------------------------------
_INTENTS = (
    ("到期", "view.review"), ("还有多少卡", "view.review"), ("复习", "view.review"),
    ("版本", "view.releases"), ("更新", "view.releases"),
    ("校准", "view.insights"), ("掌握度", "view.insights"), ("健康度", "view.insights"),
    ("重复", "view.duplicates"), ("灵感", "view.notes"), ("笔记", "view.notes"),
    ("地图", "go.route"), ("内容库", "go.route"), ("设置", "go.route"),
    ("专注", "focus.start"), ("番茄", "focus.start"),
)


DATA_WORDS = ("我", "今天", "还剩", "到期", "进度", "掌握", "版本", "更新", "灵感",
              "笔记", "几张", "多少", "状态", "校准", "健康度", "重复")
DOC_WORDS = ("怎么", "如何", "为什么", "是什么", "什么意思", "能不能", "会不会", "在哪",
             "原理", "口径", "支持", "可不可以", "解释一下", "区别")


def plan_offline(db: Database, user_id: str, question: str,
                 docs: list[dict[str, Any]], snap: dict[str, Any], turn: int) -> dict[str, Any]:
    text = (question or "").strip()
    low = text.lower()
    actions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    lines: list[str] = []
    # 「我还有几张卡到期」要答数据，「健康度是怎么算的」要答文档 —— 顺序反了就像在打太极
    asked_how = any(w in text for w in DOC_WORDS)
    wants_data = any(w in text for w in DATA_WORDS)
    wants_docs = asked_how or not wants_data

    if any(k in low for k in ("你好", "你是谁", "能做什么", "帮助", "help")):
        lines.append("我是这个系统自己的助手。我能做三类事：**查你的学习数据**、**回答关于 Boil 的问题**"
                     "（答案来自仓库里的文档，会标出处）、**替你把界面点开**。\n\n"
                     "每一步都要你点「执行」才会发生；清空数据这类动作还要你再输一次确认词。"
                     "现在没配置模型，所以只能按关键词理解你的话 —— 说具体一点更准。")
    else:
        for key, cap_id in _INTENTS:
            if key in low and not any(a["id"] == cap_id for a in actions):
                args: dict[str, Any] = {}
                if cap_id == "go.route":
                    args = {"route": {"地图": "map", "内容库": "library", "设置": "settings"}[key]}
                if cap_id == "focus.start":
                    args = {"minutes": 25}
                item = validate_action({"id": cap_id, "args": args, "why": f"你说到了「{key}」"})
                (actions if item.get("ok") else rejected).append(item)

    counts = snap.get("counts") or {}
    has_goal = bool(snap.get("goal"))
    if wants_data and has_goal:
        def pct_or_dash(key: str) -> str:
            value = snap.get(key)
            return "—" if value is None else f"{round(value * 100)}%"
        lines.append(
            f"你现在的状态：目标「{snap.get('goal')}」，"
            f"{counts.get('boiled') or 0}/{counts.get('units') or 0} 个单元已烧开，"
            f"{counts.get('due') or 0} 张卡到期、{counts.get('new') or 0} 张新卡；"
            f"平均掌握度 {pct_or_dash('mastery_average')}，"
            f"记忆健康度 {pct_or_dash('memory_health')}"
            f"（剔掉今天刚复习的卡是 {pct_or_dash('memory_health_settled')}）。"
            f"点下面的「执行」就能看到明细。")
    elif wants_data and not asked_how:
        lines.append("你还没有学习目标，所以没有数据可看。先去创建一个：把材料粘进来，或者挑一个内容包。")
    # 纯指令（「打开地图」）别附带讲座；只有真的在问"怎么/为什么"才引文档
    if wants_docs and actions and not any(w in text for w in DOC_WORDS):
        docs = []
    if wants_docs and docs:
        best = docs[0]
        lines.append(f"关于怎么用：根据 {best['section']}：\n\n> {best['text'][:600]}")
        extra = docs[1:3]
        if extra:
            lines.append("相关出处：" + "、".join(d["section"] for d in extra))
    elif asked_how and not docs:
        lines.append("仓库文档里我没检索到直接回答这句话的段落 —— 换个说法，或者把问题问得更具体一点。")
    if actions:
        lines.append("下面这一步要不要执行，你点一下才算。")
    if not lines:
        lines.append("这句话我按关键词没对上能做的事。可以试试：「今天该学什么」「我还有几张卡到期」"
                     "「记忆健康度是怎么算的」「合并单元会不会丢内容」「打开地图」「开个 25 分钟专注」。"
                     "配置模型之后，我才能真的理解长问题并分多步执行。")

    data = {"reply": "\n\n".join(lines), "actions": [a for a in actions if a.get("ok")]}
    out = _finalize(data, docs, snap, turn, engine="rules")
    out["rejected"] = rejected
    out["citations"] = [d["section"] for d in docs[:3]]        # 规则模式也要交代引了哪几段
    return out
