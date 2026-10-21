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

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.seen = {"headers": {k.lower(): v for k, v in self.headers.items()}, "payload": payload}
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ("你", "好"):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        if payload.get("response_format"):
            content = '{"hello":"world"}'
        else:
            content = "OK from fake"
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class LLMGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-llm-")
        base = Path(self.tmp.name)
        self.old = {
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
            "LLM_OFF": config.LLM_OFF,
        }
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
        self.server.shutdown()
        self.server.server_close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def test_live_chat_stream_json(self):
        self.assertTrue(llm.enabled())
        self.assertEqual(llm.mode(), "live")
        text = llm.chat([{"role": "user", "content": "hello"}], max_tokens=20)
        self.assertEqual(text, "OK from fake")
        pieces = list(llm.stream_chat([{"role": "user", "content": "hello"}]))
        self.assertEqual("".join(pieces), "你好")
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


if __name__ == "__main__":
    unittest.main()
