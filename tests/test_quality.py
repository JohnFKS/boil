from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import compiler, decompose, embeddings, llm, quality


_TMP = None


def setUpModule():
    """整个文件默认拿不到真嵌入配置：质检会算语义分，不隔离就会打真网络。"""
    global _TMP
    _TMP = tempfile.TemporaryDirectory(prefix="boil-qa-")
    base = Path(_TMP.name)
    embeddings._CONFIG_OVERRIDE = base / "embedding.json"
    embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"


def tearDownModule():
    embeddings._CONFIG_OVERRIDE = None
    embeddings._CACHE_OVERRIDE = None
    _TMP.cleanup()


def unit(**kw):
    base = {"id": "u1", "title": "提取练习", "essence": "提取强化记忆",
            "steps": ["合上书复述", "对照找差距"], "boundaries": ["睡眠不足时效果差"],
            "key_points": ["提取"], "probes": [], "cards": []}
    return {**base, **kw}


class LintTest(unittest.TestCase):
    def test_flags_empty_and_duplicate_titles(self):
        pack = {"units": [unit(), unit(id="u2", title="提取练习", essence="")]}
        issues = quality.lint_pack(pack)
        codes = {i["code"] for i in issues}
        self.assertIn("duplicate_title", codes)
        self.assertIn("no_essence", codes)

    def test_flags_numeric_steps_and_missing_parts(self):
        pack = {"units": [unit(steps=["代入 x=3 求得 f(3)=6", "再算一次"], cards=[])]}
        codes = {i["code"] for i in quality.lint_pack(pack)}
        self.assertIn("numeric_steps", codes)
        self.assertIn("no_cards", codes)
        self.assertIn("probe_kinds", codes)

    def test_identical_card_front_back_is_flagged(self):
        pack = {"units": [unit(cards=[{"front": "同一句", "back": "同一句"}])]}
        codes = {i["code"] for i in quality.lint_pack(pack)}
        self.assertIn("card_same", codes)

    def test_clean_pack_has_no_warnings(self):
        pack = compiler.compile_heuristic("干净材料", "学科", (
            "## 提取练习\n提取一次，记忆痕迹就被强化一次。\n- 合书复述\n- 间隔复习\n"
            "注意：睡眠不足时效果差。\n\n## 间隔复习\n在快要忘的时候复习最有效。\n- 到期先清\n"
        ))
        issues = quality.lint_pack(pack)
        self.assertTrue([i for i in issues] == [] or
                        all(i["level"] != "error" for i in issues))
        self.assertEqual([i for i in issues if i["code"] == "probe_kinds"], [],
                         "本地编译器应该补齐四类探针")

    def test_safe_repairs_fill_essence_and_are_idempotent(self):
        pack = {"units": [unit(id="u9", essence="", point_kind="procedure")]}
        issues = quality.lint_pack(pack)
        fixed, applied = quality.apply_safe_repairs(pack, issues)
        self.assertEqual(applied, 1)
        self.assertEqual(fixed["units"][0]["essence"], "提取练习")
        again_issues = quality.lint_pack(fixed)
        _fixed2, applied2 = quality.apply_safe_repairs(fixed, again_issues)
        self.assertEqual(applied2, 0, "没有实际变化时不该再计一次修改（qa-skip）")

    def test_kind_missing_is_repaired_by_rules_not_left_blank(self):
        pack = {"units": [unit(id="u9", title="银行家算法", point_kind="")]}
        issues = quality.lint_pack(pack)
        kind_issue = [i for i in issues if i["code"] == "kind_missing"]
        self.assertEqual(len(kind_issue), 1)
        fixed, applied = quality.apply_safe_repairs(pack, issues)
        self.assertGreaterEqual(applied, 1)
        self.assertIn(fixed["units"][0]["point_kind"], decompose.UNIT_KINDS)

    def test_qa_skip_when_patch_equals_original(self):
        pack = {"units": [unit(essence="提取练习")]}
        issues = [{"code": "no_essence", "level": "warn", "unit_id": "u1",
                   "detail": "x", "patch": {"essence": "提取练习"}}]
        _kept, applied = quality.apply_safe_repairs(pack, issues)
        self.assertEqual(applied, 0)

    def test_near_duplicate_detection_is_deterministic(self):
        pack = {"units": [
            unit(id="a", title="间隔复习", essence="在快要忘记的时候复习最有效"),
            unit(id="b", title="间隔复习法", essence="在快要忘记的时候复习最有效"),
            unit(id="c", title="睡眠巩固", essence="睡眠期间海马体回放白天的经历"),
        ]}
        pairs = quality.find_near_duplicates(pack)
        self.assertEqual(len(pairs), 1)
        self.assertEqual({pairs[0]["keep"], pairs[0]["merge"]}, {"a", "b"})
        self.assertGreater(pairs[0]["similarity"], 0.8)

    def test_rules_engine_when_no_model(self):
        old = llm.enabled
        llm.enabled = lambda: False
        try:
            res = quality.judge_residual({"units": []}, [{"keep": "a", "merge": "b",
                                                          "similarity": 0.9,
                                                          "titles": ["A", "B"]}])
        finally:
            llm.enabled = old
        self.assertEqual(res["engine"], "rules")
        self.assertEqual(res["verdicts"], [])

    def test_qa_pack_reports_engine_label_without_model(self):
        old = llm.enabled
        llm.enabled = lambda: False
        try:
            qa = quality.qa_pack({"units": [unit(essence="")]})
        finally:
            llm.enabled = old
        self.assertIn(qa["engine"], ("rules", "model"))
        self.assertIsInstance(qa["warnings"], list)
        self.assertGreaterEqual(qa["repaired"], 0)

    def test_model_judge_failure_degrades_visibly(self):
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True

        def boom(messages, **kw):
            raise RuntimeError("网关超时")
        llm.chat_json = boom
        try:
            res = quality.judge_residual(
                {"units": [unit(id="a", title="A", essence="本质甲"),
                           unit(id="b", title="B", essence="本质乙")]},
                [{"keep": "a", "merge": "b", "similarity": 0.9, "titles": ["A", "B"]}])
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json
        self.assertEqual(res["engine"], "rules")
        self.assertIn("网关超时", res["note"])

    def test_model_judge_is_asked_about_both_units(self):
        seen = {}
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True

        def fake(messages, **kw):
            seen["prompt"] = messages[1]["content"]
            return {"verdicts": [{"index": 1, "same": True, "reason": "同一概念"}]}
        llm.chat_json = fake
        try:
            res = quality.judge_residual(
                {"units": [unit(id="a", title="间隔复习", essence="在快忘时复习"),
                           unit(id="b", title="间隔复习法", essence="分散复习更好")]},
                [{"keep": "a", "merge": "b", "similarity": 0.9,
                  "titles": ["间隔复习", "间隔复习法"]}])
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json
        self.assertEqual(res["engine"], "model")
        self.assertTrue(res["verdicts"])
        self.assertIn("间隔复习", seen["prompt"])
        self.assertIn("间隔复习法", seen["prompt"])
        self.assertIn("分散复习更好", seen["prompt"], "B 的本质要一起送进去")


