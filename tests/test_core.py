from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import backup, compiler, config, embeddings, feynman, llm, mastery, planner, services, socratic
from boil.compiler import compile_heuristic, compile_material
from boil.db import Database, SCHEMA_VERSION
from boil.packs import (all_available_packs, install_pack, validate_pack)
from boil.srs.fsrs import Scheduler, interval_label

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
        self._llm_old = (llm.enabled, llm.chat_json)

    def tearDown(self):
        llm.enabled, llm.chat_json = self._llm_old
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
