"""Boil MCP stdio server（零依赖）。

用法：
    python3 -m boil.mcp_server

支持 MCP 的 initialize / tools/list / tools/call / ping。
工具直接调用本地服务层，不经过 HTTP；与 Web 共享同一个 data/boil.db。

安全：
- stdio 本地进程；不监听端口。
- 评分/费曼等写操作在描述里要求“只有用户明确要求时才调用”。
- 密钥不会出现在工具结果中。
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import advisor, config, embeddings, feynman, llm, services, socratic
from .db import Database
from .packs import get_goal_units
from .utils import truncate

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "boil", "version": config.VERSION}


def _compact_unit(detail: dict[str, Any]) -> dict[str, Any]:
    u = detail["unit"]
    return {
        "id": u["id"], "title": u["title"], "essence": u["essence"],
        "steps": u.get("steps") or [], "boundaries": u.get("boundaries") or [],
        "misconceptions": u.get("misconceptions") or [],
        "key_points": u.get("key_points") or [],
        "heat": detail.get("heat"), "heat_label": detail.get("heat_label"),
        "mastery": {f: (detail.get("mastery", {}).get("facets", {}).get(f, {}) or {}).get("score", 0)
                    for f in ("recall", "explain", "apply", "transfer")},
        "pn": detail.get("pn"),
        "notes": detail.get("notes"),
        "related": detail.get("related"),
        "probes": [{"kind": p.get("kind"), "prompt": p.get("prompt")}
                   for p in (detail.get("probes") or [])[:8]],
    }


def _compact_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    return {
        "summary": report.get("summary"),
        "understood": report.get("understood"),
        "gaps": report.get("gaps"),
        "misconceptions": report.get("misconceptions"),
        "facets": report.get("facets"),
        "next_step": report.get("next_step"),
        "confidence": report.get("confidence"),
        "engine": report.get("engine"),
    }


class Tools:
    def __init__(self):
        config.ensure_dirs()
        self.db = Database()
        services.ensure_user(self.db, config.DEMO_USER_NAME)
        self.user = config.DEFAULT_USER

    # ---------- read ----------
    def health(self) -> dict[str, Any]:
        goal = services.active_goal(self.db, self.user)
        return {
            "app": "Boil", "version": config.VERSION,
            "llm": {"mode": llm.mode(), "model": llm.model_name(True)},
            "embeddings": {k: v for k, v in embeddings.public_config().items()
                           if k in ("mode", "model", "dim", "last_error")},
            "goal": {"id": goal["id"], "title": goal["title"]} if goal else None,
        }

    def advice(self, minutes: int | None = None, goal_id: str | None = None) -> dict[str, Any]:
        return advisor.build_advice(self.db, self.user, goal_id, minutes)

    def list_units(self, limit: int = 20) -> dict[str, Any]:
        goal = services.active_goal(self.db, self.user)
        if not goal:
            return {"goal": None, "units": [], "hint": "还没有目标，先让用户在 Web 上创建目标。"}
        dash = services.dashboard(self.db, self.user, goal["id"])
        out = []
        for u in dash.get("next_units", []):
            out.append({
                "id": u["id"], "title": u["title"], "status": u["status"],
                "heat": u.get("heat"), "due_count": u.get("due_count"),
                "estimated_minutes": u.get("estimated_minutes"),
            })
        for uid, st in (dash.get("statuses") or {}).items():
            if any(x["id"] == uid for x in out):
                continue
            if len(out) >= limit:
                break
            unit = next((x for x in get_goal_units(self.db, goal["id"]) if x["id"] == uid), None)
            if unit:
                out.append({"id": uid, "title": unit["title"], "status": st.get("status"),
                            "heat": st.get("heat"), "due_count": st.get("due_count")})
        return {"goal": {"id": goal["id"], "title": goal["title"]}, "units": out[:limit]}

    def get_unit(self, unit_id: str) -> dict[str, Any]:
        return _compact_unit(services.unit_detail(self.db, self.user, unit_id))

    def search(self, q: str) -> dict[str, Any]:
        return services.search(self.db, self.user, q)

    def review_queue(self, limit: int = 20) -> dict[str, Any]:
        data = services.review_queue(self.db, self.user, limit=limit)
        cards = [{
            "id": c["id"], "front": c["front"], "back": c["back"],
            "unit_title": c.get("unit_title"), "state": c.get("state"),
            "preview": c.get("preview"), "due_label": c.get("due_label"),
            "mnemonic": c.get("mnemonic"),
        } for c in data.get("cards", [])]
        return {"cards": cards, "due_total": data.get("due_total"),
                "new_total": data.get("new_total"), "stats": data.get("stats")}

    # ---------- write ----------
    def submit_review(self, card_id: str, rating: int, elapsed_ms: int = 0) -> dict[str, Any]:
        return services.rate_card(self.db, self.user, card_id, int(rating), int(elapsed_ms or 0))

    def save_note(self, unit_id: str, kind: str, content: str) -> dict[str, Any]:
        return services.save_unit_note(self.db, self.user, unit_id, kind, content)

    def start_socratic(self, unit_id: str, advice_id: str | None = None) -> dict[str, Any]:
        detail = services.unit_detail(self.db, self.user, unit_id)
        data = socratic.start_session(self.db, self.user, detail["unit"]["goal_id"], unit_id,
                                      advice_id=advice_id)
        messages = data["session"].get("messages") or []
        return {
            "session_id": data["session"]["id"],
            "unit_id": unit_id,
            "stage": data["session"].get("stage"),
            "first_question": messages[0]["content"] if messages else "",
        }

    def socratic_reply(self, session_id: str, message: str) -> dict[str, Any]:
        events = list(socratic.stream_reply(self.db, self.user, session_id, message))
        text = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
        done = next((e for e in reversed(events) if e.get("type") == "done"), None) or {}
        return {
            "reply": text.strip(),
            "report": _compact_report(done.get("report")),
            "engine": done.get("engine"),
            "session": {k: (done.get("session") or {}).get(k)
                        for k in ("id", "status", "stage", "turn")},
        }

    def finish_socratic(self, session_id: str) -> dict[str, Any]:
        data = socratic.finish_session(self.db, self.user, session_id)
        return {"session": {"id": data["session"]["id"], "status": data["session"]["status"]},
                "report": _compact_report(data.get("report"))}

    def feynman_evaluate(self, unit_id: str, text: str, mode: str = "peer") -> dict[str, Any]:
        row = self.db.one("SELECT * FROM units WHERE id=?", (unit_id,))
        if not row:
            raise ValueError("单元不存在")
        from .packs import unit_from_row
        unit = unit_from_row(row)
        result = feynman.evaluate(self.db, self.user, unit, text, mode)
        return {
            "overall": result.get("overall"), "dimensions": result.get("dimensions"),
            "gaps": result.get("gaps"), "jargon": result.get("jargon"),
            "model_answer": result.get("model_answer"), "followups": result.get("followups"),
            "next_step": result.get("next_step"), "engine": result.get("engine"),
            "mode": result.get("mode"),
        }

    def insights(self) -> dict[str, Any]:
        goal = services.active_goal(self.db, self.user)
        if not goal:
            return {"error": "还没有学习目标"}
        data = services.insights(self.db, self.user, goal["id"])
        return {
            "goal": {"id": goal["id"], "title": goal["title"]},
            "mastery": data.get("mastery"), "counts": data.get("counts"),
            "average_heat": data.get("average_heat"),
            "stats": data.get("stats"), "gaps": data.get("gaps"),
            "next_action": data.get("next_action"),
            "records": data.get("records", [])[:6],
        }


TOOL_SPECS = [
    {
        "name": "boil_health",
        "description": "查看 Boil 当前模式、模型、Embedding 状态与当前目标。只读。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "boil_get_advice",
        "description": "获取当前唯一的学习下一步建议：模式、目标、理由、成本、PN 缺口与时间预算。只读。",
        "inputSchema": {"type": "object", "properties": {
            "minutes": {"type": "integer", "minimum": 5, "maximum": 240},
            "goal_id": {"type": "string"},
        }, "additionalProperties": False},
    },
    {
        "name": "boil_list_units",
        "description": "列出当前目标的单元、状态、火候和到期数。只读。",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        }, "additionalProperties": False},
    },
    {
        "name": "boil_get_unit",
        "description": "读取一个单元的完整学习面：本质、步骤、边界、误解、探针、掌握、PN 缺口、速记与相关单元。只读。",
        "inputSchema": {"type": "object", "properties": {"unit_id": {"type": "string"}},
                        "required": ["unit_id"], "additionalProperties": False},
    },
    {
        "name": "boil_search",
        "description": "语义/关键词检索当前目标的单元；返回得分与实际向量引擎。只读。",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}},
                        "required": ["q"], "additionalProperties": False},
    },
    {
        "name": "boil_review_queue",
        "description": "列出到期卡与新卡，包含四档间隔预览和速记线索。只读。",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        }, "additionalProperties": False},
    },
    {
        "name": "boil_submit_review",
        "description": "对一张卡提交 1–4 评分并推进 FSRS。⚠️ 只有在用户明确说出评分后才调用，禁止自动评分或替用户决定。",
        "inputSchema": {"type": "object", "properties": {
            "card_id": {"type": "string"},
            "rating": {"type": "integer", "minimum": 1, "maximum": 4},
            "elapsed_ms": {"type": "integer", "minimum": 0, "default": 0},
        }, "required": ["card_id", "rating"], "additionalProperties": False},
    },
    {
        "name": "boil_save_note",
        "description": "保存单元速记（mnemonic）或笔记（note）。content 为空会删除该条。",
        "inputSchema": {"type": "object", "properties": {
            "unit_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["mnemonic", "note"]},
            "content": {"type": "string"},
        }, "required": ["unit_id", "kind", "content"], "additionalProperties": False},
    },
    {
        "name": "boil_start_socratic",
        "description": "为单元开始苏格拉底对话，返回 session_id 与第一个问题。写操作（创建会话）。",
        "inputSchema": {"type": "object", "properties": {
            "unit_id": {"type": "string"}, "advice_id": {"type": "string"},
        }, "required": ["unit_id"], "additionalProperties": False},
    },
    {
        "name": "boil_socratic_reply",
        "description": "向苏格拉底会话发送学生的回答，返回教练下一问；第 6 轮会自动收束并返回报告。写操作。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": {"type": "string"}, "message": {"type": "string"},
        }, "required": ["session_id", "message"], "additionalProperties": False},
    },
    {
        "name": "boil_finish_socratic",
        "description": "提前结束苏格拉底会话并生成证据报告。写操作。",
        "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}},
                        "required": ["session_id"], "additionalProperties": False},
    },
    {
        "name": "boil_feynman_evaluate",
        "description": "评估学生自己写的费曼讲解，返回六维分数、缺口与参考讲解。⚠️ 讲解必须由学生本人提供，不得由模型代写。写操作。",
        "inputSchema": {"type": "object", "properties": {
            "unit_id": {"type": "string"}, "text": {"type": "string"},
            "mode": {"type": "string", "enum": ["child", "peer", "examiner"], "default": "peer"},
        }, "required": ["unit_id", "text"], "additionalProperties": False},
    },
    {
        "name": "boil_insights",
        "description": "读取掌握雷达、火候、保留率、缺口与最近 BoilRecord。只读。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def dispatch(tools: Tools, name: str, args: dict[str, Any]) -> Any:
    if name == "boil_health":
        return tools.health()
    if name == "boil_get_advice":
        return tools.advice(args.get("minutes"), args.get("goal_id"))
    if name == "boil_list_units":
        return tools.list_units(int(args.get("limit", 20)))
    if name == "boil_get_unit":
        return tools.get_unit(str(args["unit_id"]))
    if name == "boil_search":
        return tools.search(str(args.get("q") or ""))
    if name == "boil_review_queue":
        return tools.review_queue(int(args.get("limit", 20)))
    if name == "boil_submit_review":
        return tools.submit_review(str(args["card_id"]), int(args["rating"]),
                                   int(args.get("elapsed_ms") or 0))
    if name == "boil_save_note":
        return tools.save_note(str(args["unit_id"]), str(args["kind"]),
                               str(args.get("content") or ""))
    if name == "boil_start_socratic":
        return tools.start_socratic(str(args["unit_id"]), args.get("advice_id"))
    if name == "boil_socratic_reply":
        return tools.socratic_reply(str(args["session_id"]), str(args.get("message") or ""))
    if name == "boil_finish_socratic":
        return tools.finish_socratic(str(args["session_id"]))
    if name == "boil_feynman_evaluate":
        return tools.feynman_evaluate(str(args["unit_id"]), str(args["text"]),
                                      str(args.get("mode") or "peer"))
    if name == "boil_insights":
        return tools.insights()
    raise ValueError(f"未知工具：{name}")


def handle(tools: Tools, msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if msg_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "Boil 是本地学习操作系统。先 boil_health / boil_get_advice；只有在用户明确要求时才写数据。",
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOL_SPECS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if not isinstance(args, dict):
                raise ValueError("arguments 必须是对象")
            data = dispatch(tools, name, args)
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                           "isError": False},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                           "isError": True},
            }
    if method in ("shutdown",):
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> None:
    tools = Tools()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            response = handle(tools, msg)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception:
            print("[boil-mcp] invalid message", traceback.format_exc(), file=sys.stderr)


if __name__ == "__main__":
    main()
