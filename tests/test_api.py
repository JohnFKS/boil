from __future__ import annotations

import http.client
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

from boil import config, embeddings, llm, quality, socratic
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
        cls.old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
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
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = cls.old_emb
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
        self.assertEqual(r.headers.get("Cache-Control"), "no-cache")
        etag = r.headers.get("ETag")
        self.assertTrue(etag)
        guard = self.call("GET", "/js/boot-guard.js", raw=True)[1]
        self.assertIn(b"__boilBootReady", guard)
        try:
            self.call("GET", "/js/missing-module.js")
            self.fail("missing asset should 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.request("GET", "/js/app.js", headers={"If-None-Match": etag})
            cached = conn.getresponse()
            cached.read()
            self.assertEqual(cached.status, 304)
            self.assertEqual(cached.getheader("ETag"), etag)
        finally:
            conn.close()

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
        self.assertIn("heat", u)
        self.assertIn("pn", u)
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
        multi = self.call("POST", "/api/packs/compile", {
            "title": "多文件材料", "subject": "学习科学", "text": "补充：睡眠影响记忆巩固。",
            "documents": [
                {"name": "记忆.md", "text": "## 记忆形成\n学习会形成可提取的记忆，模仿与练习共同塑造神经回路。"},
                {"name": "复习.txt", "text": "间隔复习能提高长期保持。合书复述比重复阅读更有效。"},
            ],
            "use_llm": False,
        })
        self.assertTrue(multi["ok"])
        multi_pack = multi["data"]["pack"]
        self.assertTrue(multi_pack["units"])
        self.assertEqual([d["name"] for d in multi_pack["source"]["documents"]],
                         ["记忆.md", "复习.txt"])
        self.assertGreater(multi_pack["source"]["material_chars"], 60)
        try:
            self.call("POST", "/api/packs/compile", {
                "title": "坏输入", "subject": "测试", "text": "x" * 80,
                "documents": "not-a-list", "use_llm": False,
            })
            self.fail("documents must be a list")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
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

    def test_08_advice_heat_quality(self):
        adv = self.call("GET", "/api/advice?minutes=30")["data"]
        self.assertIn(adv["mode"], ("audio", "digest", "socratic", "recall", "feynman", "benchmark", "done"))
        self.assertIn("reason", adv)
        self.assertTrue(adv["ladder"])
        fb = self.call("POST", "/api/advice/feedback", {"action": "dismissed", "advice": adv})
        self.assertTrue(fb["ok"])
        ins = self.call("GET", "/api/insights")["data"]
        self.assertIn("average_heat", ins)
        self.assertIn("records", ins)
        packs = self.call("GET", "/api/packs")["data"]["packs"]
        self.assertTrue(all("quality" in p for p in packs))
        self.assertGreaterEqual(packs[0]["quality"]["atomic_rate"], 0.99)
        unit = self.call("GET", f"/api/units/{type(self).unit_id}")["data"]
        self.assertIn("heat_label", unit)
        self.assertIn("missing", unit["pn"])
        self.assertIn("related", unit)

    def test_09_notes_import_and_related(self):
        notes = self.call("POST", f"/api/units/{type(self).unit_id}/notes",
                          {"kind": "mnemonic", "content": "把复习想成压路基"})
        self.assertTrue(notes["ok"])
        self.assertEqual(notes["data"]["mnemonic"]["content"], "把复习想成压路基")
        unit = self.call("GET", f"/api/units/{type(self).unit_id}")["data"]
        self.assertEqual(unit["notes"]["mnemonic"]["content"], "把复习想成压路基")
        q = self.call("GET", "/api/review?limit=5")["data"]
        self.assertTrue(any(c.get("mnemonic") == "把复习想成压路基" for c in q["cards"]))
        search = self.call("GET", "/api/search?q=" + urllib.parse.quote("神经回路"))["data"]
        self.assertEqual(search["engine"], "local-hash-256")
        self.assertTrue(search["units"])
        r, body = self.call("GET", "/api/export", raw=True)
        exported = json.loads(body)
        self.assertIn("unit_notes", exported)
        res = self.call("POST", "/api/import", {"data": exported, "mode": "merge"})
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["data"]["units"], 8)
        self.assertGreaterEqual(res["data"]["notes"], 1)
        self.assertGreaterEqual(res["data"]["goals"], 1)

    def test_10_anki_export_and_security_headers(self):
        r, body = self.call("GET", "/api/export/anki", raw=True)
        self.assertIn("text/tab-separated-values", r.headers.get("Content-Type", ""))
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        text = body.decode()
        self.assertIn("#separator:tab", text)
        self.assertIn("#html:true", text)
        self.assertIn("#tags column:3", text)
        rows = [line for line in text.splitlines() if line and not line.startswith("#")]
        self.assertTrue(rows)
        self.assertTrue(all(len(row.split("\t")) == 3 for row in rows))
        self.assertIn("Boil", rows[0].split("\t")[2])
        self.assertIn("来源：", rows[0])
        h = self.call("GET", "/api/health")
        self.assertTrue(h["ok"])
        for path in ("/api/health", "/"):
            resp, _ = self.call("GET", path, raw=True)
            self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
            self.assertIn("camera=()", resp.headers.get("Permissions-Policy", ""))
            self.assertIn("script-src 'self'", resp.headers.get("Content-Security-Policy", ""))

    def test_12_compile_outline_and_plan(self):
        comp = self.call("POST", "/api/packs/compile", {
            "title": "大纲测试", "subject": "学习科学", "use_llm": False,
            "text": (
                "## 记忆的形成\n学习会形成可提取的记忆，模仿与练习共同塑造回路。\n- 合书复述\n\n"
                "## 间隔复习\n分散练习提高长期保持。\n- 使用复习卡片\n\n"
                "## 知识迁移\n变式练习帮助识别问题结构。\n- 换情境\n- 举反例\n"
            ),
        })
        self.assertTrue(comp["ok"])
        pack = comp["data"]["pack"]
        self.assertEqual(pack["generated_by"], "rules")
        self.assertTrue(pack["outline"])
        self.assertTrue(pack["study_plan"])
        self.assertTrue(pack["digest"]["overview"])
        titles = {u["title"] for u in pack["units"]}
        covered = {t for entry in pack["outline"] for t in entry["unit_titles"]}
        self.assertTrue(titles <= covered)
        goal = self.call("POST", "/api/onboarding", {
            "name": "大纲测试者", "pack_id": pack["id"],
            "goal_title": "学会这三件事", "daily_minutes": 40, "deadline": "2026-12-31",
        })["data"]
        dash = self.call("GET", "/api/dashboard")["data"]
        self.assertTrue(dash["plan"].get("outline"), "计划里要带上 AI 大纲")
        meta = goal["goal"]["meta"]
        self.assertEqual(meta["ai_generated_by"], "rules")
        self.assertTrue(meta["ai_digest"]["overview"])
        self.assertTrue(meta["ai_outline"])
        self.assertTrue(meta["ai_study_plan"])

    def test_13_ai_synthesis_surfaces(self):
        comp = self.call("POST", "/api/packs/compile", {
            "title": "AI 合成测试", "subject": "学习科学", "use_llm": False,
            "text": (
                "## 提取练习\n反复提取才强化记忆，重读只会带来懂了的错觉。\n- 合书复述\n\n"
                "## 间隔安排\n复习要排在快要忘的时候。\n- 到期优先\n"
            ),
        })["data"]["pack"]
        self.call("POST", "/api/onboarding", {
            "name": "合成测试者", "pack_id": comp["id"], "goal_title": "学会提取与安排",
            "daily_minutes": 40, "deadline": "2026-12-31",
        })
        unit_id = self.call("GET", "/api/map")["data"]["nodes"][0]["id"]
        u = self.call("GET", "/api/units/" + urllib.parse.quote(unit_id))["data"]["unit"]
        self.assertTrue(u["angle"], "每个单元要有怎么学的导读")
        dash = self.call("GET", "/api/dashboard")["data"]
        self.assertTrue(dash["digest"]["overview"])
        self.assertEqual(dash["generated_by"], "rules")
        self.assertTrue(dash["study_plan_phases"])
        syn = self.call("POST", "/api/insights/synthesize", {})["data"]
        self.assertEqual(syn["source"], "rules")
        self.assertTrue(syn["summary"])
        self.assertTrue(syn["suggestions"])
        self.assertTrue(syn["risks"])
        self.assertIsInstance(syn["warnings"], list)

    def test_15_inspiration_notes(self):
        unit_id = self.call("GET", "/api/map")["data"]["nodes"][0]["id"]
        created = self.call("POST", "/api/notes", {
            "quote": "提取一次，记忆痕迹就被强化一次",
            "content": "可以把它变成每天早上的第一个动作。",
            "unit_id": unit_id, "source": "unit",
        })["data"]
        note_id = created["id"]
        self.assertTrue(note_id)
        self.assertEqual(created["note"]["unit_title"], self.call("GET", "/api/map")["data"]["nodes"][0]["title"])
        loose = self.call("POST", "/api/notes", {"content": "只有想法没有引用", "source": "dashboard"})["data"]
        self.assertTrue(loose["id"])
        listed = self.call("GET", "/api/notes")["data"]
        self.assertEqual(listed["total"], 2)
        self.assertIn("提取一次", json.dumps(listed, ensure_ascii=False))
        filtered = self.call("GET", "/api/notes?unit_id=" + urllib.parse.quote(unit_id))["data"]
        self.assertEqual(len(filtered["notes"]), 1)
        searched = self.call("GET", "/api/notes?q=" + urllib.parse.quote("早上"))["data"]
        self.assertEqual(len(searched["notes"]), 1)
        r, body = self.call("GET", "/api/notes/export", raw=True)
        markdown = body.decode()
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        self.assertIn("提取一次", markdown)
        self.assertIn("只有想法没有引用", markdown)
        try:
            self.call("POST", "/api/notes", {"quote": "", "content": "   "})
            self.fail("empty note should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        long_note = self.call("POST", "/api/notes", {"quote": "y" * 3000})["data"]
        self.assertEqual(len(long_note["note"]["quote"]), 2000)
        self.call("POST", "/api/notes/delete", {"id": note_id})
        self.call("POST", "/api/notes/delete", {"id": long_note["id"]})
        self.call("POST", "/api/notes/delete", {"id": loose["id"]})
        self.assertEqual(self.call("GET", "/api/notes")["data"]["total"], 0)
        try:
            self.call("POST", "/api/notes/delete", {"id": "no-such-note"})
            self.fail("missing note should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/notes/delete", {})
            self.fail("missing id should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_16_mnemonic_requires_model_and_recall_roundtrip(self):
        unit_id = self.call("GET", "/api/map")["data"]["nodes"][0]["id"]
        try:
            self.call("POST", "/api/units/" + urllib.parse.quote(unit_id) + "/mnemonic", {})
            self.fail("demo mode should refuse mnemonic generation")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)
            payload = json.loads(exc.read())
            self.assertEqual(payload["error"]["code"], "ai_not_configured")
            self.assertNotIn("api_key", json.dumps(payload))
        try:
            self.call("POST", "/api/units/nope/mnemonic", {})
            self.fail("missing unit should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        queue = self.call("GET", "/api/review?limit=5")["data"]
        card_id = queue["cards"][0]["id"]
        res = self.call("POST", f"/api/review/{urllib.parse.quote(card_id)}", {
            "rating": 2, "elapsed_ms": 6000, "recall": "合上书讲出本质与边界。",
        })["data"]
        self.assertTrue(res["result"]["interval_label"])
        detail = self.call("GET", "/api/units/" + urllib.parse.quote(queue["cards"][0]["unit_id"]))["data"]
        self.assertTrue(any(a["kind"] == "review_recall" and a["response"] == "合上书讲出本质与边界。"
                            for a in detail["attempts"]), "HTTP 层要把回忆写进 attempts")

    def test_17_cards_endpoint_and_mistake_tags(self):
        cards = self.call("GET", "/api/cards?limit=5")["data"]
        self.assertTrue(cards["cards"])
        self.assertGreaterEqual(cards["total"], len(cards["cards"]))
        sample = cards["cards"][0]
        for key in ("id", "unit_id", "front", "state", "due_label", "is_due", "mistake_tags"):
            self.assertIn(key, sample)
        self.assertNotIn("mistake_tags_json", json.dumps(cards))
        unit_id = sample["unit_id"]
        scoped = self.call("GET", "/api/cards?unit_id=" + urllib.parse.quote(unit_id))["data"]
        self.assertTrue(all(c["unit_id"] == unit_id for c in scoped["cards"]))
        bad_state = self.call("GET", "/api/cards?state=nonsense")["data"]
        self.assertIsInstance(bad_state["cards"], list)

        queue = self.call("GET", "/api/review?limit=5")["data"]
        self.assertEqual(len(queue["mistake_tags"]), 6)
        card = queue["cards"][0]
        res = self.call("POST", f"/api/review/{urllib.parse.quote(card['id'])}", {
            "rating": 1, "elapsed_ms": 2000, "mistake_tags": ["boundary", "unknown_key"],
        })["data"]
        self.assertEqual(res["mistake_tags"], ["boundary"])
        echoed = self.call("GET", "/api/cards?unit_id=" + urllib.parse.quote(card["unit_id"]))["data"]
        marked = [c for c in echoed["cards"] if c["id"] == card["id"]][0]
        self.assertEqual(marked["mistake_tags"], ["boundary"])
        self.assertEqual(marked["mistake_labels"], ["条件/边界漏了"])
        mist = self.call("GET", "/api/insights")["data"]["mistakes"]
        self.assertEqual(mist["tagged_reviews"], 1)
        self.assertEqual(mist["tags"][0]["key"], "boundary")

    def test_18_material_fix(self):
        res = self.call("POST", "/api/material/fix", {
            "text": "#　提取练习\r\n• 合书复述\r\n公式 \\[x^2\\] 与 `\\[y\\]`\r\n",
        })["data"]
        self.assertTrue(res["changed"])
        self.assertIn("# 提取练习", res["text"])
        self.assertIn("- 合书复述", res["text"])
        self.assertIn("$$x^2$$", res["text"])
        self.assertIn("`\\[y\\]`", res["text"], "行内代码里的公式不该被改写")
        self.assertTrue(res["applied"])
        untouched = self.call("POST", "/api/material/fix", {"text": "# 已经很干净\n正文。"})["data"]
        self.assertFalse(untouched["changed"])
        self.assertEqual(untouched["applied"], [])
        empty = self.call("POST", "/api/material/fix", {"text": "   "})["data"]
        self.assertEqual(empty["text"], "")
        try:
            self.call("POST", "/api/material/fix", {"text": "x" * 200_001})
            self.fail("oversized text should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/material/fix", {"text": "abc", "options": "nope"})
            self.fail("bad options should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        disabled = self.call("POST", "/api/material/fix", {
            "text": "• 项目", "options": {"bullets": False},
        })["data"]
        self.assertTrue(disabled["text"].startswith("•"))

    def test_19_cards_preview_confirm_and_quick_ask(self):
        unit_id = self.call("GET", "/api/map")["data"]["nodes"][0]["id"]
        preview = self.call("POST", "/api/cards/preview", {
            "text": "提取练习：合上书，用自己的话把核心讲出来。\n间隔复习？在快要忘但还没忘的时候最有效。",
            "unit_id": unit_id,
        })["data"]
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["engine"], "rules")
        self.assertTrue(preview["cards"])
        self.assertTrue(preview["tool_args"]["cards"])
        queue_before = self.call("GET", "/api/review?limit=100")["data"]["new_total"]
        confirmed = self.call("POST", "/api/cards/confirm", {
            "cards": preview["cards"], "unit_id": unit_id,
        })["data"]
        self.assertEqual(confirmed["count"], len(preview["cards"]))
        queue_after = self.call("GET", "/api/review?limit=100")["data"]["new_total"]
        self.assertEqual(queue_after - queue_before, confirmed["count"],
                         "预览不该进队列，确认后才算")

        try:
            self.call("POST", "/api/cards/preview", {"text": "短"})
            self.fail("too short should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/cards/confirm", {"cards": "not-a-list"})
            self.fail("bad cards should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/ask", {"question": "为什么", "context": "材料"})
            self.fail("demo mode should 503")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 503)
            self.assertEqual(json.loads(exc.read())["error"]["code"], "ai_not_configured")

    def test_20_style_test_flow(self):
        questions = self.call("GET", "/api/styles/questions")["data"]
        self.assertEqual(len(questions["questions"]), 5)
        self.assertTrue(all("weights" not in o for q in questions["questions"] for o in q["options"]),
                        "不能把权重泄漏给前端")
        answers = [0, 1, 0, 2, 1]
        applied = self.call("POST", "/api/styles/apply", {"answers": answers})["data"]
        self.assertTrue(applied["persona"]["label"])
        self.assertTrue(applied["persona"]["strategy"])
        self.assertEqual(applied["persona"]["answers"], answers)
        dash = self.call("GET", "/api/dashboard")["data"]
        self.assertEqual(dash["goal"]["meta"]["persona"]["key"], applied["persona"]["key"])

        # 人设要真的进到追问的提示词里，而不只是存起来
        captured = {}
        old_enabled, old_chat = llm.enabled, llm.chat
        llm.enabled = lambda: True
        llm.chat = lambda messages, **kw: captured.update({"system": messages[0]["content"]}) or "先说说你的理解？"
        try:
            unit_id = self.call("GET", "/api/map")["data"]["nodes"][0]["id"]
            goal_id = dash["goal"]["id"]
            session = self.call("POST", "/api/socratic/start", {"unit_id": unit_id, "goal_id": goal_id})["data"]
            list(socratic.stream_reply(self.db, config.DEFAULT_USER,
                                       session["session"]["id"], "我先说说我的理解。"))
        finally:
            llm.enabled, llm.chat = old_enabled, old_chat
        self.assertIn("这位学习者的带法", captured.get("system", ""))
        self.assertIn(applied["persona"]["strategy"], captured.get("system", ""))

        try:
            self.call("POST", "/api/styles/apply", {"answers": [0, 1]})
            self.fail("wrong answer count should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/styles/apply", {"answers": [0, 1, 2, 3, 99]})
            self.fail("out-of-range option should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_21_duplicate_endpoint_and_merge_http(self):
        dup = self.call("GET", "/api/units/duplicates")["data"]
        self.assertIn("pairs", dup)
        self.assertEqual(dup["engine"], "rules")
        self.assertGreaterEqual(dup["checked"], 1)
        db = self.db
        goal = db.one("SELECT id FROM goals WHERE status='active' ORDER BY created_at DESC LIMIT 1")
        from boil.utils import now_iso
        for uid, order in (("unit-dup-a", 90), ("unit-dup-b", 91)):
            db.upsert("units", {"id": uid, "goal_id": goal["id"], "pack_id": "p",
                                "pack_unit_id": uid, "title": "间隔复习",
                                "essence": "在快要忘但还没忘的时候复习最有效",
                                "order_index": order, "created_at": now_iso(),
                                "updated_at": now_iso()}, "id")
        listed = self.call("GET", "/api/units/duplicates")["data"]
        self.assertTrue(listed["pairs"], "重复的一对要被检出")
        pair = listed["pairs"][0]
        self.assertEqual(sorted([pair["keep"], pair["merge"]]), ["unit-dup-a", "unit-dup-b"])
        self.assertTrue(pair["titles"])
        self.assertEqual(pair["basis"], "lexical", "没配嵌入时依据要说清楚是字面")
        self.assertTrue(pair["allowed"])
        self.assertIsNone(pair["semantic"], "云端向量不可用就不能编一个语义分")
        self.assertGreaterEqual(pair["lexical"], quality.MERGE_LEXICAL_FLOOR)
        self.assertEqual(listed["engine"], "rules")
        self.assertEqual(listed["judged"], 0, "字面过硬的不该白花模型判定")
        self.assertIn("candidate", listed["thresholds"])
        nodes_before = len(self.call("GET", "/api/map")["data"]["nodes"])
        merged = self.call("POST", "/api/units/unit-dup-b/merge", {"into": "unit-dup-a"})["data"]
        self.assertEqual(merged["into"], "unit-dup-a")
        nodes_after = self.call("GET", "/api/map")["data"]["nodes"]
        self.assertEqual(len(nodes_after), nodes_before - 1)
        self.assertNotIn("unit-dup-b", [n["id"] for n in nodes_after])
        detail = self.call("GET", "/api/units/unit-dup-b")["data"]
        self.assertEqual(detail["merged_into"]["id"], "unit-dup-a")
        after = self.call("GET", "/api/units/duplicates")["data"]
        self.assertEqual([p for p in after["pairs"] if p["merge"] == "unit-dup-b"], [],
                         "合并后不该再被推荐")
        try:
            self.call("POST", "/api/units/unit-dup-a/merge", {"into": "unit-dup-b"})
            self.fail("merging back into an absorbed unit should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/units/unit-dup-a/merge", {})
            self.fail("missing into should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_22_bad_backup_rolls_back_and_says_400(self):
        """用户可以手改备份文件：不合法要报 400 并保证原数据一条不少，不能甩 500。"""
        r, body = self.call("GET", "/api/export", raw=True)
        dump = json.loads(body)
        goals_before = len(self.call("GET", "/api/goals")["data"]["goals"])
        cards_before = self.call("GET", "/api/cards?limit=200")["data"]["total"]
        poison = json.loads(json.dumps(dump))
        unit_id = (poison.get("units") or [{}])[0].get("id") or "unit-x"
        poison.setdefault("cards", []).append({
            "id": "cd-poison", "user_id": "local", "unit_id": unit_id,
            "front": None,                       # cards.front 是 NOT NULL
            "back": "坏掉的一行", "state": "new", "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z"})
        try:
            self.call("POST", "/api/import",
                      {"data": poison, "mode": "replace", "confirm": "IMPORT"})
            self.fail("坏备份应该报 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400, "数据不合法的导入不该报 500")
            payload = json.loads(exc.read().decode())
        self.assertEqual(payload["error"]["code"], "import_conflict")
        self.assertIn("回滚", payload["error"]["message"])
        self.assertEqual(len(self.call("GET", "/api/goals")["data"]["goals"]), goals_before,
                         "导入失败把目标弄丢了")
        self.assertEqual(self.call("GET", "/api/cards?limit=200")["data"]["total"], cards_before,
                         "导入失败把复习卡弄丢了")
        self.assertIsNone(self.db.one("SELECT id FROM cards WHERE id='cd-poison'"),
                          "坏行不能已经进去了才报错")

    def test_07_unknown_route(self):
        try:
            self.call("GET", "/api/nope")
            self.fail("should 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_11_suggest_meta(self):
        out = self.call("POST", "/api/packs/suggest", {
            "text": "## 记忆的形成\n学习会形成可提取的记忆，模仿与练习共同塑造神经回路。\n"
                    "合书复述比重复阅读更有效，因为提取本身会改变记忆的强度。",
        })
        self.assertTrue(out["ok"])
        data = out["data"]
        self.assertEqual(data["source"], "rules")
        self.assertTrue(data["title"])
        self.assertTrue(data["goal_title"])
        multi = self.call("POST", "/api/packs/suggest", {
            "text": "",
            "documents": [
                {"name": "睡眠与巩固.md", "text": "## 睡眠如何巩固记忆\n快速眼动睡眠期间海马体回放白天的经历。"},
                {"name": "交错练习.txt", "text": "交错练习帮助识别题型，需要先辨别再选择方法。"},
            ],
        })
        self.assertTrue(multi["ok"])
        self.assertTrue(multi["data"]["title"])
        try:
            self.call("POST", "/api/packs/suggest", {"text": "太短了"})
            self.fail("short material should 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        try:
            self.call("POST", "/api/packs/suggest", {"text": "x" * 80, "documents": "not-a-list"})
            self.fail("documents must be a list")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


if __name__ == "__main__":
    unittest.main()
