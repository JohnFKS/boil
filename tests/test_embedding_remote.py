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

from boil import embeddings


class FakeEmbedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        FakeEmbedHandler.seen.append({
            "auth": self.headers.get("Authorization"),
            "payload": payload,
        })
        inputs = payload.get("input") or []
        rows = []
        for i, text in enumerate(inputs):
            # 故意倒序 + index，验证按 index 排序而不是盲目 zip
            rows.append({"index": i, "embedding": [float(len(text)), 1.0, 0.0, 0.5]})
        rows.reverse()
        body = json.dumps({"data": rows, "model": payload.get("model"),
                           "usage": {"prompt_tokens": len(inputs) * 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class RemoteEmbeddingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-emb-")
        base = Path(self.tmp.name)
        self.old_config = embeddings._CONFIG_OVERRIDE
        self.old_cache = embeddings._CACHE_OVERRIDE
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
        embeddings.LAST_ERROR = ""
        embeddings._fail_until = 0.0
        embeddings._LAST_ENGINE = embeddings.LOCAL_MODEL
        FakeEmbedHandler.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEmbedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        embeddings._CONFIG_OVERRIDE = self.old_config
        embeddings._CACHE_OVERRIDE = self.old_cache
        self.tmp.cleanup()

    def test_local_fallback_without_key(self):
        embeddings.set_config(api_key="")
        self.assertFalse(embeddings.configured())
        vec = embeddings.embed("学习是记忆")
        self.assertEqual(len(vec), embeddings.LOCAL_DIM)
        ranked = embeddings.rank("矩阵", [{"id": "a", "title": "矩阵的秩"},
                                           {"id": "b", "title": "极限"}], embeddings.unit_text)
        self.assertEqual(ranked[0][1]["id"], "a")
        self.assertEqual(embeddings.last_engine(), embeddings.LOCAL_MODEL)

    def test_remote_batch_cache_probe(self):
        embeddings.set_config(base_url=self.base, model="fake-embed",
                              api_key="sk-embedding-test-1234567890", timeout=5)
        vecs = embeddings.embed_texts(["a", "hello"])
        self.assertEqual(len(vecs), 2)
        self.assertEqual(len(vecs[0]), 4)
        self.assertEqual(embeddings.last_engine(), "fake-embed")
        self.assertEqual(embeddings.get_config()["dim"], 4)
        self.assertEqual(FakeEmbedHandler.seen[0]["auth"], "Bearer sk-embedding-test-1234567890")
        self.assertEqual(embeddings.probe()["ok"], True)
        # 第二次从缓存读
        before = embeddings._STATS["cached"]
        embeddings.embed_texts(["a", "hello"])
        self.assertGreater(embeddings._STATS["cached"], before)

    def test_failure_visible_and_cooldown(self):
        embeddings.set_config(base_url="http://127.0.0.1:9/v1", model="fake-embed",
                              api_key="sk-embedding-test-1234567890", timeout=1)
        self.assertIsNone(embeddings.embed_texts(["a"]))
        self.assertTrue(embeddings.last_error())
        self.assertTrue(embeddings.in_cooldown())
        self.assertFalse(embeddings.enabled())
        self.assertEqual(len(embeddings.embed("a")), embeddings.LOCAL_DIM)
        self.assertEqual(embeddings.last_engine(), embeddings.LOCAL_MODEL)
        p = embeddings.probe()
        self.assertFalse(p["ok"])


if __name__ == "__main__":
    unittest.main()
