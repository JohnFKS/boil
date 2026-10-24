from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import (backup, compiler, config, embeddings, feynman, llm, mastery,
                    planner, quality, services, socratic)
from boil.compiler import compile_heuristic, compile_material
from boil.db import Database, SCHEMA_VERSION
from boil.packs import (all_available_packs, install_pack, validate_pack)
from boil.srs.fsrs import Scheduler, interval_label
from boil.utils import now_iso

UTC = timezone.utc
PACK_ID = "boil.xueshen.learning"


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-test-")
        base = Path(self.tmp.name)
        self._old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
        self._old = {
            "PACKS_DIR": config.PACKS_DIR,
            "DATA_DIR": config.DATA_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
        }
        config.PACKS_DIR = base / "packs"
        config.DATA_DIR = base
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        self.db = Database(base / "test.db")
        self.user = services.ensure_user(self.db, "测试者")

    def tearDown(self):
        self.db.close()
        for key, value in self._old.items():
            setattr(config, key, value)
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = self._old_emb
        self.tmp.cleanup()

    def get_pack(self):
        return next(p for p in all_available_packs() if p["id"] == PACK_ID)

    def onboard(self, **overrides):
        payload = {
            "name": "测试者", "pack_id": PACK_ID, "goal_title": "学习力",
            "daily_minutes": 40, "deadline": "2026-12-31",
        }
        payload.update(overrides)
        return services.onboarding(self.db, payload)


class SchedulerTest(unittest.TestCase):
    def test_new_card_ratings(self):
        s = Scheduler()
        now = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        results = {r: s.schedule({"state": "new"}, r, now) for r in (1, 2, 3, 4)}
        self.assertEqual(results[1].state, "learning")
        self.assertEqual(results[4].state, "review")
        self.assertLess(results[1].interval_seconds, results[2].interval_seconds)
        self.assertLess(results[2].interval_seconds, results[3].interval_seconds)
        self.assertLess(results[3].interval_seconds, results[4].interval_seconds)
        self.assertGreater(results[4].interval_days, 1)

    def test_review_intervals_and_lapse(self):
        s = Scheduler()
        now = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        card = {
            "state": "review", "step": 0, "stability": 12.0, "difficulty": 5.0,
            "last_review": (now - timedelta(days=12)).isoformat(),
            "reps": 4, "lapses": 0,
        }
        good = s.schedule(card, 3, now)
        easy = s.schedule(card, 4, now)
        again = s.schedule(card, 1, now)
        self.assertEqual(good.state, "review")
        self.assertGreater(good.interval_days, 1)
        self.assertGreaterEqual(easy.interval_days, good.interval_days)
        self.assertEqual(again.state, "relearning")
        self.assertEqual(again.lapses, 1)
        self.assertLess(again.interval_seconds, 3600)

    def test_preview_and_retention(self):
        s = Scheduler()
        now = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        preview = s.preview_labels({"state": "new"}, now)
        self.assertEqual(set(preview), {"1", "2", "3", "4"})
        self.assertIn("分钟", preview["1"])
        r0 = s.retrievability(10.0, 0)
        r10 = s.retrievability(10.0, 30)
        self.assertGreater(r0, r10)
        self.assertTrue(0 <= r10 <= 1)


class PlannerTest(unittest.TestCase):
    def units(self, n=6):
        return [{
            "id": f"u{i}", "title": f"单元 {i}", "estimated_minutes": 40,
            "importance": 5 if i < 2 else 3, "order_index": i,
        } for i in range(n)]

    def relations(self, n=6):
        return [{"from_unit": f"u{i}", "to_unit": f"u{i+1}", "kind": "prerequisite"}
                for i in range(n - 1)]

    def test_topological_and_milestones(self):
        plan = planner.build_plan(self.units(), self.relations(), daily_minutes=60,
                                  start=datetime(2026, 10, 21).date(),
                                  deadline=datetime(2026, 12, 31).date(),
                                  goal_id="g1")
        self.assertEqual(plan["strategy"]["mode"], "full")
        ids = [x["id"] for d in plan["days"] for x in d["new_units"]]
        self.assertEqual(ids, [f"u{i}" for i in range(6)])
        self.assertTrue(plan["milestones"])
        self.assertGreater(len(plan["days"]), 0)

    def test_minimum_viable_parks_units(self):
        plan = planner.build_plan(self.units(10), self.relations(10), daily_minutes=20,
                                  start=datetime(2026, 10, 21).date(),
                                  deadline=datetime(2026, 10, 24).date(),
                                  goal_id="g2")
        self.assertEqual(plan["strategy"]["mode"], "minimum_viable")
        self.assertGreater(len(plan["strategy"]["parked"]), 0)
        self.assertLess(len(plan["strategy"]["parked"]), 10)

    def test_invalid_deadline_falls_back(self):
        plan = planner.build_plan(self.units(2), self.relations(2), daily_minutes=40,
                                  start=datetime(2026, 10, 21).date())
        self.assertTrue(plan["days"])

    def test_outline_reorders_without_relations(self):
        order = ["单元 3", "单元 1", "单元 0", "单元 2", "单元 4", "单元 5"]
        outline = [{"title": "先抓重点", "summary": "从 3 开始", "unit_titles": order}]
        plan = planner.build_plan(self.units(), [], daily_minutes=60,
                                  start=datetime(2026, 10, 21).date(),
                                  deadline=datetime(2026, 12, 31).date(),
                                  goal_id="g3", ai_outline=outline)
        ids = [x["id"] for d in plan["days"] for x in d["new_units"]]
        self.assertEqual(ids, ["u3", "u1", "u0", "u2", "u4", "u5"])
        self.assertEqual(plan["outline"], outline)

    def test_outline_cannot_break_prerequisites(self):
        outline = [{"title": "倒序尝试", "unit_titles": [f"单元 {i}" for i in range(5, -1, -1)]}]
        plan = planner.build_plan(self.units(), self.relations(), daily_minutes=60,
                                  start=datetime(2026, 10, 21).date(),
                                  deadline=datetime(2026, 12, 31).date(),
                                  goal_id="g4", ai_outline=outline)
        ids = [x["id"] for d in plan["days"] for x in d["new_units"]]
        self.assertEqual(ids, [f"u{i}" for i in range(6)])

    def test_outline_matching_tolerates_wording_drift(self):
        outline = [{"title": "近似标题", "unit_titles": ["单元 1 的扩展", "单元 0"]}]
        plan = planner.build_plan(self.units(3), [], daily_minutes=60,
                                  start=datetime(2026, 10, 21).date(),
                                  goal_id="g5", ai_outline=outline)
        ids = [x["id"] for d in plan["days"] for x in d["new_units"]]
        self.assertEqual(ids[:2], ["u1", "u0"])


class CompilerTest(TempCase):
    def test_heuristic_compile(self):
        text = """# 极限的定义
极限描述的是趋近过程，而不是到达。要判断极限存在，需要先固定 ε，再找 δ。
- 先理解任意 ε 的含义
- 再找依赖 ε 的 δ
注意：δ 依赖 ε，不能把它当成固定常数。
很多人以为极限就是代入函数值，这是错误的。

## 连续与可导
连续要求函数值、极限都存在且相等。
- 先检查定义域
- 再检查左右极限
可导比连续更强；连续不一定可导。
例如 y=|x| 在 0 点连续但不可导。
"""
        pack = compile_heuristic("极限笔记", "高等数学", text)
        self.assertEqual(validate_pack(pack), [])
        self.assertGreaterEqual(len(pack["units"]), 2)
        for unit in pack["units"]:
            self.assertTrue(unit["essence"])
            self.assertTrue(unit["probes"])
            self.assertTrue(unit["cards"])
        self.assertTrue(any("连续" in u["title"] for u in pack["units"]))

    def test_semantic_group_without_headings(self):
        text = (
            "极限是趋近过程，不是到达。连续要求极限存在且等于函数值。\n\n"
            "导数描述函数的变化率，可导必连续。\n\n"
            "矩阵的秩是线性无关列的最大数量。\n\n"
            "行列式为零则矩阵不可逆。"
        )
        pack = compile_heuristic("无标题材料", "数学", text, use_embeddings=True)
        self.assertGreaterEqual(len(pack["units"]), 2)
        self.assertEqual(validate_pack(pack), [])

    def test_compile_material_without_llm(self):
        pack, warnings = compile_material("测试", "数学", "## 概念\n这是核心概念。\n- 步骤一\n注意边界。", use_llm=False)
        self.assertTrue(validate_pack(pack) == [])
        self.assertIsInstance(warnings, list)

    def test_heuristic_emits_outline_plan_and_digest(self):
        text = """# 极限的定义
极限描述的是趋近过程，而不是到达。
- 先理解任意 ε 的含义

## 连续与可导
连续要求函数值、极限都存在且相等。
- 先检查定义域

## 中值定理
闭区间连续、开区间可导则存在导数等于平均变化率的点。
"""
        pack = compile_heuristic("高数笔记", "高等数学", text)
        self.assertEqual(validate_pack(pack), [])
        self.assertEqual(pack["generated_by"], "rules")
        self.assertTrue(pack["outline"])
        self.assertTrue(pack["study_plan"])
        self.assertTrue(pack["digest"]["overview"])
        self.assertTrue(pack["digest"]["advice"])
        titles = {u["title"] for u in pack["units"]}
        covered = {t for entry in pack["outline"] for t in entry["unit_titles"]}
        self.assertTrue(titles <= covered, "大纲必须覆盖全部单元")
        for phase in pack["study_plan"]:
            self.assertGreater(phase["minutes"], 0)

    def test_normalize_plan_fields_matches_drifting_titles(self):
        units = [{"title": "记忆的形成", "essence": "学习形成可提取记忆"},
                 {"title": "知识迁移", "essence": "变式练习"}]
        data = {"outline": [{"title": "第一部分", "summary": "先立地基",
                             "unit_titles": ["记忆的形成与提取", "不存在的东西"]}],
                "study_plan": [{"phase": "地基", "unit_titles": ["记忆的形成"],
                                "focus": "理解本质", "minutes": "120"}],
                "digest": {"overview": "两个单元。", "advice": ["先复述", "再复习"]}}
        fields = compiler._normalize_plan_fields(data, units)
        self.assertEqual(fields["outline"][0]["unit_titles"], ["记忆的形成"])
        self.assertEqual(fields["outline"][1]["title"], "知识迁移")
        self.assertEqual(fields["study_plan"][0]["minutes"], 120)
        self.assertEqual(len(fields["digest"]["advice"]), 2)


