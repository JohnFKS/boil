from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, llm


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: dict = {}
    requests: list = []
    # 非空时按脚本回放：每项 {"status":200,"content":"…","finish":"stop"}
    script: list = []

    def _reply(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, json.dumps({"data": [{"id": "fake-model"}]}).encode(), "application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.seen = {"headers": {k.lower(): v for k, v in self.headers.items()}, "payload": payload}
        FakeHandler.requests.append(payload)
        if FakeHandler.script:
            step = FakeHandler.script.pop(0)
            status = int(step.get("status", 200))
            content = step.get("content", "{}")
            finish = step.get("finish")
            if status != 200:
                self._reply(status, json.dumps({"error": {"message": step.get("detail", "bad request")}}).encode(),
                            "application/json")
                return
            if payload.get("stream"):
                self._send_sse(content, finish)
                return
            choice = {"message": {"content": content}}
            if finish:
                choice["finish_reason"] = finish
            self._reply(200, json.dumps({"choices": [choice]}).encode(), "application/json")
            return
        content = '{"hello":"world"}' if payload.get("response_format") else "OK from fake"
        if payload.get("stream"):
            self._send_sse(content, "stop")
            return
        self._reply(200, json.dumps({"choices": [{"message": {"content": content}}]}).encode(),
                    "application/json")

    def _send_sse(self, content: str, finish: str | None):
        """流式回给调用方；Boil 的 chat 会把它拼回非流式形状，所以这里要按字切分。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        # 切成两片，证明确实走的是分片到达而不是整块
        mid = max(1, len(content) // 2)
        pieces = [content[:mid], content[mid:]] if len(content) > 1 else [content]
        for piece in pieces:
            if not piece:
                continue
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        tail = {"choices": [{"delta": {}, "finish_reason": finish or "stop"}]}
        self.wfile.write(f"data: {json.dumps(tail, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, fmt, *args):
        pass


class LLMGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-llm-")
        base = Path(self.tmp.name)
        FakeHandler.script = []
        FakeHandler.requests = []
        self.old = {
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
            "LLM_OFF": config.LLM_OFF,
            "DATA_DIR": config.DATA_DIR,
        }
        config.DATA_DIR = base
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.LLM_OFF = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        llm.set_config({
            "base_url": f"http://127.0.0.1:{self.port}/v1",
            "api_key": "sk-test-1234567890abcdef",
            "model": "fake-model",
            "deep_model": "fake-model",
            "temperature": 0.1,
        })

    def tearDown(self):
        FakeHandler.script = []
        FakeHandler.requests = []
        self.server.shutdown()
        self.server.server_close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def test_live_chat_stream_json(self):
        self.assertTrue(llm.enabled())
        self.assertEqual(llm.mode(), "live")
        # chat 现在默认向上游要流式再拼回，所以用脚本喂内容，不靠 fake 的隐式默认值
        FakeHandler.script = [{"content": "OK from fake"}]
        text = llm.chat([{"role": "user", "content": "hello"}], max_tokens=20)
        self.assertEqual(text, "OK from fake")
        self.assertTrue(FakeHandler.requests[-1]["stream"], "上游请求应该是 stream:true")
        FakeHandler.script = [{"content": "你好世界"}]
        pieces = list(llm.stream_chat([{"role": "user", "content": "hello"}]))
        self.assertEqual("".join(pieces), "你好世界")
        self.assertGreaterEqual(len(pieces), 2, "流式应当分片到达")
        FakeHandler.script = [{"content": '{"hello":"world"}'}]
        data = llm.chat_json([{"role": "user", "content": "json"}])
        self.assertEqual(data, {"hello": "world"})
        info = llm.test_connection()
        self.assertTrue(info["ok"])
        self.assertGreaterEqual(info["latency_ms"], 0)
        pub = llm.public_config()
        self.assertTrue(pub["api_key_set"])
        self.assertNotIn("sk-test-1234567890abcdef", json.dumps(pub))
        self.assertFalse(llm.last_error())

    def test_failure_is_visible(self):
        llm.set_config({"base_url": "http://127.0.0.1:9/v1"})
        with self.assertRaises(llm.LLMError):
            llm.chat([{"role": "user", "content": "hello"}], max_tokens=8)
        err = llm.last_error()
        self.assertTrue(err)
        self.assertTrue(err.get("message"))
        pub = llm.public_config()
        self.assertTrue(pub["last_error"])

    def test_custom_auth_header_and_extra_body(self):
        llm.set_config({
            "auth_header": "api-key",
            "auth_prefix": "",
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        })
        text = llm.chat([{"role": "user", "content": "hello"}], max_tokens=20)
        self.assertEqual(text, "OK from fake")
        self.assertEqual(FakeHandler.seen["headers"].get("api-key"), "sk-test-1234567890abcdef")
        self.assertEqual(FakeHandler.seen["payload"].get("chat_template_kwargs"),
                         {"enable_thinking": False})
        self.assertNotIn("Authorization", FakeHandler.seen["headers"])
        pub = llm.public_config()
        self.assertEqual(pub["auth_header"], "api-key")
        self.assertEqual(pub["auth_prefix"], "")
        self.assertEqual(pub["extra_body"]["chat_template_kwargs"]["enable_thinking"], False)

    def test_bearer_prefix_without_trailing_space_is_normalized(self):
        llm.set_config({"auth_header": "Authorization", "auth_prefix": "Bearer"})
        self.assertEqual(llm.public_config()["auth_prefix"], "Bearer ")
        llm.chat([{"role": "user", "content": "hello"}], max_tokens=8)
        self.assertEqual(FakeHandler.seen["headers"].get("authorization"), "Bearer sk-test-1234567890abcdef")

    def test_models_endpoint(self):
        from urllib.request import urlopen
        r = urlopen(f"http://127.0.0.1:{self.port}/v1/models")
        data = json.loads(r.read())
        self.assertEqual(data["data"][0]["id"], "fake-model")

    def test_truncated_output_retries_with_bigger_budget(self):
        FakeHandler.script = [
            {"content": '{"a": [1, 2,', "finish": "length"},
            {"content": '{"a": [1, 2, 3]}', "finish": "stop"},
        ]
        out = llm.chat_json([{"role": "user", "content": "go"}], max_tokens=100)
        self.assertEqual(out, {"a": [1, 2, 3]})
        self.assertEqual(len(FakeHandler.requests), 2)
        self.assertGreater(FakeHandler.requests[1]["max_tokens"], 100)

    def test_truncation_without_explicit_budget_does_not_lower_ceiling(self):
        # 没设 max_tokens 时不能凭空塞一个更小的额度，否则会把网关默认值压低
        FakeHandler.script = [{"content": '{"a": 1}', "finish": "stop"}]
        out = llm.chat_json([{"role": "user", "content": "go"}])
        self.assertEqual(out, {"a": 1})
        self.assertNotIn("max_tokens", FakeHandler.requests[0])

    def test_junk_literal_response_is_a_failure(self):
        for junk in ("无", "undefined", "null", "..."):
            FakeHandler.script = [{"content": json.dumps(junk, ensure_ascii=False), "finish": "stop"}]
            with self.assertRaises(llm.LLMError) as ctx:
                llm.chat_json([{"role": "user", "content": "go"}])
            self.assertEqual(ctx.exception.code, "junk_output", junk)

    def test_response_format_400_degrades_and_retries(self):
        FakeHandler.script = [
            {"status": 400, "detail": "Unsupported parameter: response_format"},
            {"content": '{"ok": true}', "finish": "stop"},
        ]
        out = llm.chat_json([{"role": "user", "content": "go"}])
        self.assertEqual(out, {"ok": True})
        self.assertIn("response_format", FakeHandler.requests[0])
        self.assertNotIn("response_format", FakeHandler.requests[1])


class CacheTest(unittest.TestCase):
    """内容寻址缓存：同样输入第二次不再打网络，也不再漂移。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-cache-")
        base = Path(self.tmp.name)
        self.old = {"DATA_DIR": config.DATA_DIR, "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
                    "LLM_STATE_PATH": config.LLM_STATE_PATH, "LLM_OFF": config.LLM_OFF}
        config.DATA_DIR = base
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.LLM_OFF = False
        FakeHandler.script = []
        FakeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        llm.set_config({"base_url": f"http://127.0.0.1:{port}/v1", "api_key": "sk-test-1234567890abcdef",
                        "model": "fake-model", "deep_model": "fake-model", "temperature": 0.1})
        llm._cache_stats.update({"calls": 0, "hits": 0, "writes": 0})

    def tearDown(self):
        FakeHandler.script = []
        FakeHandler.requests = []
        self.server.shutdown()
        self.server.server_close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def _serve_one(self, content: str):
        FakeHandler.script = [{"content": content, "finish": "stop"}]

    def test_second_identical_call_hits_cache(self):
        self._serve_one('{"n": 1}')
        msgs = [{"role": "user", "content": "同一份材料"}]
        first = llm.chat_json(msgs, temperature=0.3, max_tokens=500)
        second = llm.chat_json(msgs, temperature=0.3, max_tokens=500)
        self.assertEqual(first, {"n": 1})
        self.assertEqual(second, {"n": 1})
        self.assertEqual(len(FakeHandler.requests), 1, "第二次不该再打网络")
        self.assertEqual(llm.cache_stats()["hits"], 1)
        self.assertEqual(llm.cache_stats()["calls"], 1)
        self.assertEqual(llm.cache_size(), 1)

    def test_unparseable_output_is_not_cached(self):
        """一次失败不能被固化：解析不了的结果不许落盘。"""
        FakeHandler.script = [{"content": "抱歉，这段材料我读不懂。", "finish": "stop"},
                              {"content": '{"n": 2}', "finish": "stop"}]
        msgs = [{"role": "user", "content": "会失败的材料"}]
        with self.assertRaises(llm.LLMError):
            llm.chat_json(msgs, temperature=0.2, max_tokens=300)
        self.assertEqual(llm.cache_size(), 0, "坏输出不该进缓存")
        self.assertEqual(llm.chat_json(msgs, temperature=0.2, max_tokens=300), {"n": 2},
                         "上一次失败不该让这一次也失败")

    def test_poisoned_cache_entry_self_heals(self):
        """条目已经坏在盘上：命中后必须删掉重问，而不是永久炸在同一个键上。"""
        msgs = [{"role": "user", "content": "被污染的材料"}]
        endpoint = llm.get_config()["base_url"]
        llm._cache_write(llm.cache_key("fake-model", msgs, 0.2, None, endpoint),
                         "undefined", "fake-model")
        self._serve_one('{"n": 7}')
        self.assertEqual(llm.chat_json(msgs, temperature=0.2, max_tokens=None), {"n": 7})
        self.assertEqual(len(FakeHandler.requests), 1)
        self.assertEqual(llm.chat_json(msgs, temperature=0.2, max_tokens=None), {"n": 7},
                         "自愈后同一输入应重新用上缓存")
        self.assertEqual(len(FakeHandler.requests), 1)

    def test_cache_key_separates_gateway_endpoint(self):
        """同名模型换网关不能复用：两家模型对同一提示词的输出不是一回事。"""
        msgs = [{"role": "user", "content": "同一个模型名"}]
        self.assertNotEqual(llm.cache_key("fake-model", msgs, 0.2, None, "http://a/v1"),
                            llm.cache_key("fake-model", msgs, 0.2, None, "http://b/v1"))

    def test_cache_key_separates_prompt_model_params(self):
        self._serve_one('{"n": 1}')
        base = [{"role": "user", "content": "材料 A"}]
        llm.chat_json(base, temperature=0.3, max_tokens=500)
        llm.chat_json([{"role": "user", "content": "材料 B"}], temperature=0.3, max_tokens=500)
        llm.chat_json(base, temperature=0.9, max_tokens=500)
        llm.chat_json(base, temperature=0.3, max_tokens=900)
        self.assertEqual(len(FakeHandler.requests), 4, "输入不同就不该复用")
        self.assertEqual(llm.cache_stats()["hits"], 0)
        self.assertEqual(llm.cache_size(), 4)

    def test_can_be_disabled(self):
        llm.set_config({"cache_enabled": False})
        self._serve_one('{"n": 2}')
        msgs = [{"role": "user", "content": "不缓存"}]
        llm.chat_json(msgs)
        llm.chat_json(msgs)
        self.assertEqual(len(FakeHandler.requests), 2)
        self.assertEqual(llm.cache_size(), 0)
        self.assertFalse(llm.public_config()["cache_enabled"])
        self.assertTrue(llm.public_config()["api_key_set"])
        self.assertNotIn("sk-test-1234567890abcdef", json.dumps(llm.public_config()))

    def test_clear_cache(self):
        self._serve_one('{"n": 3}')
        llm.chat_json([{"role": "user", "content": "要清掉"}])
        self.assertEqual(llm.cache_size(), 1)
        removed = llm.clear_cache()
        self.assertEqual(removed, 1)
        self.assertEqual(llm.cache_size(), 0)
        self.assertFalse(llm.cache_dir().exists() and list(llm.cache_dir().glob("*.json")))

    def test_demo_mode_does_not_cache(self):
        llm.set_config({"api_key": ""})
        self.assertFalse(llm.enabled())
        self.assertEqual(llm.cache_size(), 0, "演示模式不该留下缓存")


class DiagnosticsTest(unittest.TestCase):
    """分类与建议：错误要能指向下一步动作，而不是只有一句「请求失败」。"""

    def test_network_exceptions_are_classified(self):
        import socket
        import urllib.error

        class SSLCertError(Exception):
            pass

        cases = [
            (urllib.error.URLError(OSError("Name or service not known")), "dns"),
            (urllib.error.URLError(ConnectionRefusedError("refused")), "refused"),
            (urllib.error.URLError(socket.timeout("The read operation timed out")), "timeout"),
            (urllib.error.URLError(SSLCertError("certificate verify failed")), "tls"),
            (urllib.error.URLError(OSError("tunnel connection failed")), "proxy"),
            (RuntimeError("一切都完了"), "network"),
        ]
        for exc, expected in cases:
            kind, hint = llm._classify_exception(exc)
            self.assertEqual(kind, expected, str(exc))
            self.assertTrue(hint, f"{expected} 必须带一句可行动建议")

    def test_exception_chain_is_walked(self):
        import urllib.error
        try:
            try:
                raise urllib.error.URLError("Connection refused")
            except urllib.error.URLError as inner:
                raise RuntimeError("包装层") from inner
        except RuntimeError as outer:
            kind, _ = llm._classify_exception(outer)
        self.assertEqual(kind, "refused", "要顺着 __cause__ 找到真正原因")

    def test_http_hints_cover_common_statuses(self):
        for status in (400, 401, 403, 404, 408, 422, 429, 500, 502, 503, 504):
            self.assertTrue(llm._http_hint(status), status)
        self.assertTrue(llm._http_hint(418))
        self.assertEqual(llm._http_hint(299), "")

    def test_details_are_redacted(self):
        masked = llm._safe_detail("header: Bearer sk-abcdefghijklmnop and url has api_key=supersecretvalue123")
        self.assertNotIn("sk-abcdefghijklmnop", masked)
        self.assertNotIn("supersecretvalue123", masked)
        self.assertIn("…", masked)

    def test_last_error_carries_hint_and_masked_key(self):
        old_state = config.LLM_STATE_PATH
        with tempfile.TemporaryDirectory() as td:
            config.LLM_STATE_PATH = Path(td) / "llm_state.json"
            try:
                llm._note_error("模型服务返回 HTTP 504", "gateway timeout", "http_error",
                                llm._http_hint(504))
                err = llm.last_error()
                self.assertTrue(err)
                self.assertEqual(err["code"], "http_error")
                self.assertIn("流式", err["hint"])
                self.assertIn("base_url", err)
            finally:
                config.LLM_STATE_PATH = old_state


class StreamUpstreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-stream-")
        base = Path(self.tmp.name)
        self.old = {"DATA_DIR": config.DATA_DIR, "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
                    "LLM_STATE_PATH": config.LLM_STATE_PATH, "LLM_OFF": config.LLM_OFF}
        config.DATA_DIR = base
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.LLM_OFF = False
        FakeHandler.script = []
        FakeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        llm.set_config({"base_url": f"http://127.0.0.1:{port}/v1", "api_key": "sk-test-1234567890abcdef",
                        "model": "fake-model", "deep_model": "fake-model", "temperature": 0.1})

    def tearDown(self):
        FakeHandler.script = []
        FakeHandler.requests = []
        self.server.shutdown()
        self.server.server_close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def test_chat_requests_stream_from_upstream(self):
        FakeHandler.script = [{"content": "拼回来的完整答案"}]
        out = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "拼回来的完整答案")
        self.assertTrue(FakeHandler.requests[-1]["stream"])

    def test_can_opt_out_of_streaming_upstream(self):
        llm.set_config({"stream_upstream": False})
        self.assertFalse(llm.public_config()["stream_upstream"])
        FakeHandler.script = [{"content": "非流式答案"}]
        out = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "非流式答案")
        self.assertFalse(FakeHandler.requests[-1]["stream"])


class LooseJsonTest(unittest.TestCase):
    def test_truncated_json_is_completed(self):
        self.assertEqual(llm.parse_json_loose('{"a": [1, 2, {"b": 3}'),
                         {"a": [1, 2, {"b": 3}]})
        self.assertEqual(llm.parse_json_loose('前言 {"units": [{"title":"甲"'),
                         {"units": [{"title": "甲"}]})

    def test_brackets_inside_strings_do_not_end_the_value(self):
        self.assertEqual(llm.parse_json_loose('{"a": "}"} 尾巴 }'), {"a": "}"})
        self.assertEqual(llm.parse_json_loose('{"escaped": "\\"{"} after'), {"escaped": '"{'})

    def test_trailing_comma_and_fences_still_work(self):
        self.assertEqual(llm.parse_json_loose('```json\n{"a":1,}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json_loose('前缀 {"a": [1,2,]} 后缀'), {"a": [1, 2]})

    def test_non_json_still_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.parse_json_loose("not json")
        with self.assertRaises(llm.LLMError):
            llm.parse_json_loose("")


if __name__ == "__main__":
    unittest.main()
