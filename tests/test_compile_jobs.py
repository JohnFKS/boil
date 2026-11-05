"""材料编译后台任务：进度、幂等、取消、失败可见，以及 HTTP 端点联调。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import compile_jobs, compiler, config, embeddings
from boil.db import Database
from boil.server import App, Handler

MATERIAL = (
    "## 需求定律\n"
    "在其他条件不变时，商品价格上升，需求量下降；价格下降，需求量上升。\n"
    "需求曲线通常向右下方倾斜，反映的是边际效用递减。\n\n"
    "## 供给定律\n"
    "在其他条件不变时，商品价格上升，供给量增加，因为更高的价格能覆盖更高的边际成本。\n"
    "供给曲线通常向右上方倾斜。\n\n"
    "## 市场均衡\n"
    "供给量与需求量相等时的价格称为均衡价格；偏离均衡时，价格会通过短缺或过剩回到均衡。\n"
)


class CompileJobsUnitTest(unittest.TestCase):
    def setUp(self):
        with compile_jobs._LOCK:
            compile_jobs._JOBS.clear()

    def tearDown(self):
        with compile_jobs._LOCK:
            compile_jobs._JOBS.clear()

    def _wait(self, job_id: str, timeout: float = 5.0) -> dict:
        until = time.time() + timeout
        while time.time() < until:
            st = compile_jobs.status(job_id)
            if st and st["state"] in ("done", "error", "cancelled"):
                return st
            time.sleep(0.01)
        self.fail("编译任务没有结束")

    def test_progress_and_result(self):
        def runner(on_progress):
            on_progress("batch", 1, 3, "第 1 批")
            on_progress("batch", 2, 3, "第 2 批")
            on_progress("batch", 3, 3, "第 3 批")
            return {"pack": {"id": "user.test.1", "units": [{"title": "需求定律"}]}}

        job = compile_jobs.submit({"text": "progress"}, runner)
        st = self._wait(job["job_id"])
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["phase"], "done")
        self.assertEqual((st["current"], st["total"]), (3, 3))
        self.assertEqual(st["result"]["pack"]["id"], "user.test.1")
        # 结果要能取回第二次：前端刷新后靠这个把编译结果接回来
        self.assertEqual(compile_jobs.status(job["job_id"])["result"]["pack"]["id"],
                         "user.test.1")

    def test_same_material_reuses_running_job(self):
        release = threading.Event()

        def runner(on_progress):
            release.wait(3)
            on_progress("batch", 1, 1, "完成")
            return {"pack": {"id": "x"}}

        first = compile_jobs.submit({"text": "same"}, runner)
        second = compile_jobs.submit({"text": "same"}, runner)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(compile_jobs.active_count(), 1)
        release.set()
        self.assertEqual(self._wait(first["job_id"])["state"], "done")

    def test_cancel_stops_between_batches(self):
        started = threading.Event()

        def runner(on_progress):
            started.set()
            time.sleep(0.05)
            on_progress("batch", 1, 2, "第 1 批")
            time.sleep(0.05)
            on_progress("batch", 2, 2, "第 2 批")
            return {"pack": {"id": "should-not-happen"}}

        job = compile_jobs.submit({"text": "cancel"}, runner)
        self.assertTrue(started.wait(2))
        compile_jobs.cancel(job["job_id"])
        st = self._wait(job["job_id"])
        self.assertEqual(st["state"], "cancelled")
        self.assertIsNone(st.get("result"))

    def test_error_is_reported_not_swallowed(self):
        def runner(on_progress):
            raise ValueError("材料过长（615,618 字符），请按章节分次导入")

        job = compile_jobs.submit({"text": "error"}, runner)
        st = self._wait(job["job_id"])
        self.assertEqual(st["state"], "error")
        self.assertIn("材料过长", st["error"]["message"])

    def test_unknown_job(self):
        self.assertIsNone(compile_jobs.status("missing"))
        self.assertIsNone(compile_jobs.cancel("missing"))


class CompilerBudgetTest(unittest.TestCase):
    def test_skeleton_stays_within_budget_and_keeps_tail_sections(self):
        sections = [{"id": f"sec-{i}", "title": "章节标题" * 12, "first": "首句内容" * 60}
                    for i in range(1, 500)]
        skeleton = compiler._skeleton(sections)
        self.assertLessEqual(len(skeleton), compiler.SKELETON_LIMIT)
        self.assertIn("sec-1]", skeleton)
        ids = [int(line.split("sec-")[1].split("]")[0]) for line in skeleton.splitlines()
               if "sec-" in line]
        self.assertGreater(len(ids), 5)
        self.assertGreater(max(ids), 300)   # 均匀抽样，后半篇编号没有被整段砍掉

    def test_outline_failure_keeps_batched_compile(self):
        text = "".join(f"## 第 {i} 节\n" + "成本随产量变化，边际成本递增。" * 400 + "\n\n"
                       for i in range(1, 5))
        fake = {"units": [{"title": "成本函数", "essence": "成本随产量变化"}]}
        with mock.patch.object(compiler.llm, "enabled", return_value=True), \
             mock.patch.object(compiler, "_outline_pass", side_effect=RuntimeError("HTTP 400")), \
             mock.patch.object(compiler.llm, "chat_json", return_value=fake):
            pack, warnings = compiler.compile_material(
                "成本", "经济学", text, use_llm=True, use_embeddings=False)
        self.assertTrue(pack["units"])
        self.assertTrue(any("全篇大纲生成失败" in w for w in warnings))
        # 关键回归：以前大纲一失败就整份放弃，退化成「只送进前 6000 字」
        self.assertFalse(any("只送进了前" in w or "回退单次抽取" in w for w in warnings))
        self.assertGreater(pack["source"].get("batches", 0), 1)


class CompileJobsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-jobs-")
        base = Path(cls.tmp.name)
        cls.old = {
            "DATA_DIR": config.DATA_DIR,
            "PACKS_DIR": config.PACKS_DIR,
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
        with compile_jobs._LOCK:
            compile_jobs._JOBS.clear()
        cls.db = Database(base / "jobs.db")
        Handler.app = App(cls.db)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
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
        with compile_jobs._LOCK:
            compile_jobs._JOBS.clear()
        cls.tmp.cleanup()

    def call(self, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def wait_done(self, job_id, timeout=15):
        until = time.time() + timeout
        while time.time() < until:
            code, payload = self.call("GET", f"/api/packs/compile/status?id={job_id}")
            self.assertEqual(code, 200)
            st = payload["data"]
            if st["state"] in ("done", "error", "cancelled"):
                return st
            time.sleep(0.03)
        self.fail("异步编译没有结束")

    def test_async_compile_returns_result_and_keeps_sync_endpoint(self):
        body = {"title": "微观经济学", "subject": "经济学", "text": MATERIAL,
                "use_llm": False, "use_embeddings": False}
        code, started = self.call("POST", "/api/packs/compile", {**body, "async": True})
        self.assertEqual(code, 200)
        job_id = started["data"]["job_id"]
        self.assertTrue(job_id)
        self.assertNotIn("pack", started["data"])
        st = self.wait_done(job_id)
        self.assertEqual(st["state"], "done", st.get("error"))
        pack = st["result"]["pack"]
        self.assertTrue(pack["units"])
        self.assertEqual(st["result"]["coverage"]["applied"], "")
        # 同步端点行为不变，旧客户端/脚本仍然可用
        code, sync = self.call("POST", "/api/packs/compile", body)
        self.assertEqual(code, 200)
        self.assertTrue(sync["data"]["pack"]["units"])

    def test_async_validates_before_queueing(self):
        code, payload = self.call("POST", "/api/packs/compile", {
            "title": "短", "text": "太短", "use_llm": False, "async": True})
        self.assertEqual(code, 400)
        self.assertIn("至少提供 60 个字符", payload["error"]["message"])
        code, payload = self.call("POST", "/api/packs/compile", {
            "title": "坏输入", "text": "x" * 80, "documents": "not-a-list", "async": True})
        self.assertEqual(code, 400)

    def test_status_and_cancel_unknown_job(self):
        code, payload = self.call("GET", "/api/packs/compile/status?id=nope")
        self.assertEqual(code, 404)
        self.assertEqual(payload["error"]["code"], "job_not_found")
        code, _ = self.call("POST", "/api/packs/compile/cancel", {"job_id": "nope"})
        self.assertEqual(code, 404)

    def test_oversized_material_fails_fast_with_actionable_message(self):
        huge = "## 第 1 章\n" + "经济学材料。" * 60000
        code, payload = self.call("POST", "/api/packs/compile", {
            "title": "整本教材", "text": huge, "use_llm": False, "async": True})
        self.assertEqual(code, 400)
        self.assertIn("分", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