class MigrationTest(TempCase):
    def test_legacy_database_gains_angle_column(self):
        cols = lambda: {r["name"] for r in self.db.query("PRAGMA table_info(units)")}
        self.assertIn("angle", cols())
        self.db.execute("ALTER TABLE units DROP COLUMN angle")
        self.assertNotIn("angle", cols())
        self.db.init_schema()
        self.assertIn("angle", cols())

    def test_init_schema_is_idempotent(self):
        for _ in range(3):
            self.db.init_schema()
        version = self.db.one("SELECT value FROM meta WHERE key='schema_version'")
        self.assertEqual(version["value"], str(SCHEMA_VERSION))
        self.assertIn("angle", {r["name"] for r in self.db.query("PRAGMA table_info(units)")})


class AiPathTest(TempCase):
    """走 model 分支：这些断言能抓出“异常被回退吞掉”的 bug。"""

    def setUp(self):
        super().setUp()
        self._llm_old = (llm.enabled, llm.chat_json, llm.chat, llm.load_prompt)

    def tearDown(self):
        llm.enabled, llm.chat_json, llm.chat, llm.load_prompt = self._llm_old
        super().tearDown()

    def test_suggest_meta_model_path(self):
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {
            "title": "  记忆与复习策略  ", "subject": "学习科学",
            "goal_title": "掌握复习节奏", "description": "讲提取练习与间隔安排"}
        out = services.suggest_meta("## 记忆\n提取练习强化记忆，间隔复习对抗遗忘，交错练习促进迁移。" * 3)
        self.assertEqual(out["source"], "model")
        self.assertEqual(out["title"], "记忆与复习策略")
        self.assertEqual(out["subject"], "学习科学")

    def test_suggest_meta_falls_back_when_model_fails(self):
        llm.enabled = lambda: True

        def boom(messages, **kw):
            raise RuntimeError("网关挂了")
        llm.chat_json = boom
        out = services.suggest_meta("## 极限\n极限描述趋近过程而不是到达，需要先固定 epsilon 再找 delta，不能当成固定常数。" * 2)
        self.assertEqual(out["source"], "rules")
        self.assertTrue(out["title"])

    def test_insights_synthesize_model_path(self):
        self.onboard()
        llm.enabled = lambda: True
        seen = {}

        def fake_json(messages, **kw):
            seen["prompt"] = messages[1]["content"]
            return {"summary": "五个单元都还没积累证据。",
                    "suggestions": ["先做第一次提取练习", "把最弱单元讲一遍", "明天复习"],
                    "risks": ["长时间不动手会维持在新卡状态"]}
        llm.chat_json = fake_json
        out = services.insights_synthesize(self.db, "local")
        self.assertEqual(out["source"], "model")
        self.assertEqual(out["warnings"], [])
        self.assertTrue(out["summary"].startswith("五个单元"))
        self.assertEqual(len(out["suggestions"]), 3)
        self.assertIn("材料总览", seen["prompt"])
        self.assertIn("单元总数", seen["prompt"])

    def test_compile_material_model_path_carries_plan_fields(self):
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {
            "name": "模型包", "description": "两个单元", "subject": "学习科学",
            "outline": [{"title": "第一部分", "summary": "先立地基",
                         "unit_titles": ["提取练习"]}],
            "study_plan": [{"phase": "地基", "unit_titles": ["提取练习"],
                            "focus": "理解本质", "minutes": 90}],
            "digest": {"overview": "这份材料讲提取与复习。", "advice": ["先复述"]},
            "units": [{"title": "提取练习", "angle": "先合书复述再核对",
                       "essence": "提取强化记忆", "steps": ["合上书回忆"],
                       "key_points": ["提取"], "probes": [], "cards": []}]}
        pack, warnings = services.compile_material("材料", "学习科学", "提取练习强化记忆。" * 20,
                                                   use_llm=True, use_embeddings=False)
        self.assertEqual(warnings, [])
        self.assertEqual(pack["generated_by"], "model")
        self.assertEqual(pack["units"][0]["angle"], "先合书复述再核对")
        self.assertEqual(pack["digest"]["overview"], "这份材料讲提取与复习。")
        self.assertEqual([e["title"] for e in pack["outline"]], ["第一部分"])
        errors = validate_pack(pack)
        self.assertEqual(errors, [])


    def test_mnemonic_model_path_and_cleaning(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: True
        seen = {}

        def fake(messages, **kw):
            seen["prompt"] = messages[1]["content"]
            return {"mnemonics": ["  提取=合上书讲出来 ", "太短", "类比：肌肉记忆", "x" * 90],
                    "note": "用了定义式与类比两种手法"}
        llm.chat_json = fake
        res = services.generate_mnemonic(self.db, "local", unit_id)
        self.assertEqual(res["source"], "model")
        self.assertEqual(res["mnemonics"][0], "提取=合上书讲出来")
        self.assertNotIn("太短", res["mnemonics"], "少于 4 字的钩子要丢掉")
        self.assertTrue(all(len(m) <= 60 for m in res["mnemonics"]), "超长钩子要截断")
        self.assertLessEqual(len(res["mnemonics"]), 3)
        self.assertEqual(res["unit_id"], unit_id)
        self.assertIn("学习的本质是记忆", seen["prompt"], "材料必须由服务端读取单元得到")
        self.assertIn("提高刺激强度", seen["prompt"], "单元的 essence 要进提示词")

    def test_mnemonic_refuses_without_model(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: False
        with self.assertRaises(PermissionError):
            services.generate_mnemonic(self.db, "local", unit_id)

    def test_mnemonic_rejects_empty_output(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"mnemonics": ["短", ""]}
        with self.assertRaises(ValueError):
            services.generate_mnemonic(self.db, "local", unit_id)

    def test_mnemonic_never_trusts_client_text(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: True
        captured = {}
        llm.chat_json = lambda messages, **kw: captured.update(
            {"n": len(messages), "prompt": messages[1]["content"]}) or {
            "mnemonics": ["真实提示词只含服务端材料"], "note": ""}
        services.generate_mnemonic(self.db, "local", unit_id)
        self.assertNotIn("前端", captured["prompt"][:40])
        self.assertIn(out["next_units"][0]["title"], captured["prompt"])


    def test_cards_from_text_preview_then_confirm(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        preview = services.cards_from_text(
            self.db, "local", "提取练习：合上书，用自己的话把核心讲出来。\n间隔复习？在快要忘但还没忘的时候复习最有效。",
            unit_id)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["engine"], "rules")
        self.assertTrue(preview["cards"])
        self.assertEqual(preview["unit_id"], unit_id)
        self.assertTrue(preview["tool_args"]["cards"])
        for card in preview["cards"]:
            self.assertTrue(len(card["front"]) >= 4 and len(card["back"]) >= 2)

        # 预览不能写库：确认之前，复习队列数量不变
        before = services.review_queue(self.db, "local")["new_total"]
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id='local'", (), 0),
                         before)
        done = services.confirm_cards(self.db, "local", preview["cards"], unit_id)
        self.assertEqual(done["count"], len(preview["cards"]))
        self.assertIn("复习卡", done["summary"])
        after = self.db.query("SELECT * FROM cards WHERE user_id='local' AND unit_id=? AND probe_id IS NULL",
                              (unit_id,))
        self.assertEqual(len(after), done["count"])
        self.assertTrue(all(c["state"] == "new" for c in after))

    def test_cards_rules_cloze_picks_later_term(self):
        cards = services._rules_cards("海马体负责把短期痕迹整合成长期记忆，这个过程发生在睡眠期间。")
        self.assertTrue(cards)
        self.assertIn("（　）", cards[0]["front"])
        self.assertNotEqual(cards[0]["front"], cards[0]["back"])

    def test_cards_validation_paths(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        with self.assertRaises(ValueError):
            services.cards_from_text(self.db, "local", "太短", unit_id)
        with self.assertRaises(ValueError):
            services.confirm_cards(self.db, "local", [{"front": "短", "back": "答"}], unit_id)
        services.reset_user(self.db, "local")
        with self.assertRaises(ValueError):
            services.confirm_cards(self.db, "local", [{"front": "一个足够长的问题？", "back": "答案"}], None)

    def test_cards_from_text_model_path_labels_engine(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: True
        llm.chat_json = lambda messages, **kw: {"cards": [
            {"front": "提取练习的关键动作是什么？", "back": "合上材料，用自己的话复述核心。"},
            {"front": "重复", "back": "x"},
        ]}
        res = services.cards_from_text(self.db, "local", "提取练习就是合书复述，比反复阅读有效。", unit_id)
        self.assertEqual(res["engine"], "model")
        self.assertEqual(len(res["cards"]), 1, "过短或不完整的卡要丢掉")

    def test_cards_model_failure_falls_back_to_rules(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        llm.enabled = lambda: True

        def boom(messages, **kw):
            raise RuntimeError("网关炸了")
        llm.chat_json = boom
        res = services.cards_from_text(self.db, "local", "间隔复习？在快要忘的时候复习最有效。", unit_id)
        self.assertEqual(res["engine"], "rules")
        self.assertIn("网关炸了", res["engine_note"])
        self.assertTrue(res["cards"])

    def test_quick_ask_requires_model_and_question(self):
        llm.enabled = lambda: False
        with self.assertRaises(PermissionError):
            services.ask_about_selection(self.db, "local", "为什么？", "上下文")
        llm.enabled = lambda: True
        with self.assertRaises(ValueError):
            services.ask_about_selection(self.db, "local", "   ", "上下文")
        llm.chat = lambda messages, **kw: "因为提取会强化记忆痕迹。\n下一步：合上书讲一遍。"
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        res = services.ask_about_selection(self.db, "local", "为什么要合上书？",
                                           "合书复述属于提取练习。", unit_id)
        self.assertEqual(res["source"], "model")
        self.assertIn("强化记忆痕迹", res["answer"])
        self.assertEqual(res["unit_id"], unit_id)


    def test_long_material_is_compiled_in_batches_covering_everything(self):
        seen = {"prompts": [], "calls": 0}

        def fake_json(messages, **kw):
            prompt = messages[1]["content"]
            seen["prompts"].append(prompt)
            seen["calls"] += 1
            if "全篇骨架" in prompt:
                return {"name": "长材料包", "description": "覆盖全部小节", "subject": "学习科学",
                        "outline": [{"title": "第一部分", "summary": "先立地基",
                                     "section_titles": ["第 1 节"]}],
                        "study_plan": [{"phase": "地基", "section_titles": ["第 1 节"],
                                        "focus": "理解本质", "minutes": 200}],
                        "digest": {"overview": "这份长材料分成多节讲提取与复习。",
                                   "advice": ["先跑完第一批"]}}
            marker = prompt.count("本批进度")
            which = prompt.split("第 ")[-1].split(" /")[0].strip() or "1"
            return {"units": [{
                "title": f"批次{which}的单元",
                "angle": "先复述再验证边界",
                "essence": f"第 {which} 批的核心结论。",
                "steps": ["读小标题", "合书复述"],
                "boundaries": ["本批材料未覆盖时不外推"],
                "key_points": ["核心结论"],
                "probes": [], "cards": [],
            }]}

        llm.enabled = lambda: True
        llm.chat_json = fake_json
        old_limit = compiler.SINGLE_CALL_LIMIT
        old_batch = compiler.BATCH_CHAR_BUDGET
        compiler.SINGLE_CALL_LIMIT = 1000   # 让测试材料能越过单次阈值
        compiler.BATCH_CHAR_BUDGET = 700    # 让测试材料真的被拆成多批
        try:
            text = "\n\n".join(f"## 第 {i} 节\n这一节讲提取练习的第 {i} 个侧面，需要正文足够长。"
                               f"{'补充说明。' * 60}" for i in range(1, 9))
            expected_batches = len(compiler._batch_sections(
                compiler._split_sections(text, use_embeddings=False)))
            pack, warnings = compile_material("长材料", "学习科学", text, use_llm=True,
                                              use_embeddings=False)
        finally:
            compiler.SINGLE_CALL_LIMIT, compiler.BATCH_CHAR_BUDGET = old_limit, old_batch
        self.assertEqual(warnings, [])
        self.assertGreaterEqual(seen["calls"], 4, "应该是 1 次大纲 + 至少 3 次批次")
        self.assertEqual(len(pack["units"]), seen["calls"] - 1)
        self.assertEqual(pack["source"]["batches"], expected_batches)
        self.assertEqual(pack["source"]["material_chars"], len(text))
        self.assertEqual(pack["generated_by"], "model")
        self.assertTrue(pack["outline"] and pack["digest"]["overview"])
        # 每一批都必须真的被送进模型，不能只喂开头
        joined = "".join(seen["prompts"])
        for i in range(1, 9):
            self.assertIn(f"第 {i} 节", joined, f"第 {i} 节没有进入任何一次调用")
        self.assertEqual(validate_pack(pack), [])

    def test_batch_failure_skips_only_that_batch(self):
        calls = {"n": 0}

        def fake_json(messages, **kw):
            prompt = messages[1]["content"]
            calls["n"] += 1
            if "全篇骨架" in prompt:
                return {"name": "包", "description": "d", "subject": "s",
                        "outline": [], "study_plan": [], "digest": {"overview": "总览", "advice": []}}
            if "第 2 /" in prompt:
                raise RuntimeError("这一批网关炸了")
            return {"units": [{"title": f"单元 {calls['n']}", "essence": "本质内容",
                               "steps": ["步骤"], "key_points": ["要点"],
                               "probes": [], "cards": []}]}

        llm.enabled = lambda: True
        llm.chat_json = fake_json
        old_limit = compiler.SINGLE_CALL_LIMIT
        old_batch = compiler.BATCH_CHAR_BUDGET
        compiler.SINGLE_CALL_LIMIT = 1000
        compiler.BATCH_CHAR_BUDGET = 500
        try:
            text = "\n\n".join(f"## 第 {i} 节\n内容足够长以撑开批次。{'补充。' * 80}" for i in range(1, 6))
            pack, warnings = compile_material("材料", "学科", text, use_llm=True, use_embeddings=False)
        finally:
            compiler.SINGLE_CALL_LIMIT, compiler.BATCH_CHAR_BUDGET = old_limit, old_batch
        self.assertTrue(any("批编译失败，已跳过" in w for w in warnings), warnings)
        self.assertTrue(pack["units"], "单批失败不该让整个编译空手而归")

    def test_oversized_section_is_never_silently_cut(self):
        """小节超过批次预算必须无损切开：模型没读到的字，就没资格说「基于全部材料」。"""
        seen = {"prompts": []}

        def fake_json(messages, **kw):
            prompt = messages[1]["content"]
            seen["prompts"].append(prompt)
            if "全篇骨架" in prompt:
                return {"name": "包", "description": "d", "subject": "s", "outline": [],
                        "study_plan": [], "digest": {"overview": "总览", "advice": []}}
            return {"units": [{"title": f"批次单元 {len(seen['prompts'])}", "essence": "核心结论",
                               "steps": ["步骤"], "key_points": ["要点"],
                               "probes": [], "cards": []}]}

        markers = [f"K{i:03d} 这一段讲提取练习的一个侧面，长度足够撑开预算所以要写满。"
                   for i in range(1, 25)]
        text = "# 长文\n\n" + "\n\n".join(markers)
        llm.enabled = lambda: True
        llm.chat_json = fake_json
        old_limit = compiler.SINGLE_CALL_LIMIT
        old_batch = compiler.BATCH_CHAR_BUDGET
        compiler.SINGLE_CALL_LIMIT = 400      # 越过阈值，走分批编译
        compiler.BATCH_CHAR_BUDGET = 300      # 一小节装不下，必须无损切开
        try:
            pack, warnings = compile_material("超长单节", "学科", text, use_llm=True,
                                              use_embeddings=False)
        finally:
            compiler.SINGLE_CALL_LIMIT, compiler.BATCH_CHAR_BUDGET = old_limit, old_batch
        self.assertTrue(any("全篇骨架" in p for p in seen["prompts"]),
                        "根本没走分批编译，这条用例就白了")
        joined = "".join(seen["prompts"])
        for marker in markers:
            self.assertIn(marker, joined, f"{marker[:6]} 没有进入任何一次调用")
        self.assertEqual(warnings, [], f"不该有任何丢掉内容的告警: {warnings}")
        self.assertGreaterEqual(len(pack["units"]), 3, "切开的每一批都该有自己的单元")

    def test_chunk_section_packs_without_losing_text(self):
        body = "\n\n".join(f"第 {i} 段：合上书用自己的话讲出本质与边界。" for i in range(1, 40))
        pieces = compiler._chunk_section("长节", body, 120)
        self.assertTrue(all(len(text) <= 120 for _, text in pieces),
                        [len(t) for _, t in pieces])
        self.assertEqual("".join(t for _, t in pieces).count("讲出本质与边界"), 39, "切开不能丢段")
        self.assertLess(len(pieces), 15, "小块要装回预算内，不能一段一片")
        self.assertEqual(pieces[0][0], "长节")
        self.assertTrue(pieces[-1][0].startswith("长节（续"))

    def test_bare_unit_list_response_is_accepted(self):
        """模型偶尔不按包对象回答，而是直接回一个单元数组——不该因此就退回本地编译。"""
        def fake_json(messages, **kw):
            return [{"title": f"单元 {i}", "essence": "本质说明", "steps": ["步骤"],
                     "boundaries": ["边界"], "key_points": ["要点"],
                     "probes": [], "cards": []} for i in range(1, 4)]

        llm.enabled = lambda: True
        llm.chat_json = fake_json
        pack, warnings = compile_material("数组回答", "学科", "## 一节\n正文说明足够长。" * 4,
                                          use_llm=True, use_embeddings=False)
        self.assertEqual(len(pack["units"]), 3)
        self.assertEqual(pack["generated_by"], "model", "被误判成本地编译会谎报来源")

    def test_unit_cap_is_announced_not_silent(self):
        def fake_json(messages, **kw):
            if "全篇骨架" in messages[1]["content"]:
                return {"units": []}
            return {"units": [{"title": f"单元 {i}", "essence": "本质说明",
                               "steps": ["步骤"], "boundaries": ["边界"],
                               "key_points": ["要点"], "probes": [], "cards": []}
                              for i in range(1, 14)]}

        llm.enabled = lambda: True
        llm.chat_json = fake_json
        pack, warnings = compile_material("材料", "学科", "## 一节\n正文说明足够长。" * 4,
                                          use_llm=True, use_embeddings=False)
        self.assertEqual(len(pack["units"]), compiler.MAX_UNITS_SINGLE)
        self.assertTrue(any("只留下前" in w for w in warnings),
                        f"超限单元被静默吞掉了: {warnings}")

    def test_non_lexical_merge_needs_human_confirmation(self):
        """非字面证据的合并必须有人点头。

        真实模拟里抓到过：候选列表写「不可合并」，端点自己再问一次模型改判成
        same=true，于是真的把两个不同的单元合了库——模型两次能给出相反答案。
        """
        out = self.onboard()
        goal_id = out["goal"]["id"]
        for uid, title, essence in (("unit-cm-a", "工作记忆", "同时在线的元素数量有限"),
                                    ("unit-cm-b", "认知负荷", "学习设计要压低外在负担")):
            self.db.upsert("units", {"id": uid, "goal_id": goal_id, "pack_id": "p",
                                     "pack_unit_id": uid, "title": title, "essence": essence,
                                     "order_index": 70, "created_at": now_iso(),
                                     "updated_at": now_iso()}, "id")
        fake = {"allowed": True, "basis": "model", "reason": "模型判定同一件事",
                "signals": {"lexical": 0.2, "semantic": 0.79}}
        old = quality.merge_decision
        quality.merge_decision = lambda *a, **k: dict(fake)
        try:
            with self.assertRaises(ValueError) as cm:
                services.merge_units(self.db, "local", "unit-cm-a", "unit-cm-b")
            self.assertIn("确认", str(cm.exception))
            self.assertIsNone(self.db.one(
                "SELECT merged_into FROM units WHERE id='unit-cm-a'")["merged_into"],
                "没确认就不能改动数据库")
            res = services.merge_units(self.db, "local", "unit-cm-a", "unit-cm-b",
                                        confirmed=True)
            self.assertEqual(res["basis"], "model")
            self.assertEqual(self.db.one(
                "SELECT merged_into FROM units WHERE id='unit-cm-a'")["merged_into"], "unit-cm-b")
        finally:
            quality.merge_decision = old

    def test_merge_override_requires_a_written_reason(self):
        """分数与模型都不同意时，人想合可以，但必须署名：一次点击不能冒充裁决。"""
        out = self.onboard()
        goal_id = out["goal"]["id"]
        db = self.db
        rows = [
            {"id": "unit-ov-a", "goal_id": goal_id, "pack_id": "p", "pack_unit_id": "a",
             "title": "线性方程组求解", "essence": "消元法把未知数一个个消掉直到可回代",
             "order_index": 80, "created_at": now_iso(), "updated_at": now_iso()},
            {"id": "unit-ov-b", "goal_id": goal_id, "pack_id": "p", "pack_unit_id": "b",
             "title": "牛顿第一定律", "essence": "物体不受外力时保持匀速直线运动状态",
             "order_index": 81, "created_at": now_iso(), "updated_at": now_iso()},
        ]
        for row in rows:
            db.upsert("units", row, "id")
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-ov-a", "unit-ov-b")
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-ov-a", "unit-ov-b",
                                 reason="看着像", override=True)
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-ov-a", "unit-ov-b", override=True)
        res = services.merge_units(db, "local", "unit-ov-a", "unit-ov-b",
                                   reason="我的材料里这就是同一个概念的两种译名", override=True)
        self.assertEqual(res["basis"], "user")
        stored = db.one("SELECT merged_reason FROM units WHERE id='unit-ov-a'")["merged_reason"]
        self.assertIn("学习者确认", stored)
        self.assertIn("同一个概念", stored, "用户写的理由要能被后来的人看见")

    def _merge_units(self, goal_id, specs):
        for uid, title, order in specs:
            self.db.upsert("units", {"id": uid, "goal_id": goal_id, "pack_id": "p",
                                     "pack_unit_id": uid, "title": title,
                                     "essence": "同一件事的两种说法",
                                     "order_index": order, "created_at": now_iso(),
                                     "updated_at": now_iso()}, "id")

    def test_merge_can_be_undone_and_restores_edges(self):
        """合并会重写关系边，所以撤销必须按记录把边也还回去，不能只把指针抹掉。"""
        out = self.onboard()
        goal_id = out["goal"]["id"]
        self._merge_units(goal_id, [("u-a", "间隔复习", 60), ("u-b", "间隔复习法", 61),
                                    ("u-c", "睡眠巩固", 62)])
        self.db.upsert("relations", {"id": "rel-1", "goal_id": goal_id, "from_unit": "u-b",
                                     "to_unit": "u-c", "kind": "prerequisite"}, "id")
        res = services.merge_units(self.db, "local", "u-b", "u-a")
        self.assertEqual(res["basis"], "lexical")
        rel = self.db.one("SELECT from_unit, to_unit FROM relations WHERE id='rel-1'")
        self.assertEqual((rel["from_unit"], rel["to_unit"]), ("u-a", "u-c"), "合并应把边改指归宿")
        detail = services.unit_detail(self.db, "local", "u-b")
        self.assertTrue(detail["can_unmerge"], detail["unmerge_blocked_reason"])

        undone = services.unmerge_unit(self.db, "local", "u-b")
        self.assertFalse(undone["degraded"])
        self.assertGreaterEqual(undone["relations_restored"], 1)
        row = self.db.one("SELECT merged_into, merged_reason, merged_op FROM units WHERE id='u-b'")
        self.assertIsNone(row["merged_into"])
        self.assertEqual(row["merged_reason"], "")
        self.assertIsNone(row["merged_op"])
        rel = self.db.one("SELECT from_unit, to_unit FROM relations WHERE id='rel-1'")
        self.assertEqual((rel["from_unit"], rel["to_unit"]), ("u-b", "u-c"), "边要回到原来指向")
        self.assertIn("u-b", [n["id"] for n in services.map_data(self.db, "local", goal_id)["nodes"]],
                      "撤销后应该重新出现在地图上")
        with self.assertRaises(ValueError):
            services.unmerge_unit(self.db, "local", "u-b")

    def test_unmerge_refuses_when_the_target_moved(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        self._merge_units(goal_id, [("u-a2", "间距复习", 63), ("u-b2", "间距复习法", 64),
                                    ("u-c2", "间距复习", 65)])
        services.merge_units(self.db, "local", "u-b2", "u-a2")
        services.merge_units(self.db, "local", "u-a2", "u-c2")
        with self.assertRaises(ValueError) as cm:
            services.unmerge_unit(self.db, "local", "u-b2")
        self.assertIn("归宿", str(cm.exception))
        self.assertIsNotNone(self.db.one(
            "SELECT merged_into FROM units WHERE id='u-b2'")["merged_into"])

    def test_unmerge_refuses_when_it_absorbed_others(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        self._merge_units(goal_id, [("u-a3", "复述练习", 66), ("u-b3", "复述练习法", 67),
                                    ("u-c3", "复述练习", 68)])
        services.merge_units(self.db, "local", "u-c3", "u-b3")
        services.merge_units(self.db, "local", "u-b3", "u-a3")
        detail = services.unit_detail(self.db, "local", "u-b3")
        self.assertFalse(detail["can_unmerge"])
        self.assertIn("收了", detail["unmerge_blocked_reason"])
        with self.assertRaises(ValueError) as cm:
            services.unmerge_unit(self.db, "local", "u-b3")
        self.assertIn("先撤销那几次", str(cm.exception))

    def test_compile_heuristic_covers_all_sections(self):
        text = "\n\n".join(f"## 第 {i} 节\n这是第 {i} 节的正文说明。" for i in range(1, 11))
        pack = compile_heuristic("十节材料", "学科", text, use_embeddings=False)
        self.assertGreaterEqual(len(pack["units"]), 8, "本地路径也应尽量覆盖多节，而不是只取开头")


    def test_merge_is_code_enforced_and_never_deletes(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        db = self.db
        a = {"id": "unit-a", "goal_id": goal_id, "pack_id": "p", "pack_unit_id": "a",
             "title": "间隔复习", "essence": "在快要忘但还没忘的时候复习最有效",
             "order_index": 0, "created_at": now_iso(), "updated_at": now_iso()}
        b = {"id": "unit-b", "goal_id": goal_id, "pack_id": "p", "pack_unit_id": "b",
             "title": "间隔复习法", "essence": "在快要忘但还没忘的时候复习最有效",
             "order_index": 1, "created_at": now_iso(), "updated_at": now_iso()}
        c = {"id": "unit-c", "goal_id": goal_id, "pack_id": "p", "pack_unit_id": "c",
             "title": "线性方程组求解", "essence": "消元法把未知数一个个消掉直到可回代",
             "order_index": 2, "created_at": now_iso(), "updated_at": now_iso()}
        for row in (a, b, c):
            db.upsert("units", row, "id")
        db.upsert("relations", {"id": "r1", "goal_id": goal_id, "from_unit": "unit-b",
                                "to_unit": "unit-c", "kind": "prerequisite"}, "id")

        # 阈值由代码判定：不像的一对直接被拒
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-a", "unit-c")

        res = services.merge_units(db, "local", "unit-b", "unit-a", reason="同一件事的两种说法")
        self.assertEqual(res["into"], "unit-a")
        self.assertGreaterEqual(res["similarity"], quality.MERGE_LEXICAL_FLOOR)
        self.assertEqual(res["basis"], "lexical", "没配嵌入时必须按字面放行并说清楚依据")
        self.assertEqual(res["signals"]["semantic"], None, "云端向量不可用时不能假装有语义分")
        self.assertEqual(db.one("SELECT merged_reason FROM units WHERE id='unit-b'")["merged_reason"],
                         "同一件事的两种说法", "用户写了理由就要用用户的")
        self.assertEqual(db.one("SELECT id FROM units WHERE id='unit-b'")["id"], "unit-b",
                         "合并不能删除内容")
        self.assertEqual(db.one("SELECT merged_into FROM units WHERE id='unit-b'")["merged_into"], "unit-a")

        # 前置关系重连到归宿，且不留自环
        rel = db.one("SELECT from_unit, to_unit FROM relations WHERE id='r1'")
        self.assertEqual(rel["from_unit"], "unit-a")
        self.assertEqual(rel["to_unit"], "unit-c")

        # 地图与「下一步」不再出现被吸收的单元
        m = services.map_data(db, "local", goal_id)
        self.assertNotIn("unit-b", [n["id"] for n in m["nodes"]])
        self.assertIn("unit-a", [n["id"] for n in m["nodes"]])
        nxt = services._next_units(services.get_goal_units(db, goal_id),
                                   services._unit_status_map(db, "local",
                                                             services.get_goal_units(db, goal_id)))
        self.assertNotIn("unit-b", [u["id"] for u in nxt])

        detail = services.unit_detail(db, "local", "unit-b")
        self.assertEqual(detail["merged_into"]["id"], "unit-a")
        absorbed = services.unit_detail(db, "local", "unit-a")["absorbed"]
        self.assertEqual([x["id"] for x in absorbed], ["unit-b"])
        self.assertEqual(absorbed[0]["merged_reason"], "同一件事的两种说法")

    def test_merge_chains_to_the_final_target(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        db = self.db
        rows = [("unit-x", "间隔复习", "在快要忘的时候复习最有效", 0),
                ("unit-y", "间隔复习法", "在快要忘的时候复习最有效", 1),
                ("unit-z", "间隔复习策略", "在快要忘的时候复习最有效", 2)]
        for uid, title, essence, order in rows:
            db.upsert("units", {"id": uid, "goal_id": goal_id, "pack_id": "p",
                                "pack_unit_id": uid, "title": title, "essence": essence,
                                "order_index": order, "created_at": now_iso(),
                                "updated_at": now_iso()}, "id")
        services.merge_units(db, "local", "unit-y", "unit-x")
        services.merge_units(db, "local", "unit-z", "unit-y")
        self.assertEqual(db.one("SELECT merged_into FROM units WHERE id='unit-z'")["merged_into"],
                         "unit-x", "链要指到最终归宿，不要停在已被吸收的单元")
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-x", "unit-z")   # 不接受成环
        self.assertEqual(db.one("SELECT merged_into FROM units WHERE id='unit-x'")["merged_into"], None)

    def test_merge_validation_edges(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        db = self.db
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", unit_id, unit_id)
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", unit_id, "")
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", unit_id, "no-such-unit")
        db.upsert("units", {"id": "unit-other-goal", "goal_id": "different-goal",
                            "pack_id": "p", "pack_unit_id": "z", "title": "标题一样",
                            "essence": "本质也一样", "order_index": 9,
                            "created_at": now_iso(), "updated_at": now_iso()}, "id")
        with self.assertRaises(ValueError):
            services.merge_units(db, "local", "unit-other-goal", unit_id)


class ServiceTest(TempCase):
    def test_onboarding_dashboard_map_unit_review(self):
        out = self.onboard()
        self.assertTrue(out["has_goal"])
        self.assertEqual(out["counts"]["units"], 8)
        self.assertEqual(out["counts"]["due"], 0)
        self.assertTrue(out["plan"]["today"])
        goal_id = out["goal"]["id"]

        dash = services.dashboard(self.db, "local", goal_id)
        self.assertEqual(dash["counts"]["new"], 8)
        self.assertTrue(dash["next_units"])

        m = services.map_data(self.db, "local", goal_id)
        self.assertEqual(len(m["nodes"]), 8)
        self.assertTrue(m["edges"])
        node = m["nodes"][0]
        self.assertIn("facets", node)

        detail = services.unit_detail(self.db, "local", node["id"])
        self.assertTrue(detail["unit"]["essence"])
        self.assertTrue(detail["probes"])
        self.assertTrue(detail["cards"])

        queue = services.review_queue(self.db, "local")
        self.assertEqual(queue["due_total"], 0)
        self.assertEqual(queue["new_total"], 16)
        self.assertTrue(queue["cards"])
        card = queue["cards"][0]
        rated = services.rate_card(self.db, "local", card["id"], 3, 5000)
        self.assertIn(rated["result"]["state"], ("learning", "review"))
        self.assertGreater(rated["result"]["interval_days"], 0)
        after = services.review_queue(self.db, "local")
        self.assertLess(after["new_total"], 16)

    def test_memory_health_tracks_learned_cards(self):
        out = self.onboard()
        fresh = services.insights(self.db, "local", out["goal"]["id"])
        self.assertIsNone(fresh["memory_health"], "没有已学卡片时不该编造数值")
        self.assertEqual(fresh["learned_cards"], 0)

        queue = services.review_queue(self.db, "local")
        for card in queue["cards"][:4]:
            services.rate_card(self.db, "local", card["id"], 3, 3000)
        after = services.insights(self.db, "local", out["goal"]["id"])
        self.assertEqual(after["learned_cards"], 4)
        self.assertIsNotNone(after["memory_health"])
        self.assertGreater(after["memory_health"], 0.0)
        self.assertLessEqual(after["memory_health"], 1.0)
        # 刚复习完应该接近 1，明显高于放很久不管的情况
        self.assertGreater(after["memory_health"], 0.8)

    def test_recall_before_reveal_is_persisted(self):
        out = self.onboard()
        queue = services.review_queue(self.db, "local")
        card = queue["cards"][0]
        services.rate_card(self.db, "local", card["id"], 3, 4000,
                           recall="提取练习就是合上书用自己的话讲出来。")
        rows = self.db.query("SELECT * FROM attempts WHERE user_id='local'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "review_recall")
        self.assertEqual(rows[0]["response"], "提取练习就是合上书用自己的话讲出来。")
        self.assertEqual(rows[0]["unit_id"], card["unit_id"])
        self.assertEqual(json.loads(rows[0]["feedback_json"])["source"], "review")
        self.assertAlmostEqual(rows[0]["score"], 0.8, places=3)
        detail = services.unit_detail(self.db, "local", card["unit_id"])
        self.assertTrue(any(a["kind"] == "review_recall" for a in detail["attempts"]))

        # 复习时写的回忆不是「作答探测题」：卡片带着 probe_id 也不能把探测题的计数抬上去
        probe_id = self.db.scalar("SELECT probe_id FROM cards WHERE id=?", (card["id"],))
        self.assertIsNotNone(probe_id, "这条用例前提是编译产物把卡挂到了探测题上")
        self.assertIsNone(rows[0]["probe_id"], "复习回忆不能占着探测题的 probe_id")
        self.assertEqual(json.loads(rows[0]["feedback_json"])["probe_id"], probe_id,
                         "来源探测题要留在 feedback 里，不能计进作答次数")
        probe = [p for p in detail["probes"] if p["id"] == probe_id]
        self.assertEqual(probe[0]["attempts"], 0, "探测题「N 次作答」被复习污染了")
        self.assertEqual(len(detail["recalls"]), 1, "单元页要能看到这条回忆")
        self.assertEqual(detail["recalls"][0]["response"], "提取练习就是合上书用自己的话讲出来。")
        self.assertAlmostEqual(detail["recalls"][0]["score"], 0.8, places=3)

        second = services.review_queue(self.db, "local")
        other = [c for c in second["cards"] if c["id"] != card["id"]][0]
        services.rate_card(self.db, "local", other["id"], 4, 2000, recall="   ")
        services.rate_card(self.db, "local", other["id"], 1, 2000)
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM attempts WHERE user_id='local'", (), 0), 1,
            "空白回忆不该写库")

    def test_stream_retry_does_not_duplicate_user_turn(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", out["goal"]["id"], unit_id)
        sid = s["session"]["id"]
        turn_before = socratic.get_session(self.db, sid)["session"]["turn"]

        def drain(**kw):
            return list(socratic.stream_reply(self.db, "local", sid, "学习是形成可提取的记忆。", **kw))

        drain()
        after_first = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(after_first["turn"], turn_before + 1)
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user'", (sid,), 0), 1)

        # 断线重连：同一句话带 retry 再来一次，不该多出一条用户消息、也不该涨轮次
        drain(retry=True)
        after_retry = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(after_retry["turn"], after_first["turn"])
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user'", (sid,), 0), 1)

        # 换个说法（不带 retry）就应该正常+1
        list(socratic.stream_reply(self.db, "local", sid, "还需要及时复习来强化回路。"))
        third = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(third["turn"], after_retry["turn"] + 1)
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND role='user'", (sid,), 0), 2)

    def test_socratic_and_feynman(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", out["goal"]["id"], unit_id)
        sid = s["session"]["id"]
        events = list(socratic.stream_reply(self.db, "local", sid,
                                            "学习是模仿加练习，最后形成可以提取的记忆；需要输出和及时复习。"))
        types = [e["type"] for e in events]
        self.assertIn("delta", types)
        self.assertEqual(types[-1], "done")
        done = events[-1]
        self.assertIsNone(done["report"])
        rep = socratic.finish_session(self.db, "local", sid)
        self.assertIn("facets", rep["report"])
        self.assertTrue(rep["report"]["summary"])

        detail = services.unit_detail(self.db, "local", unit_id)
        self.assertTrue(detail["mastery"]["reps"] > 0)

        unit = detail["unit"]
        result = feynman.evaluate(
            self.db, "local", unit,
            "学习是模仿加练习，最终形成可以提取的记忆。提高刺激强度可以合上书讲出来、做出来；提高刺激频度就是及时复习。但如果睡眠不足，效果会变差。",
            "peer",
        )
        self.assertIn("overall", result)
        self.assertGreater(result["overall"], 0)
        self.assertEqual(result["engine"], "demo")
        self.assertTrue(result["dimensions"])

    def test_full_six_turn_report(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", out["goal"]["id"], unit_id)
        sid = s["session"]["id"]
        texts = [
            "学习是模仿加练习，形成可以提取的记忆。",
            "因为神经回路需要刺激，输出比输入更强。",
            "如果睡眠不足，回路不稳定。",
            "比如合上书复述，或者做题。",
            "在需要长期记忆的知识上适用，一次性信息不适用。",
            "总结：学习是形成可提取的记忆，需要提高刺激强度和频度，并及时复习。",
        ]
        for text in texts:
            events = list(socratic.stream_reply(self.db, "local", sid, text))
        self.assertEqual(events[-1]["type"], "done")
        self.assertIsNotNone(events[-1]["report"])
        report = events[-1]["report"]
        self.assertTrue(report["facets"])
        self.assertTrue(report["next_step"])

    def test_pack_reinstall_preserves_srs(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        queue = services.review_queue(self.db, "local")
        card_id = queue["cards"][0]["id"]
        services.rate_card(self.db, "local", card_id, 4, 1000)
        before = self.db.one("SELECT * FROM cards WHERE id=?", (card_id,))
        pack = self.get_pack()
        install_pack(self.db, "local", goal_id, pack)
        after = self.db.one("SELECT * FROM cards WHERE id=?", (card_id,))
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["due_at"], after["due_at"])
        self.assertEqual(before["reps"], after["reps"])
        # no duplicate cards
        count = self.db.scalar("SELECT COUNT(*) FROM cards WHERE unit_id=?", (before["unit_id"],), 0)
        self.assertEqual(count, 2)

    def test_inspiration_notes_survive_export_import(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        services.create_note(self.db, "local", {
            "quote": "提取一次，记忆痕迹就被强化一次",
            "content": "早上第一件事就合书复述。",
            "unit_id": unit_id, "source": "unit",
        })
        services.create_note(self.db, "local", {"content": "只有一条想法", "source": "insights"})
        listed = services.list_notes(self.db, "local")
        self.assertEqual(listed["total"], 2)
        markdown = services.export_notes_markdown(self.db, "local")
        self.assertIn("提取一次", markdown)
        self.assertIn("只有一条想法", markdown)
        self.assertIn(out["next_units"][0]["title"], markdown, "导出要带上所属单元")

        payload = services.export_data(self.db, "local")
        self.assertEqual(len(payload["notes"]), 2)
        services.reset_user(self.db, "local")
        self.assertEqual(services.list_notes(self.db, "local")["total"], 0,
                         "重置必须清掉灵感笔记")
        result = backup.import_backup(self.db, "local", payload, mode="replace")
        self.assertEqual(result["inspirations"], 2, "导入必须恢复灵感笔记")
        restored = services.list_notes(self.db, "local")
        self.assertEqual(restored["total"], 2)
        linked = [n for n in restored["notes"] if n["unit_id"]]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["unit_title"], out["next_units"][0]["title"])
        searched = services.list_notes(self.db, "local", q="早上")
        self.assertEqual(len(searched["notes"]), 1)
        filtered = services.list_notes(self.db, "local", unit_id=linked[0]["unit_id"])
        self.assertEqual(len(filtered["notes"]), 1)

    def test_note_validation(self):
        with self.assertRaises(ValueError):
            services.create_note(self.db, "local", {"quote": "", "content": "   "})
        with self.assertRaises(ValueError):
            services.delete_note(self.db, "local", "missing-note")
        big = services.create_note(self.db, "local", {"quote": "字" * 3000})
        self.assertEqual(len(big["note"]["quote"]), 2000)
        unknown = services.create_note(self.db, "local", {
            "quote": "引用", "content": "内容", "unit_id": "no-such-unit", "source": "恶意来源"})
        self.assertIsNone(unknown["note"]["unit_id"])
        self.assertEqual(unknown["note"]["source"], "")

    def test_mistake_tags_are_persisted_and_aggregated(self):
        out = self.onboard()
        queue = services.review_queue(self.db, "local")
        self.assertTrue(queue["mistake_tags"], "分类要由后端下发，前端不复制一份")
        keys = [t["key"] for t in queue["mistake_tags"]]
        self.assertEqual(len(keys), 6)

        first = queue["cards"][0]
        res = services.rate_card(self.db, "local", first["id"], 1, 3000,
                                 mistake_tags=["concept", "bogus", "concept", "step"])
        self.assertEqual(res["mistake_tags"], ["concept", "step"], "未知标签丢弃、重复去重")
        log = self.db.query("SELECT * FROM review_logs WHERE user_id='local'")[-1]
        self.assertEqual(json.loads(log["mistake_tags_json"]), ["concept", "step"])
        self.assertEqual(log["missed_points"], 1)
        card = self.db.one("SELECT * FROM cards WHERE id=?", (first["id"],))
        self.assertEqual(json.loads(card["mistake_tags_json"]), ["concept", "step"])

        detail = services.unit_detail(self.db, "local", first["unit_id"])
        again = [c for c in detail["cards"] if c["id"] == first["id"]][0]
        self.assertEqual(again["mistake_tags"], ["concept", "step"])
        self.assertEqual(again["mistake_labels"], ["概念没吃透", "步骤缺一环"])

        second = queue["cards"][1]
        services.rate_card(self.db, "local", second["id"], 4, 1200, mistake_tags=["blank"])
        answered = self.db.one("SELECT * FROM cards WHERE id=?", (second["id"],))
        self.assertEqual(json.loads(answered["mistake_tags_json"]), [],
                         "答对后不该继续挂着上一次的错因")

        agg = services.insights(self.db, "local", out["goal"]["id"])["mistakes"]
        self.assertEqual(agg["tagged_reviews"], 1)
        self.assertEqual(agg["missed_total"], 1)
        counts = {t["key"]: t["count"] for t in agg["tags"]}
        self.assertEqual(counts, {"concept": 1, "step": 1})

    def test_more_than_four_tags_are_capped(self):
        out = self.onboard()
        card = services.review_queue(self.db, "local")["cards"][0]
        res = services.rate_card(self.db, "local", card["id"], 2, 1000,
                                 mistake_tags=["concept", "boundary", "step", "confusion",
                                               "carelessness", "blank"])
        self.assertEqual(len(res["mistake_tags"]), 4)

    def test_list_cards_covers_not_due_cards(self):
        out = self.onboard()
        card = services.review_queue(self.db, "local")["cards"][0]
        services.rate_card(self.db, "local", card["id"], 3, 2000)
        listed = services.list_cards(self.db, "local")
        self.assertGreater(listed["total"], 0)
        self.assertTrue(any(c["id"] == card["id"] for c in listed["cards"]),
                        "已复习（未到期）的卡也要能被列出来，否则无法提前加卡")
        by_unit = services.list_cards(self.db, "local", unit_id=card["unit_id"])
        self.assertTrue(all(c["unit_id"] == card["unit_id"] for c in by_unit["cards"]))
        self.assertTrue(by_unit["cards"])
        hit = services.list_cards(self.db, "local", q=card["front"][:4])
        self.assertTrue(any(c["id"] == card["id"] for c in hit["cards"]))
        miss = services.list_cards(self.db, "local", q="绝不存在的关键词zzz")
        self.assertEqual(miss["cards"], [])
        state = services.list_cards(self.db, "local", state="new")
        self.assertTrue(all(c["state"] == "new" for c in state["cards"]))

    def test_import_snapshots_database_first(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        payload = services.export_data(self.db, "local")

        result = backup.import_backup(self.db, "local", payload, mode="merge")
        self.assertTrue(result["snapshot"], "导入必须留下快照名")
        snap = backup.backup_dir() / result["snapshot"]
        self.assertTrue(snap.exists())
        # 快照里应该是导入前的状态：同一个目标，且带 8 个单元
        with sqlite3.connect(str(snap)) as conn:
            conn.row_factory = sqlite3.Row
            goals = conn.execute("SELECT id FROM goals WHERE id=?", (goal_id,)).fetchall()
            units = conn.execute("SELECT COUNT(*) FROM units WHERE goal_id=?", (goal_id,)).fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(len(goals), 1)
        self.assertEqual(units, 8)
        self.assertEqual(integrity, "ok")

        # 替换导入前先快照，旧数据要还能从快照里捞回来
        services.reset_user(self.db, "local")
        self.assertIsNone(services.active_goal(self.db, "local"))
        replaced = backup.import_backup(self.db, "local", payload, mode="replace")
        self.assertTrue(replaced["snapshot"])
        self.assertEqual(services.list_goals(self.db, "local") and True, True)

        names = [s["name"] for s in backup.list_snapshots()]
        self.assertGreaterEqual(len(names), 2)
        self.assertTrue(all(n.startswith("pre_import_") for n in names))
        backup.prune_snapshots("pre_import")
        self.assertLessEqual(len(backup.list_snapshots()), backup.SNAPSHOTS_KEPT)

    def test_failed_replace_import_leaves_old_data_intact(self):
        """替换导入必须整体回滚：不能出现「旧数据已删、新数据没进来」的空库。"""
        self.onboard()
        payload = services.export_data(self.db, "local")
        cards = payload["cards"]
        self.assertTrue(cards, "这条用例的前提是导出的数据里有卡片")
        poisoned = [dict(c) for c in cards]
        poisoned[0]["front"] = None          # front 是 NOT NULL，插入必然炸
        bad = {**payload, "cards": poisoned}
        before_cards = self.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id='local'", (), 0)
        before_goals = len(services.list_goals(self.db, "local") or [])
        with self.assertRaises(sqlite3.Error):
            backup.import_backup(self.db, "local", bad, mode="replace")
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id='local'", (), 0),
            before_cards, "导入失败把复习卡删光了")
        self.assertEqual(len(services.list_goals(self.db, "local") or []), before_goals,
                         "导入失败把目标删光了")
        self.assertIsNotNone(services.active_goal(self.db, "local"))

    def test_reset_without_goals_still_clears_user_data(self):
        """没有目标也要清干净：留着孤儿卡片，用户重建后它们会「复活」。"""
        services.ensure_user(self.db, "")
        ts = now_iso()
        self.db.insert("cards", {
            "id": "cd-orphan", "user_id": "local", "unit_id": "unit-gone", "probe_id": None,
            "front": "无目标时的卡片", "back": "应该被清掉", "state": "new", "step": 0,
            "stability": 0, "difficulty": 0, "due_at": ts, "last_review": None,
            "reps": 0, "lapses": 0, "mistake_tags_json": "[]",
            "created_at": ts, "updated_at": ts,
        })
        services.create_note(self.db, "local", {"quote": "没有目标也要能存", "content": "灵感"})
        self.assertIsNone(services.active_goal(self.db, "local"))
        services.reset_user(self.db, "local")
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM cards WHERE user_id='local'", (), 0), 0)
        self.assertEqual(len(services.list_notes(self.db, "local")["notes"]), 0)

    def test_search_tiers_put_exact_hits_before_lookalikes(self):
        """语义分再高也不该压过精确命中：搜索框是给人用的，不是给模型喂片段。"""
        out = self.onboard()
        units = services.get_goal_units(self.db, out["goal"]["id"])
        first = units[0]
        order = [u for u in units if u["id"] != first["id"]] + [first]
        old_rank, old_engine = embeddings.rank, embeddings.last_engine
        embeddings.rank = lambda q, docs, text_fn: [
            (0.9 - i * 0.05, d) for i, d in enumerate(order)]
        embeddings.last_engine = lambda: "BAAI/bge-m3"
        try:
            found = services.search(self.db, "local", first["title"])
        finally:
            embeddings.rank, embeddings.last_engine = old_rank, old_engine
        ids = [u["id"] for u in found["units"]]
        self.assertEqual(ids[0], first["id"], "精确命中被「只是有点像」挤下去了")
        self.assertEqual(found["units"][0]["matched_by"], ["lexical", "semantic"])
        self.assertEqual(found["fusion"], "tiered")
        self.assertTrue(all("lexical" not in u["matched_by"] for u in found["units"][1:]),
                        "只有一个字面命中才对，多一个说明样例选得不干净")

    def test_rating_can_be_undone_exactly(self):
        """掌握度是状态量（EMA），撤销只能整行还原：卡片、掌握度、日志、回忆、火候记录一起回。"""
        self.onboard()
        card = services.review_queue(self.db, "local")["cards"][0]
        unit_id = card["unit_id"]
        m_before = self.db.one(
            "SELECT * FROM mastery WHERE user_id='local' AND unit_id=? AND facet='recall'",
            (unit_id,))
        logs_before = self.db.scalar("SELECT COUNT(*) FROM review_logs", (), 0) or 0

        res = services.rate_card(self.db, "local", card["id"], 2, 5000,
                                 recall="只记得一半", mistake_tags=["blank"])
        undo = res["undo"]
        self.assertTrue(undo.get("op"))
        self.assertEqual(undo["rating"], 2)
        self.assertNotEqual(self.db.one("SELECT state FROM cards WHERE id=?", (card["id"],))["state"],
                            card["state"], "评分应该改变卡片状态")
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM review_logs", (), 0), logs_before + 1)

        services.undo_review(self.db, "local", undo["op"])
        restored = self.db.one("SELECT * FROM cards WHERE id=?", (card["id"],))
        for key in ("state", "step", "stability", "difficulty", "due_at", "reps", "lapses"):
            self.assertEqual(restored[key], card[key], f"{key} 没有回到评分前")
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM review_logs", (), 0), logs_before,
                         "评分日志要删掉")
        self.assertIsNone(self.db.one(
            "SELECT id FROM attempts WHERE kind='review_recall' AND unit_id=?", (unit_id,)),
            "回忆记录要一起删掉")
        m_after = self.db.one(
            "SELECT * FROM mastery WHERE user_id='local' AND unit_id=? AND facet='recall'",
            (unit_id,))
        if m_before:
            self.assertEqual(m_after["score"], m_before["score"])
            self.assertEqual(m_after["reps"], m_before["reps"])
        else:
            self.assertIsNone(m_after, "首评前没有掌握度行，撤销后也不该留下")
        self.assertIsNone(self.db.one("SELECT id FROM boil_records WHERE unit_id=?", (unit_id,)))
        self.assertTrue(self.db.one("SELECT id FROM events WHERE type='review.undone'"))

    def test_undo_refuses_when_unit_reviewed_since(self):
        self.onboard()
        cards = services.review_queue(self.db, "local", limit=20)["cards"]
        first = cards[0]
        same_unit = [c for c in cards[1:] if c["unit_id"] == first["unit_id"]]
        if not same_unit:
            self.skipTest("演示内容里同一单元只有一张卡，构不成场景")
        res = services.rate_card(self.db, "local", first["id"], 3, 1000)
        services.rate_card(self.db, "local", same_unit[0]["id"], 3, 1000)
        with self.assertRaises(ValueError) as cm:
            services.undo_review(self.db, "local", res["undo"]["op"])
        self.assertIn("又复习", str(cm.exception))

    def test_focus_sessions_count_only_completed(self):
        """番茄钟只有「完成」才计入学习时长；重复提交不能把 25 分钟记两遍。"""
        self.onboard()
        start = services.focus_start(25)
        self.assertEqual(start["minutes"], 25)
        before = self.db.scalar("SELECT COALESCE(SUM(minutes),0) FROM study_days", (), 0) or 0
        done = services.focus_finish(self.db, "local", start["session"], 25, outcome="completed")
        self.assertFalse(done["duplicate"])
        self.assertEqual((done["sessions"], done["minutes"]), (1, 25))
        after = self.db.scalar("SELECT COALESCE(SUM(minutes),0) FROM study_days", (), 0) or 0
        self.assertEqual(after, before + 25, "完成的专注要进 study_days")
        dup = services.focus_finish(self.db, "local", start["session"], 25, outcome="completed")
        self.assertTrue(dup["duplicate"])
        self.assertEqual(services.focus_today(self.db, "local")["sessions"], 1, "重复提交不能记两次")
        abandoned = services.focus_start(45)
        services.focus_finish(self.db, "local", abandoned["session"], 45, outcome="abandoned")
        self.assertEqual(services.focus_today(self.db, "local")["sessions"], 1, "放弃的不计入")
        with self.assertRaises(ValueError):
            services.focus_finish(self.db, "local", "", 25)

    def test_note_anchor_roundtrip_and_sanitizing(self):
        """跨公式的引文要存一个「去公式可见文本」锚点，对外不暴露内部列。"""
        self.onboard()
        created = services.create_note(self.db, "local", {
            "quote": "当 $x \\to 0$ 时，$\\sin x \\sim x$，这是等价无穷小替换",
            "content": "跨公式选中",
            "anchor": {"search": "时，这是等价无穷小替换", "occurrence": 3},
        })
        self.assertEqual(created["note"]["anchor"],
                         {"search": "时，这是等价无穷小替换", "occurrence": 3})
        self.assertNotIn("anchor_json", created["note"], "内部列不该外泄")
        listed = services.list_notes(self.db, "local")["notes"][0]
        self.assertEqual(listed["anchor"]["occurrence"], 3)
        short = services.create_note(self.db, "local", {
            "quote": "太短的锚点没有消歧价值", "content": "",
            "anchor": {"search": "ab", "occurrence": 1},
        })
        self.assertNotIn("anchor", short["note"])
        clamped = services.create_note(self.db, "local", {
            "quote": "另一条引文内容", "content": "",
            "anchor": {"search": "另一条引文内容", "occurrence": 999},
        })
        self.assertEqual(clamped["note"]["anchor"]["occurrence"], 40, "序号要夹住")
        export = services.export_data(self.db, "local")
        exported = [n for n in export["notes"] if n["id"] == created["id"]][0]
        self.assertIn("时，这是等价无穷小替换", exported.get("anchor_json") or "",
                      "锚点要随导出/导入一起走")

    def test_notes_backup_and_semantic_search(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        notes = services.save_unit_note(self.db, "local", unit_id, "mnemonic", "修路压路基")
        self.assertEqual(notes["mnemonic"]["content"], "修路压路基")
        detail = services.unit_detail(self.db, "local", unit_id)
        self.assertEqual(detail["notes"]["mnemonic"]["content"], "修路压路基")
        self.assertTrue(detail["related"])
        queue = services.review_queue(self.db, "local")
        self.assertTrue(queue["cards"])
        self.assertEqual(queue["cards"][0]["mnemonic"], "修路压路基")
        services.rate_card(self.db, "local", queue["cards"][0]["id"], 3, 1000)
        found = services.search(self.db, "local", "神经回路")
        self.assertTrue(found["units"])
        self.assertEqual(found["engine"], "local-hash-256")

        export = services.export_data(self.db, "local")
        self.assertIn("unit_notes", export)
        self.assertTrue(export["boil_records"])
        other = Database(Path(self.tmp.name) / "import.db")
        try:
            result = backup.import_backup(other, "local", export, "merge")
            self.assertGreaterEqual(result["goals"], 1)
            self.assertGreaterEqual(result["units"], 8)
            self.assertGreaterEqual(result["notes"], 1)
            self.assertGreaterEqual(result["records"], 1)
            dash = services.dashboard(other, "local")
            self.assertTrue(dash["has_goal"])
            imported = services.unit_detail(other, "local", unit_id)
            self.assertEqual(imported["notes"]["mnemonic"]["content"], "修路压路基")
            self.assertTrue(services.insights(other, "local")["records"])
        finally:
            other.close()

        # replace 模式：先清空，再恢复备份
        services.onboarding(self.db, {
            "name": "临时", "pack_id": "boil.xueshen.learning",
            "goal_title": "临时目标", "daily_minutes": 20,
        })
        self.assertGreaterEqual(len(services.list_goals(self.db, "local")), 2)
        replaced = backup.import_backup(self.db, "local", export, "replace")
        self.assertEqual(replaced["mode"], "replace")
        goals = services.list_goals(self.db, "local")
        self.assertEqual(len(goals), len(export["goals"]))
        restored = services.unit_detail(self.db, "local", unit_id)
        self.assertEqual(restored["notes"]["mnemonic"]["content"], "修路压路基")

    def test_insights_catalog_search_export_replan(self):
        out = self.onboard()
        goal_id = out["goal"]["id"]
        insights = services.insights(self.db, "local", goal_id)
        self.assertIn("forecast", insights)
        self.assertEqual(len(insights["forecast"]), 7)
        self.assertTrue(all(f["due"] == 0 for f in insights["forecast"]))
        catalog = services.pack_catalog(self.db, "local")
        self.assertTrue(any(p["id"] == PACK_ID for p in catalog["packs"]))
        detail = services.pack_detail(PACK_ID)
        self.assertEqual(len(detail["units"]), 8)
        found = services.search(self.db, "local", "学习")
        self.assertTrue(found["units"])
        export = services.export_data(self.db, "local")
        self.assertIn("goals", export)
        self.assertNotIn("api_key", json.dumps(export.get("llm", {})))
        replanned = services.update_goal_and_replan(self.db, "local", {
            "goal_id": goal_id, "daily_minutes": 55, "deadline": "2026-11-30",
        })
        self.assertEqual(replanned["goal"]["daily_minutes"], 55)
        self.assertGreaterEqual(replanned["plan"]["version"], 2)

    def test_compile_install_flow(self):
        text = """## 核心概念
学习是形成可提取的记忆。
- 先拆成最小单元
- 再合书复述
注意：睡眠不足时效果变差。

## 复习
复习不是重读，而是提取。
- 合上材料默写
- 对照补漏
"""
        res = services.compile_preview(self.db, "自定义材料", "学习科学", text, use_llm=False)
        self.assertTrue(res["pack"]["units"])
        pack_id = res["pack"]["id"]
        out = self.onboard(pack_id=pack_id, goal_title="自定义目标")
        self.assertTrue(out["has_goal"])
        self.assertGreaterEqual(out["counts"]["units"], 2)


class MemoryHealthHonestyTest(TempCase):
    """「记忆健康度 100%」可以在人什么都没记住的当天刷出来 —— 所以必须同时给放凉一天的口径。"""

    def _cards(self, n=2):
        self.onboard()
        rows = self.db.query("SELECT id FROM cards ORDER BY id LIMIT ?", (n,))
        return [r["id"] for r in rows]

    def _set_card(self, card_id, stability, reviewed_hours_ago):
        when = (datetime.now(timezone.utc) - timedelta(hours=reviewed_hours_ago)).isoformat(timespec="seconds")
        self.db.execute("UPDATE cards SET state='review', stability=?, last_review=? WHERE id=?",
                        (stability, when, card_id))

    def test_fresh_reviews_inflate_the_headline_number(self):
        ids = self._cards(2)
        self._set_card(ids[0], 2.0, 0.1)      # 刚刚复习过
        self._set_card(ids[1], 2.0, 24 * 5)   # 五天前复习过
        data = services.insights(self.db, "local")
        self.assertIsNotNone(data["memory_health"])
        self.assertEqual(data["settled_cards"], 1)
        self.assertIsNotNone(data["memory_health_settled"])
        self.assertGreater(data["memory_health"], data["memory_health_settled"])

    def test_settled_is_none_when_nothing_has_settled(self):
        ids = self._cards(2)
        self._set_card(ids[0], 2.0, 1)
        self._set_card(ids[1], 3.0, 2)
        data = services.insights(self.db, "local")
        self.assertEqual(data["settled_cards"], 0)
        self.assertIsNone(data["memory_health_settled"])
        self.assertGreater(data["memory_health"], 0.9)   #  headline 数字照样好看，所以才要写清口径


class CalibrationTest(TempCase):
    """模型预测的「你想得起来的概率」对得上实际结果吗 —— 这是项目第一份真数据。"""

    def logs(self, entries):
        from datetime import datetime, timedelta, timezone
        base = datetime.now(timezone.utc)
        for i, (predicted, rating, state) in enumerate(entries):
            self.db.insert("review_logs", {
                "id": f"rl{i}", "card_id": f"card{i}", "user_id": "local", "rating": rating,
                "reviewed_at": (base - timedelta(days=i)).isoformat(timespec="seconds"),
                "retention_before": predicted, "state_before": state, "state_after": "review",
            })

    def test_empty_database_says_so(self):
        out = services.calibration(self.db, "local")
        self.assertEqual(out["samples"], 0)
        self.assertFalse(out["enough"])
        self.assertIsNone(out["bias"])
        self.assertIn("还没有可比的复习", out["verdict"])

    def test_small_sample_refuses_to_conclude(self):
        self.logs([(0.9, 3, "review")] * 5)
        out = services.calibration(self.db, "local")
        self.assertEqual(out["samples"], 5)
        self.assertFalse(out["enough"])
        self.assertIn("再攒 15 次", out["verdict"])

    def test_new_and_learning_cards_never_count(self):
        """新卡内容还在眼前，模型没做预测；混进来会让曲线虚高、看起来特别准。"""
        self.logs([(0.95, 1, "new")] * 30 + [(0.95, 1, "learning")] * 30)
        out = services.calibration(self.db, "local")
        self.assertEqual(out["samples"], 0)

    def test_optimistic_model_is_called_out(self):
        self.logs([(0.95, 1, "review")] * 12 + [(0.95, 3, "review")] * 3 + [(0.65, 3, "review")] * 10)
        out = services.calibration(self.db, "local")
        self.assertTrue(out["enough"])
        self.assertGreater(out["bias"], 0.12)
        self.assertIn("偏乐观", out["verdict"])
        top = [b for b in out["buckets"] if b["label"] == "90–100%"][0]
        self.assertEqual(top["samples"], 15)
        self.assertEqual(top["predicted"], 0.95)
        self.assertLess(top["actual"], 0.3)

    def test_conservative_model_is_called_out(self):
        self.logs([(0.6, 3, "review")] * 20)
        out = services.calibration(self.db, "local")
        self.assertLess(out["bias"], -0.12)
        self.assertIn("偏保守", out["verdict"])

    def test_well_calibrated_reads_as_trustworthy(self):
        self.logs([(0.9, 3, "review")] * 10 + [(0.9, 1, "review")] * 0 + [(0.7, 3, "review")] * 5
                  + [(0.7, 1, "review")] * 5)
        out = services.calibration(self.db, "local")
        self.assertLessEqual(abs(out["bias"]), 0.12)
        self.assertIn("对得上", out["verdict"])

    def test_bucket_edges(self):
        self.logs([(0.6, 3, "review"), (1.0, 3, "review"), (0.05, 1, "review")] * 8)
        out = services.calibration(self.db, "local")
        by_label = {b["label"]: b for b in out["buckets"]}
        self.assertEqual(by_label["60–70%"]["samples"], 8)     # 0.6 归上界含
        self.assertEqual(by_label["90–100%"]["samples"], 8)    # 1.0 落在最后一桶
        self.assertEqual(by_label["0–60%"]["samples"], 8)

    def test_zero_prediction_is_not_a_prediction(self):
        """retention_before=0 是「没做预测」而不是「预测你肯定忘」，不能进统计。"""
        self.logs([(0.0, 1, "review")] * 30)
        out = services.calibration(self.db, "local")
        self.assertEqual(out["samples"], 0)
        self.assertEqual(sum(b["samples"] for b in out["buckets"]), 0)

    def test_brier_is_the_mean_squared_prediction_error(self):
        # 预测 0.9 却答对（误差 0.1）、预测 0.4 却答错（误差 0.4）→ (0.01 + 0.16) / 2
        self.logs([(0.9, 3, "review")] * 10 + [(0.4, 1, "review")] * 10)
        out = services.calibration(self.db, "local")
        self.assertEqual(out["brier"], 0.085)
        # 平均预测 0.65 vs 实际答对 0.50 → 偏差 0.15。两个数看的是不同的事：
        # bias 说「整体偏乐观还是偏保守」，brier 说「每一次预测差多少」。
        self.assertEqual(out["predicted"], 0.65)
        self.assertEqual(out["actual"], 0.5)
        self.assertEqual(out["bias"], 0.15)

    def test_brier_of_coin_flip_predictions(self):
        self.logs([(0.5, 3, "review")] * 10 + [(0.5, 1, "review")] * 10)
        self.assertEqual(services.calibration(self.db, "local")["brier"], 0.25)

    def test_gap_only_reported_for_buckets_with_enough_data(self):
        self.logs([(0.9, 3, "review")] * 20 + [(0.65, 3, "review"), (0.65, 1, "review")])
        out = services.calibration(self.db, "local")
        small = [b for b in out["buckets"] if b["label"] == "60–70%"][0]
        self.assertEqual(small["samples"], 2)
        self.assertIsNone(small["gap"])         # 两次样本不说差异
        self.assertEqual(services.calibration(self.db, "local")["requested_retention"], 0.9)


class ValidationTest(unittest.TestCase):
    def test_bad_pack(self):
        self.assertTrue(validate_pack({"id": "x"}))
        self.assertTrue(validate_pack({"id": "x", "name": "x", "version": "1", "units": []}))
        bad = {"id": "x", "name": "x", "version": "1", "units": [{"id": "u", "title": "t"}]}
        self.assertTrue(validate_pack(bad))

    def test_loose_json(self):
        from boil.llm import parse_json_loose
        self.assertEqual(parse_json_loose('```json\n{"a":1,}\n```'), {"a": 1})
        self.assertEqual(parse_json_loose('前缀 {"a": [1,2,]} 后缀'), {"a": [1, 2]})
        with self.assertRaises(Exception):
            parse_json_loose("not json")


if __name__ == "__main__":
    unittest.main()
