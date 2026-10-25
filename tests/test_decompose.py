"""拆解口径 / 锚点溯源 / 覆盖量化 / 分层聚类的单元检验。

这一层的价值在于**不调用模型也能跑**：口径拦错、锚点算错、覆盖率算错，
都必须能在离线环境里被稳定复现，否则线上出问题就只能靠猜。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import compiler, config, decompose, embeddings, llm, packs, quality

_TMP = None
_SAVED = {}

MATERIAL = (
    "## 进程是资源分配的单位\n"
    "进程是操作系统进行资源分配的基本单位，每个进程拥有独立的地址空间。\n"
    "进程切换需要保存和恢复完整的上下文，因此开销较大。\n\n"
    "## 线程与调度\n"
    "线程是 CPU 调度的基本单位，也称作轻量级进程，同一进程内的线程共享地址空间。\n"
    "线程切换只保存寄存器与栈指针，所以比进程切换便宜得多。\n\n"
    "## 死锁的四个必要条件\n"
    "死锁的产生必须同时满足互斥、请求与保持、不可剥夺和循环等待四个必要条件。\n"
    "破坏其中任意一个条件即可预防死锁，银行家算法则用于避免死锁进入不安全状态。\n"
)


def setUpModule():
    """聚类会读嵌入配置：不隔离就会拿真 API Key 打网络，退化路径的测试也就测不到退化。"""
    global _TMP
    _TMP = tempfile.TemporaryDirectory(prefix="boil-decompose-")
    base = Path(_TMP.name)
    _SAVED.update({"emb": (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE),
                   "config": {k: getattr(config, k) for k in
                              ("LLM_CONFIG_PATH", "LLM_STATE_PATH", "DATA_DIR")}})
    embeddings._CONFIG_OVERRIDE = base / "embedding.json"
    embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
    config.LLM_CONFIG_PATH = base / "llm.json"
    config.LLM_STATE_PATH = base / "llm_state.json"
    config.DATA_DIR = base


def tearDownModule():
    embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = _SAVED["emb"]
    for key, value in _SAVED["config"].items():
        setattr(config, key, value)
    _TMP.cleanup()


def section(sid, title, chars=100, first=""):
    return {"id": sid, "title": title, "chars": chars, "first": first, "body": ""}


def tables():
    """测试材料的真实小节表：与编译器走同一条切分路径，免得断言各自一套。"""
    return compiler._section_tables(compiler._expand_sections(MATERIAL, use_embeddings=False))


class ScheduleRuleTest(unittest.TestCase):
    def test_time_slots_and_review_stages_are_not_knowledge_points(self):
        for title in ("第3天", "第三周", "每日任务", "本周计划", "冲刺日程",
                      "9:00 早读", "综合复习", "查漏补缺", "高频考点", "错题整理"):
            with self.subTest(title=title):
                self.assertTrue(decompose.schedule_like(title))

    def test_material_structure_headings_are_not_mistaken_for_schedule(self):
        # 「第三章」「第2节」是出处，不是安排；砍掉就等于把教材目录当计划删了
        for title in ("第三章", "第2节", "进程管理", "银行家算法", "死锁的四个必要条件"):
            with self.subTest(title=title):
                self.assertIsNone(decompose.schedule_like(title))

    def test_naming_a_learning_strategy_is_a_knowledge_point_not_a_plan(self):
        # 「间隔复习」以"复习"结尾，但它是被研究的策略本体，不能按安排类误杀
        for title in ("间隔复习", "提取练习", "交错练习", "睡眠巩固"):
            with self.subTest(title=title):
                self.assertIsNone(decompose.schedule_like(title))

    def test_empty_title_is_reported(self):
        self.assertTrue(decompose.schedule_like("   "))


class CanonicalTitleTest(unittest.TestCase):
    def test_type_tail_is_stripped_back_to_an_existing_name(self):
        pool = ["P、V操作", "进程同步"]
        got = decompose.canonical_title("P、V操作的实现", pool)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "P、V操作")

    def test_refuses_to_invent_a_name_when_pool_has_no_match(self):
        # 凭空"抽象"出一个更高层标题就是让模型替我们编知识点，那是另一类幻觉
        self.assertIsNone(decompose.canonical_title("完全没见过的概念", ["进程同步"]))

    def test_exact_match_counts_as_canonical(self):
        self.assertEqual(decompose.canonical_title("进程同步", ["进程同步"])[1], "原文")

    def test_short_names_are_not_substring_matched(self):
        # 三个字的名词到处都能撞上，撞上就改写会把不同知识点并成一个
        self.assertIsNone(decompose.canonical_title("进程与线程", ["进程"]))


class KindTest(unittest.TestCase):
    def test_rules_separate_the_five_kinds(self):
        cases = {"怎么判断死锁": "procedure", "为什么会发生抖动": "principle",
                 "进程状态分为五种": "fact", "间隔复习的记忆策略": "meta", "进程": "concept"}
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertEqual(decompose.classify_kind(text)[0], kind)

    def test_unjudgable_text_is_labelled_default_not_forced(self):
        kind, via = decompose.classify_kind("进程")
        self.assertEqual((kind, via), ("concept", "default"))
        self.assertIn(kind, decompose.UNIT_KINDS)


class AnchorTest(unittest.TestCase):
    def setUp(self):
        self.text = "进程是资源分配的单位。\n线程是 CPU 调度的基本单位，也称作轻量级进程。"

    def test_verbatim_anchor_hits(self):
        self.assertTrue(decompose.verify(self.text, "线程是 CPU 调度的基本单位"))

    def test_whitespace_differences_are_tolerated(self):
        # 浏览器和模型都会吞掉换行与空格；逐字核对不能因为排版差异判死
        self.assertTrue(decompose.verify(self.text, "线程是CPU调度的基本单位"))

    def test_paraphrase_is_rejected(self):
        self.assertEqual(decompose.verify(self.text, "线程就是调度的单位"), "")

    def test_anchor_shorter_than_four_chars_is_not_evidence(self):
        self.assertIsNone(decompose.locate(self.text, "线程"))

    def test_fallback_anchor_picks_a_verbatim_sentence(self):
        picked = decompose.fallback_anchor(self.text, "CPU 调度的基本单位是线程")
        self.assertTrue(picked)
        self.assertEqual(decompose.verify(self.text, picked), picked.strip(),
                         "兜底锚点必须逐字来自原文，否则就是伪造证据")

    def test_fallback_anchor_gives_up_when_nothing_matches(self):
        self.assertEqual(decompose.fallback_anchor(self.text, "光合作用与叶绿体"), "")


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.sections = [section("sec-1", "进程", 3000, "进程是资源分配的单位"),
                         section("sec-2", "线程", 200),
                         section("sec-3", "死锁", 800),
                         section("sec-4", "目录", 4)]

    def test_weighted_by_material_chars_not_by_section_count(self):
        units = [{"source_ref": {"id": "sec-4"}}, {"source_ref": {"id": "sec-2"}}]
        cov = decompose.coverage(self.sections, units)
        # 两条单元各引用一节：按个数算是 50% 覆盖，按材料字数算只读到了一点边角料
        self.assertEqual(cov["section_ratio"], 0.5)
        self.assertLess(cov["ratio"], 0.1, "三千字的核心节没被读到，不能被边角小节拉满")
        heavy = decompose.coverage(self.sections, [{"source_ref": {"id": "sec-1"}}])
        self.assertGreater(heavy["ratio"], 0.7, "读完核心节只出了一条单元，覆盖率也该接近满")

    def test_missing_sections_are_listed_by_id_and_title(self):
        cov = decompose.coverage(self.sections, [{"source_ref": {"id": "sec-1"}}])
        self.assertEqual({m["id"] for m in cov["missing"]}, {"sec-2", "sec-3", "sec-4"})

    def test_per_section_counts_units(self):
        units = [{"source_ref": {"id": "sec-3"}}, {"source_ref": {"id": "sec-3"}},
                 {"source_ref": {"id": "sec-1"}}]
        self.assertEqual(decompose.coverage(self.sections, units)["per_section"],
                         {"sec-3": 2, "sec-1": 1})

    def test_no_sections_is_full_mark_not_division_error(self):
        # 没有登记表时是"不知道"，由调用方呈现；这里不能炸，也不能谎报缺口
        cov = decompose.coverage([], [])
        self.assertEqual(cov["sections"], 0)
        self.assertEqual((cov["ratio"], cov["section_ratio"]), (1.0, 1.0))


class GroundingTest(unittest.TestCase):
    def test_only_verified_section_anchors_count(self):
        units = [
            {"source_ref": {"kind": "section", "id": "sec-1", "anchor": "原文一句"}},
            {"source_ref": {"kind": "unverified", "id": "sec-2", "anchor": "编的"}},
            {"source_ref": {"kind": "section", "id": "sec-3", "anchor": ""}},
            {"source_ref": {}},
        ]
        res = decompose.grounding(units)
        self.assertEqual((res["verified"], res["total"], res["rate"]), (1, 4, 0.25))
        self.assertEqual(res["kinds"]["unverified"], 1)

    def test_empty_units_report_zero_rate(self):
        self.assertEqual(decompose.grounding([])["rate"], 0.0)


class ChapterizeTest(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(decompose.chapterize([]), [])

    def test_degrades_to_order_when_embeddings_are_down(self):
        sections = [section(f"sec-{i}", f"第{i}节", 10) for i in range(1, 8)]
        chapters = decompose.chapterize(sections, max_per_chapter=3)
        self.assertTrue(all(c["engine"] == "order" for c in chapters),
                        "没有语义可依据时要如实标 order，不能装作聚得很准")
        flat = [sid for c in chapters for sid in c["section_ids"]]
        self.assertEqual(flat, [s["id"] for s in sections], "退化路径不能丢节")

    def test_semantic_membership_keeps_a_bridge_section_in_both_chapters(self):
        vectors = {"进程管理": [1.0, 0.0], "进程同步": [0.98, 0.02],
                   "内存分配": [0.0, 1.0], "进程换入换出": [0.72, 0.72]}
        sections = [section(f"sec-{i + 1}", t, 50) for i, t in enumerate(vectors)]
        old_enabled, old_embed = embeddings.enabled, embeddings.embed_texts
        embeddings.enabled = lambda: True
        embeddings.embed_texts = lambda texts, **kw: [vectors[t] for t in
                                                     [s["title"] for s in sections]]
        try:
            chapters = decompose.chapterize(sections, threshold=0.6, max_per_chapter=4)
        finally:
            embeddings.enabled, embeddings.embed_texts = old_enabled, old_embed
        self.assertTrue(all(c["engine"] == "semantic" for c in chapters))
        in_both = [sid for sid in [s["id"] for s in sections]
                   if sum(1 for c in chapters if sid in c["section_ids"]) > 1]
        self.assertEqual(in_both, ["sec-4"], "换入换出既属于进程也属于内存，硬切分只能选一个")


class LocalCompileTest(unittest.TestCase):
    """本地编译器路径：出处必须逐字核得过，覆盖与分层要真的落进包里。"""

    def setUp(self):
        self.tables = compiler._section_tables(
            compiler._expand_sections(MATERIAL, use_embeddings=False))
        self.pack = compiler.compile_heuristic("操作系统速记", "计算机", MATERIAL,
                                               use_embeddings=False)

    def test_material_was_split_into_three_sections(self):
        self.assertEqual([t["title"] for t in self.tables],
                         ["进程是资源分配的单位", "线程与调度", "死锁的四个必要条件"])

    def test_every_unit_points_at_a_section_that_exists(self):
        known = {t["id"] for t in self.tables}
        for unit in self.pack["units"]:
            with self.subTest(title=unit["title"]):
                self.assertIn(unit["source_ref"]["id"], known)

    def test_every_anchor_is_verbatim_in_the_section_it_claims(self):
        by_id = {t["id"]: t for t in self.tables}
        for unit in self.pack["units"]:
            ref = unit["source_ref"]
            hit = decompose.verify(by_id[ref["id"]]["body"], ref["anchor"])
            self.assertEqual(hit, ref["anchor"], "写进包里的锚点必须能在原文逐字找到")

    def test_pack_records_sections_and_chapters(self):
        self.assertEqual([s["id"] for s in self.pack["sections"]],
                         [t["id"] for t in self.tables])
        self.assertFalse(any("body" in s for s in self.pack["sections"]),
                         "持久化清单不该重复存正文")
        flat = [sid for c in self.pack["chapters"] for sid in c["section_ids"]]
        self.assertEqual(sorted(flat), sorted(t["id"] for t in self.tables))

    def test_local_pack_scores_full_coverage_and_grounding(self):
        quality_block = packs.pack_quality(self.pack)
        self.assertEqual(quality_block["coverage"]["ratio"], 1.0)
        self.assertEqual(quality_block["grounding"]["rate"], 1.0)
        self.assertTrue(set(quality_block["kinds"]).issubset(set(decompose.UNIT_KINDS)))

    def test_local_units_are_not_flagged_as_schedule_titles(self):
        codes = {i["code"] for i in quality.lint_pack(self.pack)}
        self.assertNotIn("schedule_like_title", codes,
                         "教材小节名不该被口径误杀")
        self.assertNotIn("type_tail_title", codes, "小节名本身没有题型尾巴")

    def test_pack_without_registry_reports_unknown_not_full(self):
        # 第三方包与旧包没有小节清单：覆盖率只能报"不知道"
        for key in ("sections", "chapters"):
            self.pack.pop(key, None)
        self.assertIsNone(packs.pack_quality(self.pack)["coverage"])


class ModelCompileTest(unittest.TestCase):
    """模型路径：模型说什么不算数，代码核过才算。"""

    def setUp(self):
        self._old = (llm.enabled, llm.chat_json)

    def tearDown(self):
        llm.enabled, llm.chat_json = self._old

    def compile_with(self, units, **kw):
        seen = {"prompts": []}

        def fake_json(messages, **_kw):
            seen["prompts"].append(messages[1]["content"])
            return {"name": "操作系统", "description": "d", "subject": "计算机", "units": units}

        llm.enabled = lambda: True
        llm.chat_json = fake_json
        pack, warnings = compiler.compile_material("操作系统速记", "计算机", MATERIAL,
                                                   use_embeddings=False, **kw)
        return pack, warnings, seen

    def test_verbatim_anchor_is_accepted_as_declared(self):
        pack, _w, _s = self.compile_with([{
            "title": "线程与调度", "essence": "线程是调度的基本单位",
            "section": "sec-2", "anchor": "线程是 CPU 调度的基本单位",
            "steps": ["说出定义"], "key_points": ["轻量级进程"]}])
        ref = pack["units"][0]["source_ref"]
        self.assertEqual((ref["kind"], ref["id"], ref["via"]), ("section", "sec-2", "declared"))

    def test_wrong_section_number_is_corrected_by_the_text(self):
        # 模型报错了节号，但这句话只在第二节出现：出处按原文落回 sec-2，不按声明
        pack, _w, _s = self.compile_with([{
            "title": "线程与调度", "essence": "线程是调度的基本单位",
            "section": "sec-1", "anchor": "线程切换只保存寄存器与栈指针",
            "steps": ["说出定义"], "key_points": ["寄存器"]}])
        self.assertEqual(pack["units"][0]["source_ref"]["id"], "sec-2")

    def test_invented_anchor_is_labelled_unverified_not_passed_off_as_evidence(self):
        pack, _w, _s = self.compile_with([{
            "title": "虚拟内存", "essence": "页面置换完全不需要磁盘参与",
            "section": "sec-1", "anchor": "页面置换不需要磁盘参与",
            "steps": ["背结论"], "key_points": ["置换"]}])
        ref = pack["units"][0]["source_ref"]
        self.assertEqual(ref["kind"], "unverified")
        self.assertEqual(ref["anchor"], "")
        codes = {i["code"] for i in quality.lint_pack(pack)}
        self.assertIn("ungrounded", codes)

    def test_rewritten_evidence_falls_back_to_a_verbatim_sentence(self):
        pack, _w, _s = self.compile_with([{
            "title": "死锁的四个必要条件", "essence": "死锁需要互斥、请求与保持、不可剥夺、循环等待",
            "section": "sec-3", "anchor": "四个条件是互斥、请求保持、不可剥夺、循环等待（我概括的）",
            "steps": ["列出条件"], "key_points": ["循环等待"]}])
        ref = pack["units"][0]["source_ref"]
        self.assertEqual(ref["via"], "fallback")
        self.assertEqual(decompose.verify(tables()[2]["body"], ref["anchor"]), ref["anchor"])

    def test_type_tail_model_title_is_linted_back_to_the_material_name(self):
        pack, _w, _s = self.compile_with([{
            "title": "线程与调度的实现", "essence": "线程按时间片被调度",
            "section": "sec-2", "anchor": "线程是 CPU 调度的基本单位",
            "steps": ["说出定义"], "key_points": ["时间片"]}])
        issues = [i for i in quality.lint_pack(pack) if i["code"] == "type_tail_title"]
        self.assertEqual(len(issues), 1, "题型尾巴应该被口径认出来")
        self.assertIn("线程与调度", issues[0]["detail"],
                      "建议的名字只能取自材料里已有的小节名，不能替模型另起一个")

    def test_schedule_model_title_is_linted_as_not_a_knowledge_point(self):
        pack, _w, _s = self.compile_with([{
            "title": "第三周任务", "essence": "按周推进学习",
            "section": "sec-1", "anchor": "进程是操作系统进行资源分配的基本单位",
            "steps": ["排计划"], "key_points": ["周"]}])
        issues = [i for i in quality.lint_pack(pack) if i["code"] == "schedule_like_title"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(pack["units"][0]["source_ref"]["id"], "sec-1",
                         "标题不合格不影响它该有的出处：口径与溯源是两条独立的检查")

    def test_coverage_dial_reaches_the_prompt_and_the_pack(self):
        pack, _w, seen = self.compile_with(
            [{"title": "进程", "essence": "进程是资源分配的单位", "section": "sec-1",
              "anchor": "进程是操作系统进行资源分配的基本单位", "steps": ["说定义"],
              "key_points": ["地址空间"]}], coverage="full")
        self.assertEqual(pack["source"]["coverage_mode"], "full")
        self.assertIn(compiler.coverage_text("full"), seen["prompts"][0])
        self.assertNotIn(compiler.coverage_text("standard"), seen["prompts"][0])

    def test_unknown_coverage_mode_falls_back_to_standard(self):
        pack, _w, _s = self.compile_with(
            [{"title": "进程", "essence": "进程是资源分配的单位", "section": "sec-1",
              "anchor": "进程是操作系统进行资源分配的基本单位", "steps": ["说定义"],
              "key_points": ["地址空间"]}], coverage="everything")
        self.assertEqual(pack["source"]["coverage_mode"], compiler.DEFAULT_COVERAGE)

    def test_section_numbers_are_shown_to_the_model(self):
        _pack, _w, seen = self.compile_with([{
            "title": "进程", "essence": "进程是资源分配的单位", "section": "sec-1",
            "anchor": "进程是操作系统进行资源分配的基本单位", "steps": ["说定义"],
            "key_points": ["地址空间"]}])
        self.assertIn("[sec-2] 线程与调度", seen["prompts"][0],
                      "模型看不到节号就没法报出可对上的出处")


if __name__ == "__main__":
    unittest.main()
