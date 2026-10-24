"""韧性测试：坏消息进来的时候，它是诚实报错还是假装没事 / 直接崩。

安全测试问"攻击者能不能得逞"，这里问两个更日常的问题：
1. 同时点、快点、点两下，数据会不会算错或写坏；
2. 配置文件、内容包、上游模型坏掉时，用户看到的是能读的错，还是白屏和 500。
"""
from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, embeddings, llm  # noqa: E402
from boil.db import Database  # noqa: E402
from boil.server import App, Handler, Server  # noqa: E402


class ResilienceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-res-")
        cls.base_dir = Path(cls.tmp.name)
        cls.old = {k: getattr(config, k) for k in
                   ("PACKS_DIR", "DATA_DIR", "LLM_CONFIG_PATH", "LLM_STATE_PATH", "TOKEN", "MAX_BODY_BYTES")}
        config.DATA_DIR = cls.base_dir
        config.PACKS_DIR = cls.base_dir / "packs"
        config.LLM_CONFIG_PATH = cls.base_dir / "llm.json"
        config.LLM_STATE_PATH = cls.base_dir / "llm_state.json"
        config.TOKEN = ""
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        cls.old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = cls.base_dir / "embedding.json"
        embeddings._CACHE_OVERRIDE = cls.base_dir / "embedding_cache.json"
        llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "none"})
        cls.db = Database(cls.base_dir / "res.db")
        Handler.app = App(cls.db)
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = cls.server.server_address[:2]

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

    # ---------------- 请求helpers ----------------
    def call(self, method, path, payload=None, timeout=60):
        """一次请求一条连接：并发测试要考的是"N 条并发连接"，不是连接复用。"""
        data = json.dumps(payload).encode() if payload is not None else None
        conn = http.client.HTTPConnection(*self.origin, timeout=timeout)
        try:
            conn.request(method, path, body=data,
                         headers={"Content-Type": "application/json"} if data else {})
            resp = conn.getresponse()
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except ValueError:
                return resp.status, {"ok": False, "raw": body[:400].decode("utf-8", "replace")}
        finally:
            conn.close()

    def ok(self, method, path, payload=None):
        status, out = self.call(method, path, payload)
        self.assertEqual(status, 200,
                         f"{method} {path} → {status} {json.dumps(out, ensure_ascii=False)[:200]}")
        self.assertTrue(out.get("ok"), out)
        return out["data"]

    def seed(self, name: str = "韧性测试者"):
        return self.ok("POST", "/api/onboarding", {
            "name": name, "pack_id": "boil.xueshen.learning",
            "goal_title": "韧性", "daily_minutes": 30, "deadline": "2026-12-31"})

    def count(self, table, sql=None):
        return self.db.scalar(sql or f"SELECT COUNT(*) FROM {table}", (), 0)

    def burst(self, jobs):
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(jobs)))) as pool:
            futures = [pool.submit(self.call, *job) for job in jobs]
            concurrent.futures.wait(futures, timeout=240)
        return [f.result() if f.exception() is None else f.exception() for f in futures]

    @staticmethod
    def landed(result):
        return isinstance(result, tuple) and result[0] == 200 and result[1].get("ok")


