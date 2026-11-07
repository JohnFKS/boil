"""方向 6：极速索引导入 + 学习时按需深化的回归测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import compiler, config, embeddings, indexer, llm, services  # noqa: E402
from boil.db import Database  # noqa: E402
from boil.packs import _unit_id, find_pack, install_pack, save_user_pack, validate_pack  # noqa: E402
from boil.server import App, Handler  # noqa: E402

MATERIAL = (
    "## 需求定律\n"
    "在其他条件不变时，商品价格上升，需求量下降；价格下降，需求量上升。\n"
    "需求曲线向右下方倾斜，反映边际效用递减。\n\n"
    "## 供给定律\n"
    "商品价格上升时供给量增加，因为更高的价格能覆盖更高的边际成本。\n"
    "供给曲线通常向右上方倾斜。\n\n"
    "## 市场均衡\n"
    "供给量与需求量相等时的价格称为均衡价格；偏离均衡时价格会通过短缺回到均衡。\n\n"
    "## 弹性\n"
    "需求价格弹性衡量需求量对价格变化的敏感程度，弹性大时涨价会减少总收入。\n"
)

FAKE_DEEPEN = {"units": [{
    "title": "需求定律（模型深化）",
    "essence": "价格与需求量反向变动，背后是边际效用递减。",
    "steps": ["确认其他条件不变", "画出需求曲线", "判断移动与沿曲线移动"],
    "boundaries": ["只适用于其他条件不变"],
    "probes": [{"kind": "recall", "prompt": "需求定律说什么？",
                "answer": "价格上升，需求量下降"}],
    "cards": [{"front": "需求曲线为何向下？", "back": "边际效用递减"}],
}]}


class IndexModeUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-index-")
        base = Path(self.tmp.name)
        self.old = {
            "DATA_DIR": config.DATA_DIR,
            "PACKS_DIR": config.PACKS_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
        }
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        self.old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"

        self.patches = [
            mock.patch.object(embeddings, "embed_texts", return_value=None),
            mock.patch.object(embeddings, "embed",
                              side_effect=lambda t: embeddings.local_embed(t)),
        ]
        for p in self.patches:
            p.start()
        self.db = Database(base / "index.db")
        services.ensure_user(self.db)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.db.close()
        for key, value in self.old.items():
            setattr(config, key, value)
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = self.old_emb
        self.tmp.cleanup()

    def _prepare(self):
        result = services.compile_index_preview(
            self.db, "经济学教材", "微观经济学", MATERIAL)
        pack = result["pack"]
        installed = services.onboarding(self.db, {
            "name": "索引测试", "pack_id": pack["id"], "goal_title": "索引目标",
            "daily_minutes": 40, "deadline": "2027-01-31", "level": "beginner"})
        self.assertIn("install", installed)
        return result, pack

    def test_build_is_legal_pack_with_zero_model_calls(self):
        docs = [{"name": "教材.md", "chars": len(MATERIAL)}]
        with mock.patch.object(llm, "chat_json", side_effect=AssertionError("不应调用模型")):
            pack, index, _ = indexer.build("经济学教材", "微观经济学", MATERIAL, docs)
        self.assertEqual(validate_pack(pack), [])
        self.assertEqual(pack["generated_by"], "index")
        self.assertEqual(pack["source"]["mode"], "index")
        self.assertEqual(pack["source"]["index"]["model_calls"], 0)
        self.assertGreaterEqual(len(pack["units"]), 3)
        self.assertTrue(all(u["source_ref"]["deepen"]["state"] == "pending"
                            for u in pack["units"]))
        self.assertEqual(len(index["chunks"]), len(pack["units"]))
        self.assertEqual(index["dim"], len(index["chunks"][0]["vector"]))
        self.assertEqual(index["engine"], embeddings.LOCAL_MODEL)

    def test_search_prefers_own_section_and_caps_context(self):
        _, index, _ = indexer.build("经济学教材", "微观经济学", MATERIAL, [])
        chunks = indexer.search(index, query="供给", section_id="sec-2")
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["id"], "sec-2")
        self.assertTrue(all("vector" not in c for c in chunks))
        self.assertLessEqual(sum(len(c["text"]) for c in chunks),
                             indexer.MAX_CONTEXT_CHARS)

    def test_oversized_sections_are_split_locally_without_any_model_call(self):
        para = "需求定律说明价格与需求量反向变动，边际效用递减是其基础。" * 12
        long_body = "\n\n".join(para for _ in range(12))
        material = (f"## 需求定律\n{long_body}\n\n"
                    "## 供给定律\n价格上升时供给量增加，因为更高的价格能覆盖边际成本。\n")
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "chat_json",
                               side_effect=AssertionError("索引导入不应调用模型")), \
             mock.patch.object(compiler, "_anchors_from_llm",
                               side_effect=AssertionError("不应问模型要锚点")):
            sections = compiler._expand_sections(material, use_embeddings=False,
                                                use_llm=False)
            pack, index, _ = indexer.build("教材", "微观经济学", material, [])
        self.assertGreaterEqual(len(sections), 3, "超长小节应被本地切成多片")
        self.assertEqual(validate_pack(pack), [])
        self.assertEqual(pack["source"]["index"]["model_calls"], 0)
        self.assertEqual(len(index["chunks"]), len(pack["units"]))
        self.assertLessEqual(max(len(b) for _, b in sections),
                             compiler.SECTION_SPLIT_THRESHOLD + len(para) + 2)

    def test_index_keeps_all_sections_when_units_are_capped(self):
        material = "\n\n".join(
            f"## 小节 {i}\n这是第 {i} 节的正文，说明一个独立概念、推导步骤与适用条件。"
            for i in range(1, indexer.MAX_INDEX_UNITS + 6))
        with mock.patch.object(llm, "chat_json",
                               side_effect=AssertionError("不应调用模型")):
            pack, index, warnings = indexer.build("教材", "经济学", material, [])
        self.assertEqual(len(pack["units"]), indexer.MAX_INDEX_UNITS)
        self.assertEqual(len(index["chunks"]), indexer.MAX_INDEX_UNITS + 5)
        self.assertEqual(len(pack["sections"]), indexer.MAX_INDEX_UNITS + 5)
        self.assertEqual(index["chunks"][-1]["id"], f"sec-{indexer.MAX_INDEX_UNITS + 5}")
        self.assertTrue(any("原文索引仍保留全部" in w for w in warnings))
        self.assertEqual(validate_pack(pack), [])

    def test_index_preview_saves_pack_and_index(self):
        result = services.compile_index_preview(
            self.db, "经济学教材", "微观经济学", MATERIAL)
        pack = result["pack"]
        self.assertEqual(result["coverage"]["applied"], "index")
        self.assertEqual(result["index"]["chunks"], len(pack["units"]))
        self.assertTrue(result["index"]["available"])
        self.assertIsNotNone(find_pack(pack["id"]))
        self.assertIsNotNone(indexer.load_index(pack["id"]))

    def test_index_preview_never_calls_model_even_with_duplicate_units(self):
        material = "\n\n".join(
            f"## 需求定律 {i}\n商品价格上升，需求量下降，需求曲线向右下方倾斜，边际效用递减。"
            for i in range(1, 7))
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "chat_json",
                               side_effect=AssertionError("导入不应调用生成模型")):
            result = services.compile_index_preview(
                self.db, "教材", "经济学", material)
        self.assertEqual(result["quality"]["engine"], "rules")
        self.assertGreaterEqual(len(result["pack"]["units"]), 3)

    def test_install_then_deepen_once_and_cache(self):
        _, pack = self._prepare()
        units = services.get_goal_units(self.db, self._goal_id())
        self.assertEqual(len(units), len(pack["units"]))
        unit_id = units[1]["id"]
        before = units[1]["essence"]

        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "model_name", return_value="mock-model"), \
             mock.patch.object(llm, "chat_json", return_value=FAKE_DEEPEN) as chat:
            first = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
            second = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
        self.assertTrue(first["deepened"])
        self.assertIn("sec-2", first["chunks"])
        self.assertEqual(first["deepen"]["state"], "done")
        self.assertEqual(chat.call_count, 1)                # 第二次不重复烧 token
        self.assertEqual(second["reason"], "already_done")

        after = {u["id"]: u for u in services.get_goal_units(self.db, self._goal_id())}[unit_id]
        self.assertNotEqual(after["essence"], before)
        self.assertIn("模型深化", after["title"])
        self.assertEqual(after["source_ref"]["deepen"]["state"], "done")
        self.assertEqual(len(services.get_goal_units(self.db, self._goal_id())), len(units))
        self.assertEqual(find_pack(pack["id"])["units"][1]["source_ref"]["deepen"]["state"], "done")

    def test_deepen_accepts_bare_unit_object(self):
        _, _ = self._prepare()
        unit_id = services.get_goal_units(self.db, self._goal_id())[0]["id"]
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "model_name", return_value="mock-model"), \
             mock.patch.object(llm, "chat_json", return_value=FAKE_DEEPEN["units"][0]):
            result = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
        self.assertTrue(result["deepened"], result)

    def test_deepen_failure_keeps_skeleton(self):
        _, _ = self._prepare()
        unit_id = services.get_goal_units(self.db, self._goal_id())[0]["id"]
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "model_name", return_value="mock-model"), \
             mock.patch.object(llm, "chat_json", side_effect=RuntimeError("网关 400")):
            result = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
        self.assertFalse(result["deepened"])
        self.assertEqual(result["reason"], "model_failed")
        unit = services.get_goal_units(self.db, self._goal_id())[0]
        self.assertEqual(unit["source_ref"]["deepen"]["state"], "pending")

    def test_index_mode_is_default_and_copy_matches(self):
        source = (ROOT / "static/js/views/onboarding.js").read_text(encoding="utf-8")
        self.assertIn("indexMode: true", source, "极速导入应是默认导入路径")
        self.assertNotIn("下一步会先编译", source, "默认路径下这句会说谎")
        self.assertIn("取消勾选会改为完整 AI 编译", source)

    def test_deepen_reuses_cached_result_after_pack_restore(self):
        """pack 重装 / 状态丢失后重深化同一节，不该再烧一次模型。"""
        _, pack = self._prepare()
        unit_id = services.get_goal_units(self.db, self._goal_id())[1]["id"]
        initial = json.loads(json.dumps(pack))
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "model_name", return_value="mock-model"), \
             mock.patch.object(llm, "chat_json", return_value=FAKE_DEEPEN) as chat:
            first = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
            # 骨架单元回到初始内容：请求与首次完全一致，应命中内容寻址缓存
            save_user_pack(initial)
            second = services.deepen_unit(self.db, config.DEFAULT_USER, unit_id)
        self.assertTrue(first["deepened"], first)
        self.assertTrue(second["deepened"], second)
        self.assertEqual(chat.call_count, 1, "相同请求必须命中 deepen 缓存")

    def test_long_pack_id_installs_without_primary_key_collision(self):
        """pack_id 可达 76 字符：截断曾让同包所有单元塌成同一个主键。"""
        long_name = "微观经济学考点精讲" * 12
        result = services.compile_index_preview(self.db, long_name, long_name, MATERIAL)
        pack = result["pack"]
        out = services.onboarding(self.db, {
            "name": "长标题索引测试", "pack_id": pack["id"],
            "goal_title": "长标题目标", "daily_minutes": 40,
            "deadline": "2027-01-31", "level": "beginner"})
        self.assertIn("install", out)
        units = services.get_goal_units(self.db, self._goal_id())
        self.assertEqual(len(units), len(pack["units"]),
                         "每个骨架单元都必须有自己的主键")

    def test_install_pack_self_heals_legacy_primary_key_collision(self):
        """历史坏数据占用了待生成的主键：不能让整次 onboarding 500。"""
        result = services.compile_index_preview(
            self.db, "经济学教材", "微观经济学", MATERIAL)
        pack = result["pack"]
        goal_id = "g_legacy_1"
        occupied = _unit_id(goal_id, pack["id"], pack["units"][0]["id"])
        self.db.insert("units", {
            "id": occupied, "goal_id": "g_other", "pack_id": "p_other",
            "pack_unit_id": "foreign", "title": "历史残留",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        })
        install_pack(self.db, config.DEFAULT_USER, goal_id, pack)
        rows = self.db.execute(
            "SELECT id FROM units WHERE goal_id=?", (goal_id,)).fetchall()
        self.assertEqual(len(rows), len(pack["units"]), "所有单元都要装上")
        self.assertNotIn(occupied, [r[0] for r in rows],
                         "被占用的主键必须换掉，不能覆盖旧记录")

    def _goal_id(self):
        return self.db.one("SELECT id FROM goals ORDER BY created_at DESC LIMIT 1")["id"]


class IndexModeApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-index-api-")
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
        cls.patches = [
            mock.patch.object(embeddings, "embed_texts", return_value=None),
            mock.patch.object(embeddings, "embed",
                              side_effect=lambda t: embeddings.local_embed(t)),
        ]
        for p in cls.patches:
            p.start()
        cls.db = Database(base / "api.db")
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
        for p in reversed(cls.patches):
            p.stop()
        for key, value in cls.old.items():
            setattr(config, key, value)
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = cls.old_emb
        cls.tmp.cleanup()

    def call(self, method, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_index_import_install_and_deepen_route(self):
        code, res = self.call("POST", "/api/packs/index", {
            "title": "经济学教材", "subject": "微观经济学", "text": MATERIAL})
        self.assertEqual(code, 200, res)
        pack = res["data"]["pack"]
        self.assertEqual(pack["generated_by"], "index")
        self.assertEqual(res["data"]["index"]["model_calls"], 0)
        self.assertGreaterEqual(len(pack["units"]), 3)

        code, _ = self.call("POST", "/api/onboarding", {
            "name": "索引测试", "pack_id": pack["id"], "goal_title": "索引目标",
            "daily_minutes": 40, "deadline": "2027-01-31", "level": "beginner"})
        self.assertEqual(code, 200)
        code, dash = self.call("GET", "/api/dashboard")
        self.assertEqual(code, 200)
        self.assertEqual(dash["data"]["counts"]["units"], len(pack["units"]))

        unit_id = dash["data"]["next_units"][0]["id"]
        encoded = quote(unit_id, safe="")
        with mock.patch.object(llm, "enabled", return_value=True), \
             mock.patch.object(llm, "model_name", return_value="mock-model"), \
             mock.patch.object(llm, "chat_json", return_value=FAKE_DEEPEN) as chat:
            code, deep = self.call("POST", f"/api/units/{encoded}/deepen", {})
            self.assertEqual(code, 200, deep)
            self.assertTrue(deep["data"]["deepened"])
            code, again = self.call("POST", f"/api/units/{encoded}/deepen", {})
            self.assertEqual(again["data"]["reason"], "already_done")
            self.assertEqual(chat.call_count, 1)
        code, detail = self.call("GET", f"/api/units/{encoded}")
        self.assertEqual(code, 200)
        self.assertEqual(detail["data"]["unit"]["source_ref"]["deepen"]["state"], "done")


if __name__ == "__main__":
    unittest.main()