class ProbeBackfillTest(unittest.TestCase):
    def test_partial_probes_are_backfilled_to_four_kinds(self):
        data = {
            "name": "包", "description": "d", "subject": "s",
            "units": [{"title": "提取练习", "essence": "提取强化记忆",
                       "steps": ["合书复述"], "key_points": ["提取"],
                       "probes": [{"kind": "recall", "prompt": "核心是什么？",
                                   "answer": "提取强化记忆"}]}],
        }
        pack = compiler._normalize_llm_units(data, "材料", "学科")
        kinds = {p["kind"] for p in pack["units"][0]["probes"]}
        self.assertEqual(kinds, {"recall", "explain", "apply", "boundary"},
                         "模型只给一类探针时，缺的三种要补齐")
        self.assertEqual(quality.lint_pack(pack) and [
            i for i in quality.lint_pack(pack) if i["code"] == "probe_kinds"], [])

    def test_full_probe_set_is_left_alone(self):
        probes = [{"kind": k, "prompt": f"{k} 题", "answer": "参考答案"}
                  for k in compiler.PROBE_TEMPLATES]
        data = {"name": "包", "units": [{"title": "T", "essence": "E",
                                        "probes": probes, "cards": []}]}
        pack = compiler._normalize_llm_units(data, "材料", "学科")
        self.assertEqual(len(pack["units"][0]["probes"]), len(probes))


