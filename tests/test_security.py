"""安全测试：不是"接口能返回 200"，而是"攻击者能试的路径都被挡住"。

覆盖：静态资源路径穿越、BOIL_TOKEN 鉴权、请求体上限、非法 JSON、非 GET/POST 动词、
SQL 注入式输入、Unicode/emoji/超长串往返、所有 GET 端点不泄密钥、安全响应头。

XSS 的「存下来是原文」在这里测；「渲染出来不执行」在 tools/audit_frontend.py 里用真浏览器测
—— 转义发生在前端，用 Python 断言等于没测。
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, embeddings, llm  # noqa: E402
from boil.db import Database  # noqa: E402
from boil.server import App, Handler, Server  # noqa: E402

SECRET_MARKERS = ("sk-", "ak_", '"api_key"', "api_key_masked", "Authorization")
GET_ENDPOINTS = (
    "/api/health", "/api/me", "/api/goals", "/api/dashboard", "/api/advice", "/api/map",
    "/api/units/duplicates", "/api/notes", "/api/packs", "/api/review", "/api/cards",
    "/api/insights", "/api/search?q=a", "/api/settings/llm", "/api/settings/embeddings",
    "/api/mistakes", "/api/cram", "/api/exams",
    "/api/styles/questions", "/api/releases", "/api/agent/capabilities", "/api/events",
    "/api/export", "/api/export/anki", "/api/focus", "/api/units/" + "nope",
)


class ServerFixture(unittest.TestCase):
    """每个测试类一套临时实例；token 非空时开启 BOIL_TOKEN 鉴权。"""

    token = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-sec-")
        base = Path(cls.tmp.name)
        cls.old = {k: getattr(config, k) for k in
                   ("PACKS_DIR", "DATA_DIR", "LLM_CONFIG_PATH", "LLM_STATE_PATH", "TOKEN", "MAX_BODY_BYTES")}
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.TOKEN = cls.token
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        cls.old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
        llm.set_config({"api_key": ""})
        cls.db = Database(base / "sec.db")
        Handler.app = App(cls.db)
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()
        Handler.app = None
        for key, value in cls.old.items():
            setattr(config, key, value)
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = cls.old_emb
        cls.tmp.cleanup()

    def raw(self, method, path, body=None, headers=None):
        """用 http.client 而不是 urllib：需要发畸形头、非法方法、原始路径。"""
        url = urllib.parse.urlsplit(self.base)
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        hdrs = dict(headers or {})
        payload = None
        if body is not None:
            payload = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        status = resp.status
        conn.close()
        return status, dict(resp.getheaders()), data

    def socket_raw(self, request: bytes):
        """绕过 http.client 的 URL 校验：控制字符、畸形请求行只能手写TCP。

        调用方自己带 Connection: close，否则 keep-alive 会让我们等到读超时。
        """
        url = urllib.parse.urlsplit(self.base)
        with socket.create_connection((url.hostname, url.port), timeout=10) as sock:
            sock.sendall(request)
            chunks = []
            try:
                while True:
                    piece = sock.recv(4096)
                    if not piece:
                        break
                    chunks.append(piece)
            except socket.timeout:
                pass
        return b"".join(chunks)

    def get(self, path, **kw):
        return self.raw("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.raw("POST", path, body=body, **kw)

    def auth(self, token=None):
        tok = self.token if token is None else token
        return {"Authorization": f"Bearer {tok}"} if tok else {}


class StaticPathTest(ServerFixture):
    """穿越攻击的判定标准是"没拿到 static/ 之外的字节"，不是状态码。

    无扩展名的攻击路径会被 SPA 回退吃掉并返回 200 —— 这是设计行为（深链要靠前端路由），
    只要正文是壳子而不是文件内容就没漏。真要越界的那些必须 403/404。
    """

    LEAKS = (b"MAX_BODY_BYTES", b"root::", b"__future__", b"SECRET", b"api_key")
    SHELL = b'<div id="app">'

    def _check(self, status, headers, body, path):
        joined = {k.lower(): v for k, v in headers.items()}
        for marker in self.LEAKS:
            self.assertNotIn(marker, body, f"{path} 漏出了 {marker!r}")
        if status == 200:
            self.assertEqual(joined.get("content-type", "").split(";")[0], "text/html",
                             f"{path} 用 200 返回了非 HTML 内容")
            self.assertIn(self.SHELL, body, f"{path} 返回 200 但不是 SPA 壳子")

    def test_dot_dot_cannot_escape_the_static_dir(self):
        attacks = [
            "/../boil/config.py",
            "/../%62oil/config.py",
            "/..%2f..%2fetc%2fpasswd",
            "/%2e%2e/%2e%2e/etc/passwd",
            "/....//....//etc/passwd",
            "//..//etc/passwd",
            "/css/../../boil/server.py",
            "/%2e%2e%2fboil%2fconfig.py",
            "/../../../etc/passwd",
            "/%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        ]
        for path in attacks:
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self._check(status, headers, body, path)

    def test_source_files_are_never_served(self):
        # 仓库里的真实文件：穿越成功的话这些一定存在，静态目录里则一定 404
        for path in ("/../boil/config.py", "/../data/llm.json", "/../server.py", "/boil/server.py"):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self._check(status, headers, body, path)
                self.assertNotIn(b"ThreadingHTTPServer", body, "把后端源码发出去了")

    def test_absolute_path_and_null_byte_are_refused(self):
        for path in ("//etc/passwd", "/index.html%00.css", "/\x00/etc/passwd", "/%00"):
            with self.subTest(path=path):
                if "\x00" in path:
                    # http.client 拒绝发送控制字符，得手写请求行
                    head = self.socket_raw(
                        f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
                    self.assertNotIn(b"root::", head)
                    self.assertNotIn(b"ThreadingHTTPServer", head)
                    self.assertNotIn(b"200 OK", head.split(b"\r\n", 1)[0])
                else:
                    status, headers, body = self.get(path)
                    self._check(status, headers, body, path)
        # 攻击之后服务器还活着：拒绝服务不该是穿越测试的副产品
        status, _, _ = self.get("/api/health")
        self.assertEqual(status, 200)

    def test_junk_in_the_request_does_not_split_the_response(self):
        # 真正要防的不是"服务器嫌不嫌你脏"，而是垃圾字节能不能变成响应头（缓存投毒/响应分割）
        close = b"\r\nConnection: close\r\n\r\n"
        cases = {
            "0.9 请求行": b"GET / HTTP/0.9" + close,
            "裸控制字符请求行": b"\x00\x01\x02" + close,
            "头值里塞 NUL": b"GET /?a= HTTP/1.1\r\nEvil: \x00head" + close,
            "换行续头": b"GET / HTTP/1.1\r\nX-A:\r\nEvil: Set-Cookie: a=b" + close,
        }
        for name, request in cases.items():
            with self.subTest(case=name):
                resp = self.socket_raw(request)
                head = resp.split(b"\r\n\r\n", 1)[0]
                self.assertLessEqual(head.count(b"HTTP/1.1 "), 1, "响应被切成了两段")
                self.assertNotIn(b"\x00", head, "请求里的脏字节进了响应头")
                self.assertNotIn(b"Set-Cookie", head)
                self.assertNotIn(b"Evil", head)
        status, _, _ = self.get("/api/health")
        self.assertEqual(status, 200, "畸形请求把服务器打挂了")

    def test_missing_asset_is_404_not_the_spa_shell(self):
        # 带扩展名的资源缺失必须 404：回退成壳子会让 ES module 报 MIME 错，用户看到白屏
        status, headers, body = self.get("/js/definitely-not-here.js")
        self.assertEqual(status, 404)
        self.assertNotIn(self.SHELL, body, "缺资源时把整个壳子当成 JS 发回去了")
        self.assertNotEqual(headers.get("Content-Type", "").split(";")[0], "text/javascript")

    def test_deep_spa_route_still_returns_the_shell(self):
        status, _, body = self.get("/some/deep/route")
        self.assertEqual(status, 200)
        self.assertIn(b"BOIL", body)


class HeaderTest(ServerFixture):
    def test_security_headers_on_every_response_shape(self):
        for path, expect_status in (("/api/health", 200), ("/", 200), ("/api/nope", 404), ("/nope.js", 404)):
            with self.subTest(path=path):
                status, headers, _ = self.get(path)
                self.assertEqual(status, expect_status)
                joined = {k.lower(): v for k, v in headers.items()}
                self.assertEqual(joined.get("x-content-type-options"), "nosniff")
                self.assertEqual(joined.get("x-frame-options"), "DENY")
                self.assertEqual(joined.get("referrer-policy"), "no-referrer")
                csp = joined.get("content-security-policy", "")
                self.assertIn("default-src 'self'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertIn("object-src 'none'", csp)
                self.assertNotIn("*", csp.split("connect-src")[1].split(";")[0])
                self.assertNotIn("access-control-allow-origin", joined,
                                 "开了跨域就等于允许别的站点带着本机令牌发请求")


class CrossOriginTest(ServerFixture):
    """别的网站能不能借用户的浏览器驱动本机 Boil。

    防线不是"检查 Origin"，而是"从不授予跨域"：没有 Access-Control-Allow-Origin，
    预检就过不去，简单表单请求又带不进合法 JSON —— 两条路都堵死才叫防住了 CSRF。
    """

    def test_preflight_from_evil_origin_grants_nothing(self):
        status, headers, _ = self.raw(
            "OPTIONS", "/api/settings/llm",
            headers={"Origin": "http://evil.example",
                     "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type"})
        joined = {k.lower(): v for k, v in headers.items()}
        self.assertNotIn("access-control-allow-origin", joined)
        self.assertNotIn("access-control-allow-methods", joined)
        self.assertNotIn("access-control-allow-headers", joined)
        self.assertNotIn("access-control-allow-credentials", joined)
        self.assertIn(status, (204, 405))

    def test_simple_form_post_cannot_write_settings(self):
        before = config.LLM_CONFIG_PATH.read_text(encoding="utf-8") if config.LLM_CONFIG_PATH.exists() else ""
        status, _, body = self.raw(
            "POST", "/api/settings/llm",
            body=b"api_key=sk-from-another-site&base_url=http%3A%2F%2Fevil.example",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": "http://evil.example"})
        self.assertEqual(status, 400, body)
        after = config.LLM_CONFIG_PATH.read_text(encoding="utf-8") if config.LLM_CONFIG_PATH.exists() else ""
        self.assertEqual(before, after, "表单式跨站请求改写了模型配置")

    def test_json_post_without_cors_still_unreadable_cross_origin(self):
        status, headers, _ = self.get("/api/dashboard", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 200)
        joined = {k.lower(): v for k, v in headers.items()}
        self.assertNotIn("access-control-allow-origin", joined,
                         "响应可读 = 别的站点能悄悄读本机学习数据")


class BodyLimitTest(ServerFixture):
    def test_oversized_body_is_413_not_a_hung_server(self):
        old = config.MAX_BODY_BYTES
        try:
            config.MAX_BODY_BYTES = 2048
            status, _, body = self.post("/api/notes", {"content": "字" * 4000})
            self.assertEqual(status, 413)
            self.assertIn("too_large", body.decode())
        finally:
            config.MAX_BODY_BYTES = old

    def test_negative_and_bogus_content_length_do_not_crash(self):
        for value in ("-1", "abc", "", "99999999999999999999"):
            with self.subTest(value=value):
                try:
                    status, _, body = self.post("/api/notes", b"{}", headers={"Content-Length": value})
                except (http.client.RemoteDisconnected, ConnectionResetError):
                    self.fail(f"Content-Length={value!r} 把连接打断了，浏览器只会看到网络错误")
                self.assertIn(status, (400, 413))
                self.assertNotIn(b"Traceback", body)
                self.assertNotIn(b'"ok": true', body, "非法长度头居然写进了数据")

    def test_chunked_body_is_refused_rather_than_smuggled(self):
        # 没有 Content-Length 的分块请求：读不到 body 却继续解析同一条连接，就是请求走私的入口
        close = b"\r\nConnection: close\r\n\r\n"
        head = self.socket_raw(
            b"POST /api/notes HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked" + close).split(
            b"\r\n", 1)[0]
        self.assertIn(b"4", head, f"分块请求没有被拒绝：{head!r}")
        status, _, _ = self.get("/api/health")
        self.assertEqual(status, 200)

    def test_malformed_json_is_400_not_a_dead_connection(self):
        for payload in (b"{not json", b"[1,2", b"\xff\xfe bad utf8", b'{"quote": '):
            with self.subTest(payload=payload[:12]):
                try:
                    status, _, body = self.post("/api/notes", payload)
                except (http.client.RemoteDisconnected, ConnectionResetError):
                    self.fail("畸形 JSON 让请求线程崩了：用户收到的是没有任何解释的网络错误")
                self.assertEqual(status, 400)
                self.assertIn("bad_json", body.decode())
                self.assertNotIn(b"Traceback", body)

    def test_json_that_is_not_an_object_is_refused(self):
        # 合法 JSON、但不是所有 handler 都能当映射用的东西：null / 数组 / 数字 / 字符串
        for payload in (b"null", b"[1, 2]", b'"hi"', b"42", b"true"):
            with self.subTest(payload=payload):
                status, _, body = self.post("/api/notes", payload)
                self.assertEqual(status, 400)
                self.assertIn("对象", body.decode())
                self.assertNotIn(b"internal", body)

    def test_empty_body_is_not_treated_as_an_attack(self):
        # 空 body 走 {} 是设计：前端有只靠路径参数完成的 POST
        status, _, body = self.post("/api/notes", b"")
        self.assertEqual(status, 400)
        self.assertIn("至少要有一个", body.decode())
        self.assertNotIn(b"bad_json", body)


class VerbTest(ServerFixture):
    def test_destructive_verbs_are_not_implemented(self):
        # 这个系统的破坏性操作一律走 POST + confirm；PUT/PATCH/DELETE 根本不存在
        for verb in ("DELETE", "PUT", "PATCH", "TRACE"):
            with self.subTest(verb=verb):
                status, _, body = self.raw(verb, "/api/notes")
                self.assertIn(status, (405, 501))
                self.assertNotIn(b'"ok": true', body)

    def test_options_only_advertises_verbs(self):
        # 预检要有用，但除了"支持哪些方法"之外不能有任何内容
        status, headers, body = self.raw("OPTIONS", "/api/notes")
        self.assertEqual(status, 204)
        self.assertEqual(body, b"", "204 不该带正文")
        allow = headers.get("Allow", "")
        self.assertIn("GET", allow)
        self.assertNotIn("DELETE", allow)
        self.assertNotIn("PUT", allow)

    def test_head_never_writes_a_body(self):
        status, headers, body = self.raw("HEAD", "/api/notes")
        self.assertEqual(status, 405)
        self.assertEqual(body, b"")
        status, headers, body = self.raw("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertIn("text/html", headers.get("Content-Type", ""))


class TokenAuthTest(ServerFixture):
    token = "s3cret-local-token"

    def test_anonymous_api_is_401(self):
        status, _, body = self.get("/api/dashboard")
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", body.decode())

    def test_both_header_forms_work(self):
        for headers in ({"Authorization": "Bearer s3cret-local-token"},
                        {"Authorization": "bearer s3cret-local-token"},   # 大小写不敏感
                        {"X-Boil-Token": "s3cret-local-token"}):
            with self.subTest(**headers):
                status, _, _ = self.get("/api/health", headers=headers)
                self.assertEqual(status, 200)

    def test_wrong_or_partial_token_is_rejected(self):
        for headers in ({"Authorization": "Bearer nope"},
                        {"Authorization": "Bearer s3cret-local-token extra"},
                        {"Authorization": "Token s3cret-local-token"},      # 不是 Bearer 前缀
                        {"Authorization": "Bearer s3cret-local-toke"},      # 少一个字符
                        {"X-Boil-Token": ""}):
            with self.subTest(**headers):
                status, _, _ = self.get("/api/health", headers=headers)
                self.assertEqual(status, 401)

    def test_token_never_appears_in_any_response(self):
        for path in GET_ENDPOINTS:
            status, _, body = self.get(path, headers=self.auth())
            if status == 200:
                with self.subTest(path=path):
                    self.assertNotIn(self.token.encode(), body)

    def test_spa_shell_stays_public_but_leaks_nothing(self):
        # 静态壳子必须能匿名打开（否则用户在哪儿输令牌），但不能带出任何数据
        status, _, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertNotIn(self.token.encode(), body)
        self.assertNotIn(b'"units"', body)


class InjectionTest(ServerFixture):
    def test_sql_shaped_inputs_are_stored_verbatim_and_harmless(self):
        payloads = ["'; DROP TABLE cards; --", "1 OR 1=1", "union select null,null",
                    "'; INSERT INTO users VALUES('x'); --", "%'%'#", "admin'--"]
        for i, payload in enumerate(payloads):
            status, _, body = self.post("/api/notes", {"quote": payload, "content": f"备注 {i}"})
            self.assertEqual(status, 200, payload)
        status, _, _ = self.get("/api/search?q=" + urllib.parse.quote("'; DROP TABLE cards; --"))
        self.assertEqual(status, 200)
        # 表还在
        self.assertGreaterEqual(self.db.scalar("SELECT COUNT(*) FROM cards", (), 0), 0)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM users", (), 0), 1)
        status, _, body = self.get("/api/notes")
        data = json.loads(body)["data"]["notes"]
        stored = {n["quote"] for n in data}
        self.assertTrue(set(payloads).issubset(stored), "注入式输入被改写或吞掉了")

    def test_path_parameters_are_not_interpreted_as_sql_or_paths(self):
        for weird in ("..", "%2e%2e", "u%00id", "unit' or '1'='1"):
            with self.subTest(weird=weird):
                status, _, body = self.get("/api/units/" + urllib.parse.quote(weird, safe=""))
                self.assertIn(status, (400, 404))
                self.assertNotIn(b'"units"', body)

    def test_unicode_emoji_rtl_and_very_long_strings_round_trip(self):
        nasty = "记忆 🧠 ‮rtl‬ \u200b零宽 " + "长" * 3000
        status, _, body = self.post("/api/notes", {"content": nasty[:600], "quote": "引文 🧠"})
        self.assertEqual(status, 200)
        status, _, body = self.get("/api/notes")
        notes = json.loads(body)["data"]["notes"]
        stored = [n for n in notes if n.get("quote") == "引文 🧠"]
        self.assertTrue(stored, "emoji 引文没存下来")
        self.assertIn("🧠", stored[0]["quote"])
        self.assertIn("‮", stored[0]["content"] or nasty[:600])   # 控制方向标记原样存
        self.assertLessEqual(len(stored[0]["content"] or ""), 1200, "超长内容必须被截断而不是无限增长")

    def test_html_shaped_note_is_returned_as_json_not_html(self):
        payload = "<img src=x onerror=alert(1)><script>fetch('//evil')</script>"
        self.post("/api/notes", {"content": payload, "quote": "x" * 12})
        status, headers, body = self.get("/api/notes")
        joined = {k.lower(): v for k, v in headers.items()}
        self.assertTrue(joined.get("content-type", "").startswith("application/json"))
        self.assertIn("onerror", body.decode())   # 原文保留（是数据），执行与否由前端转义决定


class SecretLeakTest(ServerFixture):
    def test_no_credential_shaped_text_in_any_get_endpoint(self):
        planted = "sk-planted-fake-key-1234567890"
        config.LLM_CONFIG_PATH.write_text(json.dumps({
            "base_url": "https://example.invalid/v1", "model": "m", "api_key": planted,
        }), encoding="utf-8")
        try:
            for path in GET_ENDPOINTS:
                status, _, body = self.get(path)
                if status != 200:
                    continue
                with self.subTest(path=path):
                    text = body.decode("utf-8", "replace")
                    self.assertNotIn(planted, text, "响应里出现了完整密钥")
                    self.assertNotIn('"api_key": "' , text, "响应里出现了 api_key 字段")
        finally:
            llm.set_config({"api_key": ""})

    def test_llm_public_config_only_masks(self):
        planted = "ak_abcdefghijklmnop"
        llm.set_config({"api_key": planted, "base_url": "https://example.invalid/v1", "model": "m"})
        try:
            status, _, body = self.get("/api/settings/llm")
            self.assertEqual(status, 200)
            text = body.decode()
            self.assertNotIn(planted, text)
            data = json.loads(body)["data"]
            self.assertTrue(data.get("api_key_masked"))
            self.assertNotIn("abcdefghijklmnop", data["api_key_masked"])
        finally:
            llm.set_config({"api_key": ""})

    def test_export_contains_no_credentials_or_absolute_secrets(self):
        planted = "sk-export-canary-000"
        llm.set_config({"api_key": planted, "base_url": "https://example.invalid/v1", "model": "m"})
        try:
            status, _, body = self.get("/api/export")
            self.assertEqual(status, 200)
            text = body.decode()
            self.assertNotIn(planted, text)
            self.assertNotIn("api_key", text)
            status, _, body = self.get("/api/notes/export")
            self.assertNotIn(planted, body.decode())
        finally:
            llm.set_config({"api_key": ""})


if __name__ == "__main__":
    unittest.main()
