"""零依赖 HTTP 服务：REST + SSE + 静态 SPA。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import (advisor, agent, backup, config, embeddings, events, feynman, llm,
                markdown_fix, mastery, releases, services, socratic, styles)
from .compiler import compile_material
from .db import Database, json_loads
from .events import recent
from .packs import PackError, unit_from_row
from .utils import now_iso, parse_iso, truncate

MAX_FIX_CHARS = 200_000

STARTED = time.time()


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "error",
                 detail: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.detail = detail


def _json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    return str(value)


class App:
    def __init__(self, db: Database | None = None):
        config.ensure_dirs()
        self.db = db or Database()
        services.ensure_user(self.db)
        self.routes = [
            ("GET", re.compile(r"^/api/health$"), self.health),
            ("GET", re.compile(r"^/api/releases$"), self.release_notes),
            ("GET", re.compile(r"^/api/agent/capabilities$"), self.agent_capabilities),
            ("POST", re.compile(r"^/api/agent/plan$"), self.agent_plan),
            ("GET", re.compile(r"^/api/me$"), self.me),
            ("GET", re.compile(r"^/api/goals$"), self.goals),
            ("POST", re.compile(r"^/api/onboarding$"), self.onboarding),
            ("GET", re.compile(r"^/api/dashboard$"), self.dashboard),
            ("GET", re.compile(r"^/api/advice$"), self.advice),
            ("POST", re.compile(r"^/api/advice/feedback$"), self.advice_feedback),
            ("POST", re.compile(r"^/api/plan/rebuild$"), self.plan_rebuild),
            ("GET", re.compile(r"^/api/map$"), self.map),
            ("GET", re.compile(r"^/api/units/duplicates$"), self.units_duplicates),
            ("GET", re.compile(r"^/api/units/([^/]+)$"), self.unit),
            ("POST", re.compile(r"^/api/units/([^/]+)/notes$"), self.unit_note),
            ("POST", re.compile(r"^/api/units/([^/]+)/mnemonic$"), self.unit_mnemonic),
            ("POST", re.compile(r"^/api/units/([^/]+)/merge$"), self.unit_merge),
            ("POST", re.compile(r"^/api/units/([^/]+)/unmerge$"), self.unit_unmerge),
            ("GET", re.compile(r"^/api/notes$"), self.notes_list),
            ("POST", re.compile(r"^/api/notes$"), self.notes_create),
            ("POST", re.compile(r"^/api/notes/delete$"), self.notes_delete),
            ("GET", re.compile(r"^/api/notes/export$"), self.notes_export),
            ("POST", re.compile(r"^/api/cards/preview$"), self.cards_preview),
            ("POST", re.compile(r"^/api/cards/confirm$"), self.cards_confirm),
            ("POST", re.compile(r"^/api/ask$"), self.quick_ask),
            ("GET", re.compile(r"^/api/packs$"), self.packs),
            ("GET", re.compile(r"^/api/packs/([^/]+)$"), self.pack_detail),
            ("POST", re.compile(r"^/api/packs/compile$"), self.compile_pack),
            ("POST", re.compile(r"^/api/packs/suggest$"), self.pack_suggest),
            ("POST", re.compile(r"^/api/material/fix$"), self.material_fix),
            ("POST", re.compile(r"^/api/packs/import$"), self.pack_import),
            ("POST", re.compile(r"^/api/socratic/start$"), self.socratic_start),
            ("POST", re.compile(r"^/api/socratic/stream$"), self.socratic_stream),
            ("POST", re.compile(r"^/api/socratic/finish$"), self.socratic_finish),
            ("GET", re.compile(r"^/api/socratic/([^/]+)$"), self.socratic_get),
            ("POST", re.compile(r"^/api/feynman/evaluate$"), self.feynman_evaluate),
            ("GET", re.compile(r"^/api/review$"), self.review),
            ("GET", re.compile(r"^/api/cards$"), self.cards),
            ("POST", re.compile(r"^/api/review/([^/]+)$"), self.review_rate),
            ("POST", re.compile(r"^/api/reviews/undo$"), self.review_undo),
            ("GET", re.compile(r"^/api/focus$"), self.focus_today),
            ("POST", re.compile(r"^/api/focus/start$"), self.focus_start),
            ("POST", re.compile(r"^/api/focus/finish$"), self.focus_finish),
            ("GET", re.compile(r"^/api/insights$"), self.insights),
            ("POST", re.compile(r"^/api/insights/synthesize$"), self.insights_synthesize),
            ("GET", re.compile(r"^/api/settings/llm$"), self.llm_get),
            ("POST", re.compile(r"^/api/settings/llm$"), self.llm_set),
            ("POST", re.compile(r"^/api/settings/llm/test$"), self.llm_test),
            ("POST", re.compile(r"^/api/settings/llm/cache/clear$"), self.llm_cache_clear),
            ("GET", re.compile(r"^/api/settings/embeddings$"), self.embedding_get),
            ("POST", re.compile(r"^/api/settings/embeddings$"), self.embedding_set),
            ("POST", re.compile(r"^/api/settings/embeddings/test$"), self.embedding_test),
            ("GET", re.compile(r"^/api/styles/questions$"), self.styles_questions),
            ("POST", re.compile(r"^/api/styles/apply$"), self.styles_apply),
            ("GET", re.compile(r"^/api/search$"), self.search),
            ("GET", re.compile(r"^/api/events$"), self.events),
            ("GET", re.compile(r"^/api/export$"), self.export),
            ("GET", re.compile(r"^/api/export/anki$"), self.export_anki),
            ("POST", re.compile(r"^/api/import$"), self.import_data),
            ("POST", re.compile(r"^/api/reset$"), self.reset),
        ]

    # ---------------- route handlers ----------------
    def agent_capabilities(self, req, match, query, body):
        return {"capabilities": agent.capabilities(), "max_actions": agent.MAX_ACTIONS,
                "max_turns": agent.MAX_TURNS, "engine": llm.mode()}

    def agent_plan(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        question = str(body.get("question") or "")
        history = body.get("history") if isinstance(body.get("history"), list) else []
        observations = body.get("observations") if isinstance(body.get("observations"), list) else []
        try:
            turn = max(1, min(int(body.get("turn") or 1), agent.MAX_TURNS))
        except (TypeError, ValueError):
            turn = 1
        try:
            out = agent.plan(self.db, config.DEFAULT_USER, question,
                             history=history, observations=observations, turn=turn)
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc
        events.emit(self.db, "agent.turn", {
            "question": question[:200], "engine": out["engine"], "turn": out["turn"],
            "proposed": [a["id"] for a in out["actions"]],
            "rejected": [r.get("id") for r in out["rejected"]],
        }, config.DEFAULT_USER)
        return out

    def release_notes(self, req, match, query, body):
        return releases.payload()

    def health(self, req, match, query, body):
        return {
            "app": config.APP_NAME, "version": config.VERSION,
            "build": releases.build_identity()["commit"],
            "status": "ok", "mode": llm.mode(),
            "llm": llm.public_config(),
            "packs": len(services.all_available_packs()),
            "embeddings": {k: v for k, v in embeddings.public_config().items() if k not in ("suggest", "stats")},
            "data_dir": str(config.DATA_DIR),
            "uptime_seconds": int(time.time() - STARTED),
            "time": now_iso(),
        }

    def me(self, req, match, query, body):
        user = services.ensure_user(self.db)
        out = services.goals_overview(self.db, user["id"])
        out["llm"] = llm.public_config()
        out["token_required"] = bool(config.TOKEN)
        out["version"] = config.VERSION
        return out

    def goals(self, req, match, query, body):
        return services.goals_overview(self.db, config.DEFAULT_USER)

    def onboarding(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.onboarding(self.db, body)

    def dashboard(self, req, match, query, body):
        return services.dashboard(self.db, config.DEFAULT_USER,
                                  _q(query, "goal_id") or None)

    def advice(self, req, match, query, body):
        minutes_raw = _q(query, "minutes")
        minutes = None
        if minutes_raw:
            try:
                minutes = int(minutes_raw)
            except ValueError:
                minutes = None
        return advisor.build_advice(self.db, config.DEFAULT_USER,
                                    _q(query, "goal_id") or None, minutes)

    def advice_feedback(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return advisor.feedback(self.db, config.DEFAULT_USER, body)

    def plan_rebuild(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.update_goal_and_replan(self.db, config.DEFAULT_USER, body)

    def map(self, req, match, query, body):
        return services.map_data(self.db, config.DEFAULT_USER,
                                 _q(query, "goal_id") or None)

    def unit(self, req, match, query, body):
        return services.unit_detail(self.db, config.DEFAULT_USER, unquote(match.group(1)))

    def unit_note(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        unit_id = unquote(match.group(1))
        row = self.db.one("SELECT id FROM units WHERE id=?", (unit_id,))
        if not row:
            raise ApiError(404, "单元不存在", "not_found")
        return services.save_unit_note(
            self.db, config.DEFAULT_USER, unit_id,
            str(body.get("kind") or "note"), str(body.get("content") or ""),
        )

    def cards_preview(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.cards_from_text(
            self.db, config.DEFAULT_USER,
            str(body.get("text") or ""),
            str(body.get("unit_id") or "") or None,
        )

    def cards_confirm(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        cards = body.get("cards")
        if not isinstance(cards, list):
            raise ApiError(400, "cards 必须是数组", "bad_request")
        return services.confirm_cards(self.db, config.DEFAULT_USER, cards,
                                      str(body.get("unit_id") or "") or None)

    def quick_ask(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            return services.ask_about_selection(
                self.db, config.DEFAULT_USER,
                str(body.get("question") or ""),
                str(body.get("context") or ""),
                str(body.get("unit_id") or "") or None,
            )
        except PermissionError as exc:
            raise ApiError(503, str(exc), "ai_not_configured") from exc
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def unit_mnemonic(self, req, match, query, body):
        unit_id = unquote(match.group(1))
        try:
            return services.generate_mnemonic(self.db, config.DEFAULT_USER, unit_id)
        except PermissionError as exc:
            raise ApiError(503, str(exc), "ai_not_configured") from exc
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def units_duplicates(self, req, match, query, body):
        try:
            return services.unit_duplicates(self.db, config.DEFAULT_USER,
                                            _q(query, "goal_id") or None)
        except ValueError as exc:
            raise ApiError(404, str(exc), "not_found") from exc

    def unit_merge(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            return services.merge_units(self.db, config.DEFAULT_USER, unquote(match.group(1)),
                                        str(body.get("into") or ""),
                                        str(body.get("reason") or ""),
                                        override=bool(body.get("override")),
                                        confirmed=bool(body.get("confirmed")))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def unit_unmerge(self, req, match, query, body):
        try:
            return services.unmerge_unit(self.db, config.DEFAULT_USER,
                                         unquote(match.group(1)))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def notes_list(self, req, match, query, body):
        return services.list_notes(
            self.db, config.DEFAULT_USER,
            unit_id=_q(query, "unit_id") or None,
            q=_q(query, "q") or None,
            limit=int(_q(query, "limit") or 200),
        )

    def notes_create(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.create_note(self.db, config.DEFAULT_USER, body)

    def notes_delete(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        note_id = str(body.get("id") or "").strip()
        if not note_id:
            raise ApiError(400, "缺少 id", "bad_request")
        return services.delete_note(self.db, config.DEFAULT_USER, note_id)

    def notes_export(self, req, match, query, body):
        text = services.export_notes_markdown(self.db, config.DEFAULT_USER)
        req.send_download(text, "boil-notes.md", content_type="text/markdown; charset=utf-8")

    def packs(self, req, match, query, body):
        return services.pack_catalog(self.db, config.DEFAULT_USER)

    def pack_detail(self, req, match, query, body):
        return services.pack_detail(unquote(match.group(1)))

    def pack_import(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.import_pack(body)

    def compile_pack(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        title = str(body.get("title") or "我的材料")
        subject = str(body.get("subject") or "")
        text = str(body.get("text") or "")
        documents = body.get("documents")
        if documents is not None and not isinstance(documents, list):
            raise ApiError(400, "documents 必须是数组", "bad_request")
        use_llm = bool(body.get("use_llm", True))
        use_embeddings = bool(body.get("use_embeddings", True))
        return services.compile_preview(self.db, title, subject, text, use_llm,
                                        use_embeddings=use_embeddings,
                                        documents=documents)

    def material_fix(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        text = str(body.get("text") or "")
        if len(text) > MAX_FIX_CHARS:
            raise ApiError(400, f"待清洗文本超过 {MAX_FIX_CHARS:,} 字符", "bad_request")
        options = body.get("options")
        if options is not None and not isinstance(options, dict):
            raise ApiError(400, "options 必须是对象", "bad_request")
        cleaned, applied = markdown_fix.fix_markdown(text, options)
        return {"text": cleaned, "applied": applied, "changed": cleaned != text}

    def pack_suggest(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        text = str(body.get("text") or "")
        documents = body.get("documents")
        if documents is not None and not isinstance(documents, list):
            raise ApiError(400, "documents 必须是数组", "bad_request")
        return services.suggest_meta(text, documents)

    def socratic_start(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        unit_id = str(body.get("unit_id") or "")
        goal_id = str(body.get("goal_id") or "")
        if not unit_id:
            raise ApiError(400, "缺少 unit_id", "bad_request")
        if not goal_id:
            row = self.db.one("SELECT goal_id FROM units WHERE id=?", (unit_id,))
            if not row:
                raise ApiError(404, "单元不存在", "not_found")
            goal_id = row["goal_id"]
        return socratic.start_session(self.db, config.DEFAULT_USER, goal_id, unit_id,
                                      advice_id=body.get("advice_id"))

    def socratic_stream(self, req, match, query, body):
        if not isinstance(body, dict):
            req.send_json(400, {"ok": False, "error": {"message": "请求体必须是 JSON 对象"}})
            return
        session_id = str(body.get("session_id") or "")
        message = str(body.get("message") or "")
        if not session_id:
            req.send_json(400, {"ok": False, "error": {"message": "缺少 session_id"}})
            return
        req.start_sse()
        try:
            req.send_sse("status", {"text": "正在理解你的回答…"})
            for event in socratic.stream_reply(self.db, config.DEFAULT_USER, session_id, message,
                                                retry=bool(body.get("retry"))):
                etype = event.get("type", "message")
                data = {k: v for k, v in event.items() if k != "type"}
                req.send_sse(etype, data)
        except Exception as exc:
            try:
                req.send_sse("error", {"message": str(exc)[:300]})
            except Exception:
                pass
            print("[sse] error:", traceback.format_exc(), file=sys.stderr)
        finally:
            req.close_sse()

    def socratic_finish(self, req, match, query, body):
        session_id = str((body or {}).get("session_id") or "")
        if not session_id:
            raise ApiError(400, "缺少 session_id", "bad_request")
        return socratic.finish_session(self.db, config.DEFAULT_USER, session_id)

    def socratic_get(self, req, match, query, body):
        try:
            return socratic.get_session(self.db, unquote(match.group(1)))
        except ValueError as exc:
            raise ApiError(404, str(exc), "not_found") from exc

    def feynman_evaluate(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        unit_id = str(body.get("unit_id") or "")
        text = str(body.get("text") or "")
        mode = str(body.get("mode") or "peer")
        row = self.db.one("SELECT * FROM units WHERE id=?", (unit_id,))
        if not row:
            raise ApiError(404, "单元不存在", "not_found")
        unit = unit_from_row(row)
        try:
            result = feynman.evaluate(self.db, config.DEFAULT_USER, unit, text, mode,
                                      advice_id=body.get("advice_id"))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc
        return result

    def review(self, req, match, query, body):
        limit = int(_q(query, "limit") or 30)
        unit_id = _q(query, "unit_id") or None
        return services.review_queue(self.db, config.DEFAULT_USER, limit, unit_id)

    def cards(self, req, match, query, body):
        return services.list_cards(
            self.db, config.DEFAULT_USER,
            q=_q(query, "q") or None,
            unit_id=_q(query, "unit_id") or None,
            state=_q(query, "state") or None,
            limit=int(_q(query, "limit") or 60),
        )

    def review_rate(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            rating = int(body.get("rating"))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "rating 必须是 1–4", "bad_request") from exc
        try:
            elapsed = int(body.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            elapsed = 0
        try:
            return services.rate_card(self.db, config.DEFAULT_USER,
                                      unquote(match.group(1)), rating, elapsed,
                                      recall=str(body.get("recall") or ""),
                                      mistake_tags=body.get("mistake_tags"))
        except ValueError as exc:
            raise ApiError(404, str(exc), "not_found") from exc

    def review_undo(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            return services.undo_review(self.db, config.DEFAULT_USER,
                                        str(body.get("op") or ""))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def focus_today(self, req, match, query, body):
        return services.focus_today(self.db, config.DEFAULT_USER)

    def focus_start(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.focus_start(int(body.get("minutes") or 25),
                                    str(body.get("unit_id") or "") or None)

    def focus_finish(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            return services.focus_finish(
                self.db, config.DEFAULT_USER, str(body.get("session") or ""),
                int(body.get("minutes") or 0), str(body.get("unit_id") or "") or None,
                str(body.get("outcome") or "completed"))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def insights(self, req, match, query, body):
        return services.insights(self.db, config.DEFAULT_USER, _q(query, "goal_id") or None)

    def insights_synthesize(self, req, match, query, body):
        goal_id = None
        if isinstance(body, dict):
            goal_id = str(body.get("goal_id") or "") or None
        return services.insights_synthesize(self.db, config.DEFAULT_USER, goal_id)

    def llm_get(self, req, match, query, body):
        return llm.public_config()

    def llm_set(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        llm.set_config(body)
        return llm.public_config()

    def llm_cache_clear(self, req, match, query, body):
        removed = llm.clear_cache()
        return {"removed": removed, "cache": llm.cache_stats(), "entries": llm.cache_size()}

    def embedding_get(self, req, match, query, body):
        return embeddings.public_config()

    def embedding_set(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        embeddings.set_config(
            base_url=body.get("base_url"),
            model=body.get("model"),
            api_key=body.get("api_key"),
            enabled=body.get("enabled"),
            timeout=body.get("timeout"),
        )
        return embeddings.public_config()

    def embedding_test(self, req, match, query, body):
        res = embeddings.probe()
        if not res.get("ok"):
            raise ApiError(400, res.get("detail") or "Embedding 连接失败", "embedding_error",
                           detail=res.get("last_error") or "")
        return res

    def llm_test(self, req, match, query, body):
        try:
            return llm.test_connection()
        except llm.LLMError as exc:
            raise ApiError(400, exc.message, exc.code, exc.detail) from exc

    def styles_questions(self, req, match, query, body):
        return styles.questions_payload()

    def styles_apply(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        try:
            return services.save_style_test(self.db, config.DEFAULT_USER, body.get("answers"))
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc

    def search(self, req, match, query, body):
        return services.search(self.db, config.DEFAULT_USER, _q(query, "q"))

    def events(self, req, match, query, body):
        after = int(_q(query, "after") or 0)
        limit = int(_q(query, "limit") or 30)
        return {"events": recent(self.db, config.DEFAULT_USER, limit, after)}

    def export(self, req, match, query, body):
        data = services.export_data(self.db, config.DEFAULT_USER)
        req.send_download(json.dumps(data, ensure_ascii=False, indent=2),
                          "boil-export.json")

    def export_anki(self, req, match, query, body):
        text = services.export_anki(self.db, config.DEFAULT_USER)
        req.send_download(text, "boil-anki.txt",
                          content_type="text/tab-separated-values; charset=utf-8")

    def import_data(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        mode = str(body.get("mode") or "merge")
        if mode == "replace" and body.get("confirm") != "IMPORT":
            raise ApiError(400, "替换导入需要 confirm=IMPORT", "bad_request")
        try:
            return backup.import_backup(self.db, config.DEFAULT_USER, body, mode)
        except sqlite3.Error as exc:
            # 备份文件是用户可以手改的东西：不合法要说清哪里不合法，而不是甩一个 500
            raise ApiError(400, f"备份文件与本地库不兼容：{str(exc)[:160]}（本次导入已整体回滚，"
                                "原有数据未受影响）", "import_conflict") from exc

    def reset(self, req, match, query, body):
        confirm = str((body or {}).get("confirm") or "")
        if confirm != "RESET":
            raise ApiError(400, "需要 confirm=RESET", "bad_request")
        if config.HOST not in ("127.0.0.1", "localhost", "::1") and os.environ.get("BOIL_ALLOW_RESET") != "1":
            raise ApiError(403, "非本机部署默认禁止重置；请设置 BOIL_ALLOW_RESET=1", "forbidden")
        services.reset_user(self.db, config.DEFAULT_USER)
        services.ensure_user(self.db)
        return {"ok": True, "reset": True}


def _q(query: dict[str, list[str]], key: str) -> str:
    return (query.get(key) or [""])[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Boil/1.0"
    app: App

    # ---------------- HTTP ----------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            self.send_response(405)
            self.send_header("Allow", "GET, POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._serve_static(path, head=True)

    def _handle(self, method: str):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path.startswith("/api/"):
            if not self._auth_ok():
                self.send_json(401, {"ok": False, "error": {
                    "code": "unauthorized", "message": "需要访问令牌（BOIL_TOKEN）"}})
                return
            body = self._read_json() if method == "POST" else {}
            for m, pattern, handler in self.app.routes:
                match = pattern.match(path)
                if match and m == method:
                    try:
                        result = handler(self, match, query, body)
                        if result is not None:
                            self.send_json(200, {"ok": True, "data": result})
                    except ApiError as exc:
                        self.send_json(exc.status, {"ok": False, "error": {
                            "code": exc.code, "message": exc.message, "detail": exc.detail}})
                    except PackError as exc:
                        self.send_json(400, {"ok": False, "error": {
                            "code": "pack_error", "message": str(exc),
                            "detail": exc.errors}})
                    except PermissionError as exc:
                        self.send_json(403, {"ok": False, "error": {
                            "code": "forbidden", "message": str(exc)}})
                    except ValueError as exc:
                        self.send_json(400, {"ok": False, "error": {
                            "code": "bad_request", "message": str(exc)}})
                    except llm.LLMError as exc:
                        self.send_json(502, {"ok": False, "error": exc.to_dict()})
                    except BrokenPipeError:
                        return
                    except Exception as exc:
                        print("[api] error:", traceback.format_exc(), file=sys.stderr)
                        self.send_json(500, {"ok": False, "error": {
                            "code": "internal", "message": "服务器内部错误",
                            "detail": str(exc)[:300]}})
                    return
            self.send_json(404, {"ok": False, "error": {
                "code": "not_found", "message": f"接口不存在：{method} {path}"}})
            return
        if method != "GET":
            self.send_json(405, {"ok": False, "error": {"message": "Method Not Allowed"}})
            return
        self._serve_static(path)

    def _auth_ok(self) -> bool:
        if not config.TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            token = self.headers.get("X-Boil-Token", "").strip()
        return token == config.TOKEN

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > config.MAX_BODY_BYTES:
            raise ApiError(413, "请求体过大", "too_large")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "请求体不是合法 JSON", "bad_json", str(exc)[:200]) from exc

    def end_headers(self):
        # 统一安全头，覆盖 API、SSE、静态资源与错误页。
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "media-src 'self' blob:; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def send_json(self, status: int, payload: Any):
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def send_download(self, text: str, filename: str,
                      content_type: str = "application/json; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        self.wfile.flush()

    def send_sse(self, event: str, data: Any):
        payload = json.dumps(data, ensure_ascii=False, default=_json_default)
        chunk = f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def close_sse(self):
        try:
            self.wfile.write(b"event: close\ndata: {}\n\n")
            self.wfile.flush()
        except Exception:
            pass

    # ---------------- static ----------------
    def _serve_static(self, path: str, head: bool = False):
        if path in ("", "/"):
            path = "/index.html"
        safe = path.lstrip("/")
        target = (config.STATIC_DIR / safe).resolve()
        try:
            target.relative_to(config.STATIC_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            # 带扩展名的资源缺失必须 404，不能回退成 HTML（否则模块会 MIME 报错）。
            if target.suffix:
                self.send_error(404)
                return
            # 无扩展名的深链回退到 SPA。
            target = config.STATIC_DIR / "index.html"
            if not target.is_file():
                self.send_error(404)
                return
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".svg":
            ctype = "image/svg+xml"
        elif target.suffix == ".woff2":
            ctype = "font/woff2"
        elif target.suffix in (".html",):
            ctype = "text/html; charset=utf-8"
        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 前端是原生 ES modules，必须每次校验，避免更新后新旧模块混用。
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self.end_headers()
        if head:
            return
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /api/health"):
            return
        print(f"[http] {self.address_string()} {fmt % args}", file=sys.stderr)


def run(host: str | None = None, port: int | None = None, db: Database | None = None) -> None:
    host = host or config.HOST
    port = port or config.PORT
    app = App(db)
    Handler.app = app
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    mode = "真实模型" if llm.enabled() else "演示模式（本地确定性引擎）"
    print("")
    print("  ██████  ██████  ██ ██")
    print("  ██   ██ ██  ██  ██ ██")
    print("  ██████  ██████  ██ ██████   Boil · 烧水")
    print("")
    print(f"  {config.APP_NAME} v{config.VERSION} · {mode}")
    print(f"  http://{host}:{port}")
    print(f"  数据目录：{config.DATA_DIR}")
    if config.TOKEN:
        print("  已启用访问令牌（BOIL_TOKEN）")
    if not llm.enabled():
        print("  提示：未配置模型也能完整体验；在设置页填写 API Key 可切换真实模型。")
    print("  Ctrl-C 停止")
    print("")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  再见。")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
