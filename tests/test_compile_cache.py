"""⑥ 内容寻址增量编译 + 批级 checkpoint 的回归断言。

只测「同一身份指纹」下的复用行为：单元测试直接调 compile_material 时不传
checkpoint，行为应与改造前完全一致，这里显式传入才启用缓存。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boil import compile_cache, compiler, config, services

SUBJECT = "微观经济学"
TITLE = "增量编译材料"


def material(marker: str = "原") -> str:
    return "\n\n".join(
        f"## 第 {i} 节\n{marker} 这一节讲第 {i} 个知识点，正文要足够长才能撑开批次预算。"
        f"{'补充说明。' * 30}"
        for i in range(1, 6))


class CompileCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-ccache-")
        base = Path(self.tmp.name)
        self.old = {name: getattr(config, name) for name in
                    ("DATA_DIR", "PACKS_DIR", "LLM_CONFIG_PATH", "LLM_STATE_PATH")}
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        compile_cache.clear()

        self.old_budget = (compiler.SINGLE_CALL_LIMIT, compiler.BATCH_CHAR_BUDGET)
        compiler.SINGLE_CALL_LIMIT = 300
        compiler.BATCH_CHAR_BUDGET = 300
        self.old_concurrency = config.COMPILE_CONCURRENCY
        config.COMPILE_CONCURRENCY = 1        # 让假模型的调用数与顺序可判定

        self.calls: list[str] = []
        self.fail_next_batch = False
        enabled = mock.patch.object(compiler.llm, "enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        chat = mock.patch.object(compiler.llm, "chat_json", side_effect=self._fake_json)
        chat.start()
        self.addCleanup(chat.stop)

    def tearDown(self):
        compiler.SINGLE_CALL_LIMIT, compiler.BATCH_CHAR_BUDGET = self.old_budget
        config.COMPILE_CONCURRENCY = self.old_concurrency
        compile_cache.clear()
        for name, value in self.old.items():
            setattr(config, name, value)
        self.tmp.cleanup()

    def _fake_json(self, messages, **kw):
        prompt = messages[1]["content"]
        self.calls.append(prompt)
        if "全篇骨架" in prompt:
            return {"name": TITLE, "description": "d", "subject": SUBJECT,
                    "outline": [], "study_plan": [],
                    "digest": {"overview": "总览", "advice": []}}
        if self.fail_next_batch:
            self.fail_next_batch = False
            raise RuntimeError("这一批网关炸了")
        n = len(self.calls)
        return {"units": [{"title": f"单元{n}", "essence": "核心结论",
                           "steps": ["步骤"], "key_points": ["要点"],
                           "probes": [], "cards": []}]}

    def _compile(self, text: str, fp: str):
        return compiler.compile_material(TITLE, SUBJECT, text, use_llm=True,
                                         use_embeddings=False, checkpoint=fp)

    def test_fingerprint_is_stable_and_sensitive(self):
        a = compile_cache.material_fingerprint({"title": "x", "coverage": "standard"})
        b = compile_cache.material_fingerprint({"coverage": "standard", "title": "x"})
        self.assertEqual(a, b, "键顺序不该影响指纹")
        self.assertNotEqual(a, compile_cache.material_fingerprint(
            {"title": "x", "coverage": "full"}))

    def test_second_run_reuses_every_batch_and_outline(self):
        fp = compile_cache.material_fingerprint({"title": TITLE, "text_shape": "v1"})
        first, warnings = self._compile(material(), fp)
        self.assertEqual(warnings, [])
        first_calls = len(self.calls)
        self.assertGreater(first_calls, 2, "这条用例必须真的走了分批编译")

        state = compile_cache.load(fp)
        self.assertIsNotNone(state, "批结果必须落盘")
        self.assertIsNotNone(state["outline"], "大纲结果也要复用")
        self.assertEqual(len(state["batches"]), first_calls - 1)

        second, _ = self._compile(material(), fp)
        self.assertEqual(len(self.calls), first_calls, "重复导入不该再调模型")
        self.assertEqual(len(second["units"]), len(first["units"]))
        self.assertEqual([u["title"] for u in second["units"]],
                         [u["title"] for u in first["units"]], "复用后单元顺序要一致")

    def test_one_word_change_only_recompiles_affected_batch(self):
        fp = compile_cache.material_fingerprint({"title": TITLE, "text_shape": "v1"})
        self._compile(material(), fp)
        before = len(self.calls)

        # 只动第 3 节里的一个片段：其余批次和大纲形状都保持不变
        changed = material().replace("第 3 个知识点", "第 3 个知识点（改）", 1)
        self.assertNotEqual(changed, material())
        self.calls.clear()
        _, warnings = self._compile(changed, fp)
        self.assertEqual(warnings, [])
        self.assertEqual(len(self.calls), 1,
                         "只改正文时，大纲与未受影响的批都应命中 checkpoint")

    def test_corrupted_checkpoint_is_ignored(self):
        fp = compile_cache.material_fingerprint({"title": TITLE})
        path = compile_cache._path(fp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(compile_cache.load(fp))
        compile_cache._path(fp).write_text('{"v": 999}', encoding="utf-8")
        self.assertIsNone(compile_cache.load(fp), "版本不符不能当成命中")

    def test_result_reuse_requires_same_content(self):
        fp = compile_cache.material_fingerprint({"title": TITLE})
        digest = compile_cache.digest({"text": "A"})[:32]
        compile_cache.mark_complete(fp, {"pack": {"id": "p1"}}, digest)
        self.assertEqual(compile_cache.result_of(fp, digest)["pack"]["id"], "p1")
        self.assertIsNone(compile_cache.result_of(fp, "other"),
                          "同名不同内容的材料不能拿到旧包")

    def test_failed_batch_does_not_freeze_incomplete_result(self):
        qa = {"warnings": [], "issues": [], "repaired": [],
              "near_duplicates": [], "engine": "rules"}
        text = material()
        with mock.patch.object(services.quality, "qa_pack", return_value=qa):
            self.fail_next_batch = True
            res = services.compile_preview(None, TITLE, SUBJECT, text,
                                           use_embeddings=False)
        self.assertGreater(int((res["pack"]["source"] or {}).get("incomplete_batches") or 0), 0)
        fps = compile_cache.entries()
        self.assertEqual(len(fps), 1)
        state = compile_cache.load(fps[0])
        self.assertFalse(state["complete"], "残缺结果不能被标记成整轮完成")
        self.assertGreater(len(state["batches"]), 0, "成功的批仍要留在 checkpoint 里")
        self.assertIsNone(compile_cache.result_of(
            fps[0], compile_cache.digest({"text": text})[:32]))

    def test_compile_preview_hits_whole_result_cache(self):
        qa = {"warnings": [], "issues": [], "repaired": [],
              "near_duplicates": [], "engine": "rules"}
        with mock.patch.object(services.quality, "qa_pack", return_value=qa):
            first = services.compile_preview(None, TITLE, SUBJECT, material(),
                                             use_embeddings=False)
            before = len(self.calls)
            self.assertGreater(before, 2)
            second = services.compile_preview(None, TITLE, SUBJECT, material(),
                                              use_embeddings=False)
        self.assertEqual(len(self.calls), before, "整轮命中时连切分/大纲/质检都不该再调模型")
        self.assertEqual(second["pack"]["id"], first["pack"]["id"])