class AnchorRepairTest(unittest.TestCase):
    def long_body(self, marker: str = "") -> str:
        return marker.join([
            "第一段讲提取练习的原理：提取一次，记忆痕迹就被强化一次。" + "补充说明。" * 250,
            "第二段转到间隔复习：在快要忘但还没忘的时候复习最有效。" + "补充说明。" * 250,
            "第三段讲交错练习：先判断题属于哪一类，再做。" + "补充说明。" * 250,
        ])

    def test_long_body_actually_exceeds_threshold(self):
        self.assertGreater(len(self.long_body()), compiler.SECTION_SPLIT_THRESHOLD)

    def test_anchors_must_be_verbatim(self):
        body = self.long_body()
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"anchors": [
            "第二段转到间隔复习", "模型编造出来的片段不存在于正文", "短"]}
        try:
            anchors = compiler._anchors_from_llm(body)
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json
        self.assertEqual(anchors, ["第二段转到间隔复习"], "不在原文里的锚点必须丢掉")

    def test_oversized_section_is_sliced_by_code_not_rewritten(self):
        body = self.long_body()
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"anchors": ["第二段转到间隔复习",
                                                          "第三段讲交错练习"]}
        try:
            pieces = compiler._split_oversized("无标题长文", body)
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json
        self.assertEqual(len(pieces), 3)
        joined = "\n".join(p[1] for p in pieces)
        for sentence in ("提取一次，记忆痕迹就被强化一次", "在快要忘但还没忘的时候复习最有效",
                         "先判断题属于哪一类"):
            self.assertIn(sentence, joined, "切分不能丢内容")

    def test_short_section_and_missing_model_skip_repair(self):
        old = llm.enabled
        llm.enabled = lambda: False
        try:
            self.assertEqual(compiler._split_oversized("标题", "x" * 5000), [("标题", "x" * 5000)],
                             "没有模型时不切，原样交给语义分组")
        finally:
            llm.enabled = old
        self.assertEqual(compiler._split_oversized("标题", "短正文"), [("标题", "短正文")])

    def test_llm_failure_keeps_original_section(self):
        body = self.long_body()
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True

        def boom(messages, **kw):
            raise RuntimeError("网关炸了")
        llm.chat_json = boom
        try:
            self.assertEqual(compiler._split_oversized("长文", body), [("长文", body)])
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json

    def test_one_usable_anchor_still_splits(self):
        body = self.long_body()
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"anchors": ["第二段转到间隔复习"]}
        try:
            pieces = compiler._split_oversized("长文", body)
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json
        self.assertEqual(len(pieces), 2, "一个有效锚点切出两段也是有用的")
        self.assertTrue(all(len(b) >= 60 for _t, b in pieces))

    def test_unusable_anchors_keep_original_section(self):
        body = self.long_body()
        old_enabled, old_json = llm.enabled, llm.chat_json
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"anchors": ["正文里根本没有的话", "短"]}
        try:
            self.assertEqual(compiler._split_oversized("长文", body), [("长文", body)],
                             "锚点全都对不上时不能乱切")
        finally:
            llm.enabled, llm.chat_json = old_enabled, old_json


