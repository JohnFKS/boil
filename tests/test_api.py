from __future__ import annotations

import json
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

from boil import config, llm
from boil.db import Database
from boil.server import App, Handler
from http.server import ThreadingHTTPServer


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-api-")
        base = Path(cls.tmp.name)
        cls.old = {
            "PACKS_DIR": config.PACKS_DIR,
            "DATA_DIR": config.DATA_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
            "TOKEN": config.TOKEN,
        }
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.TOKEN = ""
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        cls.db = Database(base / "api.db")
        cls.app = App(cls.db)
        Handler.app = cls.app
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.unit_id = None

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()
        Handler.app = None
        for key, value in cls.old.items():
            setattr(config, key, value)
        cls.tmp.cleanup()

    def call(self, method, path, payload=None, raw=False):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if raw:
                return r, body
            return json.loads(body)

    def test_01_health_and_static(self):
        h = self.call("GET", "/api/health")
        self.assertTrue(h["ok"])
        self.assertEqual(h["data"]["app"], "Boil")
        self.assertEqual(h["data"]["mode"], "demo")
        r, body = self.call("GET", "/", raw=True)
        self.assertIn("BOIL", body.decode())
        r, css = self.call("GET", "/css/app.css", raw=True)
        self.assertIn(b"--ember", css)
        r, js = self.call("GET", "/js/app.js", raw=True)
        self.assertIn(b"loadCore", js)

    def test_02_onboarding_and_views(self):
        out = self.call("POST", "/api/onboarding", {
            "name": "API 测试者", "pack_id": "boil.xueshen.learning",
            "goal_title": "学习力", "daily_minutes": 40, "deadline": "2026-12-31",
        })
        self.assertTrue(out["ok"])
        d = out["data"]
        self.assertTrue(d["has_goal"])
        self.assertEqual(d["counts"]["units"], 8)
        type(self).unit_id = d["next_units"][0]["id"]
        dash = self.call("GET", "/api/dashboard")["data"]
        self.assertEqual(dash["goal"]["id"], d["goal"]["id"])
        m = self.call("GET", "/api/map")["data"]
        self.assertEqual(len(m["nodes"]), 8)
        u = self.call("GET", f"/api/units/{type(self).unit_id}")["data"]
        self.assertTrue(u["unit"]["essence"])
        self.assertEqual(self.call("GET", "/api/me")["data"]["user"]["name"], "API 测试者")
        ins = self.call("GET", "/api/insights")["data"]
        self.assertEqual(len(ins["forecast"]), 7)
        self.assertTrue(all(f["due"] == 0 for f in ins["forecast"]))

    def test_03_socratic_sse_and_report(self):
        start = self.call("POST", "/api/socratic/start", {"unit_id": type(self).unit_id})["data"]
        sid = start["session"]["id"]
        data = json.dumps({"session_id": sid,
                           "message": "学习是模仿加练习，最后形成可以提取的记忆，需要输出和复习。"}).encode()
        req = urllib.request.Request(self.base + "/api/socratic/stream", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
        self.assertIn("event: delta", raw)
        self.assertIn("event: done", raw)
        self.assertIn("event: close", raw)
        done_block = [b for b in raw.split("\n\n") if b.startswith("event: done")]
        self.assertTrue(done_block)
        payload = json.loads(done_block[0].split("data: ", 1)[1])
        self.assertEqual(payload["engine"], "demo")
        rep = self.call("POST", "/api/socratic/finish", {"session_id": sid})["data"]
        self.assertTrue(rep["report"]["summary"])
        self.assertIn("facets", rep["report"])
        session = self.call("GET", f"/api/socratic/{sid}")["data"]
        self.assertEqual(session["session"]["status"], "completed")

    def test_04_feynman_review_insights(self):
        unit = self.call("GET", f"/api/units/{type(self).unit_id}")["data"]["unit"]
        fey = self.call("POST", "/api/feynman/evaluate", {
            "unit_id": type(self).unit_id, "mode": "peer",
            "text": "学习是模仿加练习，最后形成可以提取的记忆。提高刺激强度可以合书讲出来、做出来；提高刺激频度就是及时复习。但是睡眠不足时效果会变差。",
        })
        self.assertTrue(fey["ok"])
        self.assertGreater(fey["data"]["overall"], 0)
        q = self.call("GET", "/api/review?limit=5")["data"]
        self.assertTrue(q["cards"])
        card = q["cards"][0]
        rated = self.call("POST", f"/api/review/{card['id']}", {"rating": 3, "elapsed_ms": 3000})
        self.assertTrue(rated["ok"])
        self.assertIn("interval_label", rated["data"]["result"])
        ins = self.call("GET", "/api/insights")["data"]
        self.assertEqual(len(ins["forecast"]), 7)
        self.assertTrue(ins["retention"])

    def test_05_packs_compile_replan_export(self):
        cat = self.call("GET", "/api/packs")["data"]
        self.assertEqual(cat["packs"][0]["installed"], True)
        detail = self.call("GET", "/api/packs/boil.xueshen.learning")["data"]
        self.assertEqual(len(detail["units"]), 8)
        comp = self.call("POST", "/api/packs/compile", {
            "title": "API 材料", "subject": "学习科学", "text": (
                "## 记忆\n学习是形成可提取的记忆。\n- 合书复述\n- 间隔复习\n注意睡眠。\n\n"
                "## 迁移\n迁移需要变式练习。\n- 换情境\n- 举反例\n"
            ), "use_llm": False,
        })
        self.assertTrue(comp["ok"])
        self.assertTrue(comp["data"]["pack"]["units"])
        new_pack = comp["data"]["pack"]["id"]
        imported = self.call("POST", "/api/packs/import", {"pack": comp["data"]["pack"]})
        self.assertTrue(imported["ok"])
        plan = self.call("POST", "/api/plan/rebuild", {"daily_minutes": 55, "deadline": "2026-11-30"})
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["data"]["goal"]["daily_minutes"], 55)
        r, body = self.call("GET", "/api/export", raw=True)
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        exported = json.loads(body)
        self.assertIn("goals", exported)
        self.assertNotIn("api_key", json.dumps(exported.get("llm", {})))
        search = self.call("GET", "/api/search?q=" + urllib.parse.quote("学习"))["data"]
        self.assertTrue(search["units"])
        events = self.call("GET", "/api/events?limit=10")["data"]
        self.assertTrue(events["events"])

    def test_06_llm_settings_demo(self):
        cfg = self.call("GET", "/api/settings/llm")["data"]
        self.assertEqual(cfg["mode"], "demo")
        try:
            self.call("POST", "/api/settings/llm/test", {})
            self.fail("should fail in demo")
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "not_configured")

    def test_07_unknown_route(self):
        try:
            self.call("GET", "/api/nope")
            self.fail("should 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == "__main__":
    unittest.main()