class ConcurrencyTest(ResilienceFixture):
    def test_parallel_note_writes_all_land(self):
        self.seed()
        total = 24
        results = self.burst([("POST", "/api/notes", {"quote": f"并发 {i}", "content": f"第 {i} 条"})
                              for i in range(total)])
        missing = [r for r in results if not self.landed(r)]
        self.assertFalse(missing, f"并发写入被吞掉 {len(missing)} 条：{missing[:2]}")
        stored = {n["quote"] for n in self.ok("GET", "/api/notes")["notes"]}
        self.assertEqual(len(stored & {f"并发 {i}" for i in range(total)}), total, "有笔记没落库")

    def test_mixed_read_write_storm_keeps_the_database_consistent(self):
        self.seed()
        goals_before = self.count("goals")
        reads = ["/api/dashboard", "/api/advice", "/api/insights", "/api/review?limit=5",
                 "/api/map", "/api/search?q=" + urllib.parse.quote("模仿"), "/api/events", "/api/goals"]
        jobs = []
        for i, path in enumerate(reads * 4):
            jobs.append(("GET", path) if i % 3 else
                        ("POST", "/api/notes", {"content": f"风暴 {i}"}))
        results = self.burst(jobs)
        self.assertFalse([r for r in results if not isinstance(r, tuple)],
                         f"并发把请求直接打挂了：{[r for r in results if not isinstance(r, tuple)][:2]}")
        crashed = [(job, r[0], r[1]) for job, r in zip(jobs, results)
                   if isinstance(r, tuple) and r[0] >= 500]
        self.assertFalse(crashed, f"读写并发打出 5xx：{crashed[:3]}")
        self.assertEqual(self.db.scalar("PRAGMA integrity_check", (), ""), "ok")
        self.assertEqual(self.count("users"), 1, "并发把用户表写重了")
        self.assertEqual(self.count("goals"), goals_before, "只读+写笔记的并发凭空造出了目标")

    def test_keyboard_burst_reviews_different_cards_all_get_scheduled(self):
        self.seed()
        cards = self.ok("GET", "/api/review?limit=12")["cards"]
        self.assertGreaterEqual(len(cards), 4, "演示包没给出够复习的卡")
        results = self.burst([("POST", f"/api/review/{urllib.parse.quote(c['id'])}",
                               {"rating": 3 + (i % 2)}) for i, c in enumerate(cards)])
        bad = [r for r in results if not self.landed(r)]
        self.assertFalse(bad, f"并发复习失败：{bad[:2]}")
        for row in self.db.query("SELECT id, state, stability, difficulty, due_at, reps FROM cards"):
            self.assertIn(row["state"], ("new", "learning", "review", "relearn"), "写进了非法状态")
            self.assertTrue(row["due_at"] and "T" in row["due_at"], "到期时间格式坏了")
            if row["reps"] > 0:      # 没复习过的新卡 stability 本来就是 0
                self.assertGreaterEqual(row["stability"], 1e-6, f"卡片 {row['id']} 稳定性被写坏")
                self.assertGreater(row["difficulty"], 0)
                self.assertLessEqual(row["difficulty"], 10)

    def test_double_submit_on_one_card_leaves_a_self_consistent_card(self):
        """连点两次"记得"是真的会发生：不强求只算一次，但卡片计数必须和日志对得上。"""
        self.seed()
        cid = self.ok("GET", "/api/review?limit=1")["cards"][0]["id"]
        results = self.burst([("POST", f"/api/review/{urllib.parse.quote(cid)}", {"rating": 4})] * 8)
        self.assertTrue(all(self.landed(r) for r in results), f"同卡并发评分报错：{results[:2]}")
        row = self.db.one("SELECT * FROM cards WHERE id=?", (cid,))
        logs = self.db.scalar("SELECT COUNT(*) FROM review_logs WHERE card_id=?", (cid,), 0)
        self.assertGreaterEqual(logs, 1, "八次评分一条都没记")
        self.assertLessEqual(logs, 8, "评分记录比请求还多")
        self.assertEqual(row["reps"], logs, "卡片 reps 与复习日志对不上：并发写覆盖没被察觉")
        self.assertEqual(self.db.scalar("PRAGMA integrity_check", (), ""), "ok")

    def test_undo_racing_a_review_does_not_leave_a_phantom_card(self):
        self.seed()
        card = self.ok("GET", "/api/review?limit=1")["cards"][0]
        cid = card["id"]
        rated = self.ok("POST", f"/api/review/{urllib.parse.quote(cid)}", {"rating": 2})
        op = rated["undo"]["op"]
        results = self.burst([("POST", "/api/reviews/undo", {"op": op})] * 3 +
                             [("POST", f"/api/review/{urllib.parse.quote(cid)}", {"rating": 1})] * 2)
        self.assertFalse([r for r in results if not isinstance(r, tuple)],
                         f"撤销并发把请求打挂：{[r for r in results if not isinstance(r, tuple)][:2]}")
        codes = {r[0] for r in results if isinstance(r, tuple)}
        self.assertTrue(codes.issubset({200, 400}), f"撤销并发出现意外状态码：{codes}")
        row = self.db.one("SELECT * FROM cards WHERE id=?", (cid,))
        self.assertIsNotNone(row, "撤销把卡片整个删了")
        self.assertIn(row["state"], ("new", "learning", "review", "relearn"))
        self.assertTrue(row["due_at"])
        if row["reps"] > 0:      # 撤销成功回到"新卡"时，stability 本来就该是 0
            self.assertGreaterEqual(row["stability"], 1e-6, "复习过的卡稳定性归零")
        self.assertEqual(row["reps"],
                         self.db.scalar("SELECT COUNT(*) FROM review_logs WHERE card_id=?", (cid,), 0),
                         "撤销和评分并发之后，卡片计数与日志不一致")
        self.assertEqual(self.db.scalar("PRAGMA integrity_check", (), ""), "ok")


