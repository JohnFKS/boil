"""零依赖 HTTP 服务：REST + SSE + 静态 SPA。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import config, feynman, llm, mastery, services, socratic
from .compiler import compile_material
from .db import Database, json_loads
from .events import recent
from .packs import PackError, unit_from_row
from .utils import now_iso, parse_iso, truncate

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
            ("GET", re.compile(r"^/api/me$"), self.me),
            ("GET", re.compile(r"^/api/goals$"), self.goals),
            ("POST", re.compile(r"^/api/onboarding$"), self.onboarding),
            ("GET", re.compile(r"^/api/dashboard$"), self.dashboard),
            ("POST", re.compile(r"^/api/plan/rebuild$"), self.plan_rebuild),
            ("GET", re.compile(r"^/api/map$"), self.map),
            ("GET", re.compile(r"^/api/units/([^/]+)$"), self.unit),
            ("GET", re.compile(r"^/api/packs$"), self.packs),
            ("GET", re.compile(r"^/api/packs/([^/]+)$"), self.pack_detail),
            ("POST", re.compile(r"^/api/packs/compile$"), self.compile_pack),
            ("POST", re.compile(r"^/api/packs/import$"), self.pack_import),
            ("POST", re.compile(r"^/api/socratic/start$"), self.socratic_start),
            ("POST", re.compile(r"^/api/socratic/stream$"), self.socratic_stream),
            ("POST", re.compile(r"^/api/socratic/finish$"), self.socratic_finish),
            ("GET", re.compile(r"^/api/socratic/([^/]+)$"), self.socratic_get),
            ("POST", re.compile(r"^/api/feynman/evaluate$"), self.feynman_evaluate),
            ("GET", re.compile(r"^/api/review$"), self.review),
            ("POST", re.compile(r"^/api/review/([^/]+)$"), self.review_rate),
            ("GET", re.compile(r"^/api/insights$"), self.insights),
            ("GET", re.compile(r"^/api/settings/llm$"), self.llm_get),
            ("POST", re.compile(r"^/api/settings/llm$"), self.llm_set),
            ("POST", re.compile(r"^/api/settings/llm/test$"), self.llm_test),
            ("GET", re.compile(r"^/api/search$"), self.search),
            ("GET", re.compile(r"^/api/events$"), self.events),
            ("GET", re.compile(r"^/api/export$"), self.export),
            ("POST", re.compile(r"^/api/reset$"), self.reset),
        ]

    # ---------------- route handlers ----------------
    def health(self, req, match, query, body):
        return {
            "app": config.APP_NAME, "version": config.VERSION,
            "status": "ok", "mode": llm.mode(),
            "llm": llm.public_config(),
            "packs": len(services.all_available_packs()),
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

    def plan_rebuild(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        return services.update_goal_and_replan(self.db, config.DEFAULT_USER, body)

    def map(self, req, match, query, body):
        return services.map_data(self.db, config.DEFAULT_USER,
                                 _q(query, "goal_id") or None)

    def unit(self, req, match, query, body):
        return services.unit_detail(self.db, config.DEFAULT_USER, unquote(match.group(1)))

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
        use_llm = bool(body.get("use_llm", True))
        return services.compile_preview(self.db, title, subject, text, use_llm)

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
        return socratic.start_session(self.db, config.DEFAULT_USER, goal_id, unit_id)

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
            for event in socratic.stream_reply(self.db, config.DEFAULT_USER, session_id, message):
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
            result = feynman.evaluate(self.db, config.DEFAULT_USER, unit, text, mode)
        except ValueError as exc:
            raise ApiError(400, str(exc), "bad_request") from exc
        return result

    def review(self, req, match, query, body):
        limit = int(_q(query, "limit") or 30)
        unit_id = _q(query, "unit_id") or None
        return services.review_queue(self.db, config.DEFAULT_USER, limit, unit_id)

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
                                      unquote(match.group(1)), rating, elapsed)
        except ValueError as exc:
            raise ApiError(404, str(exc), "not_found") from exc

    def insights(self, req, match, query, body):
        return services.insights(self.db, config.DEFAULT_USER, _q(query, "goal_id") or None)

    def llm_get(self, req, match, query, body):
        return llm.public_config()

    def llm_set(self, req, match, query, body):
        if not isinstance(body, dict):
            raise ApiError(400, "请求体必须是 JSON 对象", "bad_request")
        llm.set_config(body)
        return llm.public_config()

    def llm_test(self, req, match, query, body):
        try:
            return llm.test_connection()
        except llm.LLMError as exc:
            raise ApiError(400, exc.message, exc.code, exc.detail) from exc

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

    def send_json(self, status: int, payload: Any):
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def send_download(self, text: str, filename: str):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
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
            # SPA 回退（hash 路由不需要，但保留深链）
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
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        cache = "no-cache" if target.name == "index.html" else "public, max-age=3600"
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
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