class SemanticLadderTest(unittest.TestCase):
    """语义只提候选，裁决走三级阶梯：字面 / 语义 / 模型判定。

    阈值来自实测（BAAI/bge-m3）：真同义改写 0.75–0.81，而「蒸发 / 沸腾」这类
    相关但不同的也能到 0.79，所以中间带必须有模型的话才放行。
    """

    VECTORS = {"沸腾": [1.0, 0.0], "水的沸腾": [0.86, 0.51], "蒸发": [0.0, 1.0]}

    def _units(self):
        return [
            unit(id="boil", title="沸腾", essence="液体内部和表面同时剧烈汽化需要达到沸点"),
            unit(id="water", title="水的沸腾", essence="水在标准大气压下剧烈汽化冒泡的现象"),
            unit(id="evap", title="蒸发", essence="液体表面在任何温度下都能缓慢汽化"),
        ]

    class _FakeEmbed:
        def __init__(self, on: bool):
            self.on = on

        def __enter__(self):
            self.old_enabled = embeddings.enabled
            self.old_texts = embeddings.embed_texts
            embeddings.enabled = lambda: self.on
            table = SemanticLadderTest.VECTORS

            def embed_texts(texts, fresh=False):
                if not self.on:
                    return None
                out = []
                for t in texts:
                    ordered = sorted(table.items(), key=lambda kv: -len(kv[0]))
                    hit = next((v for k, v in ordered if k in t), None)
                    if hit is None:
                        return None
                    out.append(hit)
                return out

            embeddings.embed_texts = embed_texts
            return self

        def __exit__(self, *exc):
            embeddings.enabled = self.old_enabled
            embeddings.embed_texts = self.old_texts

    def test_paraphrase_only_shows_up_with_semantics(self):
        pack = {"units": self._units()}
        with self._FakeEmbed(False):
            off = quality.find_near_duplicates(pack)
        self.assertNotIn({"boil", "water"}, [{x["keep"], x["merge"]} for x in off],
                         "纯字面不该抓到同义改写")
        with self._FakeEmbed(True):
            on = quality.find_near_duplicates(pack)
        pair = [p for p in on if {p["keep"], p["merge"]} == {"boil", "water"}]
        self.assertEqual(len(pair), 1, "语义分应该把改写版揪出来")
        self.assertAlmostEqual(pair[0]["semantic"], 0.86, places=2)
        self.assertEqual(pair[0]["engine"], "semantic")
        self.assertFalse(pair[0]["auto"], "语义分再高也不自动放行：真重复与假对只差几个点")
        self.assertNotIn({"boil", "evap"}, [{p["keep"], p["merge"]} for p in on],
                         "语义 0 的相关概念不能进候选")

    def test_lexical_evidence_alone_still_authorises(self):
        a = unit(id="x", title="间隔复习", essence="在快要忘的时候复习最有效")
        b = unit(id="y", title="间隔复习", essence="在快要忘的时候复习最有效")
        got = quality.merge_decision(a, b, semantic=0.0)
        self.assertTrue(got["allowed"])
        self.assertEqual(got["basis"], "lexical")

    def test_signals_stay_honest_when_embeddings_down(self):
        a, b = self._units()[0], self._units()[1]
        with self._FakeEmbed(False):
            sig = quality.signals(a, b)
        self.assertIsNone(sig["semantic"], "云端不可用时不能假装有语义分")

    def _lex_low(self):
        a, b = self._units()[0], self._units()[1]
        self.assertLess(quality.lexical_score(a, b), quality.MERGE_LEXICAL_FLOOR,
                        "这对样例的前提就是字面分够不上")
        return a, b

    def test_ladder(self):
        a, b = self._lex_low()
        c = self._units()[2]
        cases = [
            (0.94, {"same": True, "reason": "同义"}, True, "model"),   # 语义再高也要签字
            (0.86, None, False, "none"),
            (0.77, {"same": True, "reason": "说的都是水开了"}, True, "model"),
            (0.77, {"same": False, "reason": "一个讲原理一个讲现象"}, False, "model"),
            (0.77, None, False, "none"),
            (0.70, {"same": True, "reason": "×"}, False, "none"),      # 没进候选带就不给合
            (None, None, False, "none"),
        ]
        with self._FakeEmbed(False):   # semantic=None 表示「没有嵌入」，不是「去算一个」
            for sem, verdict, allowed, basis in cases:
                got = quality.merge_decision(a, b, verdict=verdict, semantic=sem)
            self.assertEqual(got["allowed"], allowed, f"{sem}/{verdict} → {got}")
            self.assertEqual(got["basis"], basis, f"{sem}/{verdict} → {got}")
            refused = quality.merge_decision(a, b, verdict=None, semantic=0.77)
            self.assertIn("模型", refused["reason"], "被拦在中间带时要说清楚缺什么")
            degraded = quality.merge_decision(a, b, semantic=None)
            self.assertIn(f"字面 {degraded['signals']['lexical']:.2f}", degraded["reason"])
            self.assertNotIn("语义", degraded["reason"], "没有语义分就别报一个不存在的数")
            strong = quality.merge_decision(a, c, semantic=0.0)
            self.assertFalse(strong["allowed"])


if __name__ == "__main__":
    unittest.main()