class BrokenConfigTest(ResilienceFixture):
    def test_llm_config_that_is_not_json_falls_back_to_env_defaults(self):
        config.LLM_CONFIG_PATH.write_text("{ this is not json", encoding="utf-8")
        status, out = self.call("GET", "/api/settings/llm")
        self.assertEqual(status, 200, out)
        self.assertFalse(out["data"]["api_key_set"], "坏配置里不该凭空长出密钥")
        self.assertEqual(self.call("GET", "/api/health")[0], 200)

    def test_wrong_typed_config_values_degrade_instead_of_500(self):
        config.LLM_CONFIG_PATH.write_text(json.dumps({
            "base_url": 123, "model": ["gpt"], "api_key": {"nested": True},
            "temperature": "有点热", "timeout": "很久", "max_tokens": "很多",
            "extra_body": "不是对象", "cache_enabled": "yes", "auth_header": "X Key\r\nInjected: 1",
        }), encoding="utf-8")
        try:
            cfg = llm.get_config()        # 这儿抛异常，整个 AI 层就都起不来
            self.assertIsInstance(cfg["temperature"], float)
            self.assertIsInstance(cfg["timeout"], int)
            self.assertIsInstance(cfg["base_url"], str)
            self.assertIsInstance(cfg["api_key"], str)
            self.assertNotIn("\r", cfg["auth_header"] + cfg["api_key"])
            self.assertNotIn("\n", cfg["auth_header"])
            for path in ("/api/settings/llm", "/api/health", "/api/advice", "/api/dashboard"):
                self.assertEqual(self.call("GET", path)[0], 200, f"{path} 被坏配置打挂了")
        finally:
            config.LLM_CONFIG_PATH.unlink(missing_ok=True)
            llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "none"})

    def test_config_that_is_a_json_list_does_not_break_the_settings_page(self):
        config.LLM_CONFIG_PATH.write_text('["not", "a", "mapping"]', encoding="utf-8")
        try:
            status, out = self.call("GET", "/api/settings/llm")
            self.assertEqual(status, 200, out)
        finally:
            llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "none"})

    def test_state_file_garbage_still_lets_the_settings_page_open(self):
        config.LLM_STATE_PATH.write_text("not json at all", encoding="utf-8")
        self.assertEqual(self.call("GET", "/api/settings/llm")[0], 200)
        self.assertIsNone(llm.last_error())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root 下 chmod 挡不住读")
    def test_unreadable_llm_config_does_not_take_the_api_down(self):
        config.LLM_CONFIG_PATH.write_text(json.dumps({"api_key": "sk-should-not-leak"}), encoding="utf-8")
        os.chmod(config.LLM_CONFIG_PATH, 0o000)
        try:
            status, out = self.call("GET", "/api/settings/llm")
            self.assertEqual(status, 200, out)
            self.assertNotIn("sk-should-not-leak", json.dumps(out))
        finally:
            os.chmod(config.LLM_CONFIG_PATH, 0o600)


class BrokenContentTest(ResilienceFixture):
    def test_missing_packs_directory_is_explained_not_a_crash(self):
        self.seed()
        live = config.PACKS_DIR
        try:
            config.PACKS_DIR = self.base_dir / "nope"
            status, out = self.call("GET", "/api/packs")
            self.assertEqual(status, 200, out)      # 目录没了也不该 500；库里已装的包照旧可见
            status, out = self.call("POST", "/api/onboarding",
                                    {"name": "x", "pack_id": "boil.nope.nope", "goal_title": "g"})
            self.assertEqual(status, 400, out)
            self.assertNotIn("internal", json.dumps(out, ensure_ascii=False))
            self.assertTrue(out["error"]["message"], "报错没有可读的 message")
            # 包文件不在，但已装包的单元还能读：不能变成一片白
            unit = self.ok("GET", "/api/map")["nodes"][0]["id"]
            self.assertEqual(self.call("GET", f"/api/units/{unit}")[0], 200)
        finally:
            config.PACKS_DIR = live

    def test_half_broken_pack_file_is_skipped_not_fatal(self):
        live = config.PACKS_DIR
        mixed = self.base_dir / "mixedpacks"
        try:
            (mixed / "bad").mkdir(parents=True)
            (mixed / "bad" / "pack.json").write_text("{oops", encoding="utf-8")
            shutil.copytree(live, mixed / "good", dirs_exist_ok=True)
            config.PACKS_DIR = mixed
            status, out = self.call("GET", "/api/packs")
            self.assertEqual(status, 200, out)
            ids = [p["id"] for p in out["data"]["packs"]]
            self.assertIn("boil.xueshen.learning", ids, "一个坏文件把整个包列表带崩了")
        finally:
            config.PACKS_DIR = live

    def test_importing_garbage_leaves_the_library_untouched(self):
        self.seed()
        before = {t: self.count(t) for t in ("units", "packs", "cards", "notes")}
        for body in ({"pack": {"id": "x"}}, {"text": ""}, {"pack": "{{{"}, {}, {"id": None}):
            status, out = self.call("POST", "/api/packs/import", body)
            self.assertIn(status, (200, 400), f"{body} → {status} {out}")
            if status == 400:
                self.assertNotIn("internal", json.dumps(out, ensure_ascii=False), f"垃圾输入打出 500：{out}")
        after = {t: self.count(t) for t in before}
        self.assertEqual(before, after, "失败的导入改动了库")


class DeadUpstreamTest(ResilienceFixture):
    def test_ai_features_degrade_to_rules_and_say_so(self):
        """上游死了：能规则化的功能继续跑，但必须自报 source=rules，不能冒充模型。"""
        llm.set_config({"api_key": "sk-not-real-123456", "base_url": "http://127.0.0.1:1/v1",
                        "model": "ghost"})
        try:
            status, out = self.call("POST", "/api/packs/suggest", {"text": "一段足够长的学习材料。" * 40})
            self.assertEqual(status, 200, out)
            self.assertEqual(out["data"]["source"], "rules", "上游不可用却报成了模型合成")
            self.assertNotIn("sk-not-real-123456", json.dumps(out, ensure_ascii=False))
            self.assertNotIn("Traceback", json.dumps(out))
            self.assertEqual(self.call("GET", "/api/dashboard")[0], 200)
            diag = self.call("GET", "/api/settings/llm")[1]["data"]
            self.assertNotIn("sk-not-real-123456", json.dumps(diag, ensure_ascii=False))
        finally:
            llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "none"})

    def test_hard_llm_dependency_fails_readably(self):
        """必须要模型的调用（连通性测试）失败时：状态码、message、hint 都要能读，且不泄密钥。"""
        llm.set_config({"api_key": "sk-not-real-123456", "base_url": "http://127.0.0.1:1/v1",
                        "model": "ghost"})
        try:
            status, out = self.call("POST", "/api/settings/llm/test", {})
            self.assertIn(status, (400, 502), out)
            self.assertFalse(out.get("ok"), "上游不可用却回了 ok:true")
            text = json.dumps(out, ensure_ascii=False)
            self.assertTrue(out["error"].get("message"), "错误没有可读的 message")
            self.assertNotIn("sk-not-real-123456", text, "错误里带了完整密钥")
            self.assertNotIn("Traceback", text)
        finally:
            llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "none"})

    def test_llm_test_endpoint_reports_instead_of_hanging(self):
        llm.set_config({"api_key": "", "base_url": "http://127.0.0.1:1/v1", "model": "ghost"})
        start = time.monotonic()
        status, out = self.call("POST", "/api/settings/llm/test", {}, timeout=60)
        self.assertLess(time.monotonic() - start, 40, "测试按钮把请求挂死了")
        self.assertIn(status, (200, 400, 502), out)


class AbortTest(ResilienceFixture):
    def test_hanging_up_mid_stream_does_not_leak_handler_threads(self):
        self.seed()
        unit_id = self.ok("GET", "/api/map")["nodes"][0]["id"]
        session = self.ok("POST", "/api/socratic/start", {"unit_id": unit_id})["session"]["id"]
        baseline = threading.active_count()
        payload = json.dumps({"session_id": session,
                              "message": "学习是模仿加练习，需要输出和复习。"}).encode()
        for _ in range(5):
            conn = http.client.HTTPConnection(*self.origin, timeout=30)
            conn.request("POST", "/api/socratic/stream", body=payload,
                         headers={"Content-Type": "application/json"})
            conn.getresponse().read(1)      # 只读一个字节就跑，模拟用户关掉页面
            conn.close()
        deadline = time.monotonic() + 20
        while threading.active_count() > baseline and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), baseline + 1,
                             f"半途断开的流式请求留下了 {threading.active_count() - baseline} 个线程")
        self.assertEqual(self.call("GET", "/api/health")[0], 200, "掐流之后服务器不干了")

    def test_stalled_body_is_cut_off_instead_of_blocking_forever(self):
        """声明一大坨 body 却一个字都不发：不能让它永久占住一个线程。"""
        self.assertLessEqual(Handler.timeout or 0, 60, "没设连接超时，慢客户端可以耗尽线程")
        live = Handler.timeout
        Handler.timeout = 1
        try:
            conn = http.client.HTTPConnection(*self.origin, timeout=10)
            conn.putrequest("POST", "/api/notes")
            conn.putheader("Content-Length", "5000")
            conn.endheaders()
            start = time.monotonic()
            try:
                status = conn.getresponse().status
            except http.client.HTTPException:
                status = 0          # 服务器直接断链，同样算"没被挂住"
            self.assertLess(time.monotonic() - start, 8, "卡住的 body 拖死了这条连接")
            self.assertIn(status, (0, 400, 408))
            conn.close()
        finally:
            Handler.timeout = live
        self.assertEqual(self.call("GET", "/api/health")[0], 200)


class ResetTest(ResilienceFixture):
    def test_reset_twice_is_idempotent_and_leaves_a_usable_user(self):
        self.seed()
        for _ in range(2):
            status, out = self.call("POST", "/api/reset", {"confirm": "RESET"})
            self.assertEqual(status, 200, out)
        self.assertFalse(self.ok("GET", "/api/dashboard")["has_goal"], "重置之后还以为有目标")
        self.assertEqual(self.count("notes"), 0)
        self.assertEqual(self.count("cards"), 0)
        self.assertEqual(self.count("users"), 1, "重置把用户删了，之后谁都进不来")
        self.assertTrue(self.ok("POST", "/api/onboarding", {
            "name": "重开", "pack_id": "boil.xueshen.learning", "goal_title": "第二次",
            "daily_minutes": 20, "deadline": "2026-12-31"})["has_goal"])

    def test_parallel_resets_do_not_deadlock(self):
        self.seed()
        results = self.burst([("POST", "/api/reset", {"confirm": "RESET"})] * 6)
        self.assertTrue(all(self.landed(r) for r in results), results[:2])
        self.assertEqual(self.count("goals"), 0)
        self.assertEqual(self.call("GET", "/api/dashboard")[0], 200)


if __name__ == "__main__":
    unittest.main()
