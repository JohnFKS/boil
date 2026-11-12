"""灵活学习：24 小时预算、随时重排、自由学习队列、稍后再学。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from boil import config, planner, services  # noqa: E402
from boil.server import App                 # noqa: E402
from test_core import TempCase              # noqa: E402


def _units(n: int = 20):
    return [{"id": f"u{i}", "title": f"知识点{i}", "estimated_minutes": 45,
             "importance": 3, "order_index": i} for i in range(n)]


class PlannerFlexTest(unittest.TestCase):
    def test_24h_budget_is_not_clamped_back_and_fills_more_units(self):
        units = _units()
        kw = dict(start=date.today(), deadline=date.today() + timedelta(days=60))
        small = planner.build_plan(units, [], daily_minutes=40, **kw)
        large = planner.build_plan(units, [], daily_minutes=1440, **kw)
        self.assertLessEqual(len(small["days"][0]["new_units"]), 2)
        self.assertGreater(len(large["days"][0]["new_units"]),
                           len(small["days"][0]["new_units"]),
                           "设 24 小时却仍每天只排 1~2 个单元，预算就白设了")
        self.assertGreater(large["days"][0]["new_minutes"],
                           small["days"][0]["new_minutes"])

    def test_intensity_and_rest_day_preferences_are_honored(self):
        units = _units()
        kw = dict(start=date.today(), deadline=date.today() + timedelta(days=60))
        capped = planner.build_plan(units, [], daily_minutes=1440, unit_cap=2, **kw)
        self.assertLessEqual(len(capped["days"][0]["new_units"]), 2)
        no_rest = planner.build_plan(units, [], daily_minutes=60,
                                     rest_days_enabled=False, **kw)
        self.assertTrue(all(not d.get("is_rest") for d in no_rest["days"]),
                        "关掉休息日后不该还有强制休息日")

    def test_progress_counts_units_learned_ahead_of_the_plan(self):
        plan = planner.build_plan(_units(4), [], daily_minutes=40,
                                  start=date.today(),
                                  deadline=date.today() + timedelta(days=30))
        p = planner.progress(plan, {"u0"}, counted_ids={f"u{i}" for i in range(20)})
        self.assertEqual(p["total"], 20, "提前学的单元也要进进度分母")
        self.assertEqual(p["done"], 1)


class FlexibleLearningTest(TempCase):
    def _onboard(self):
        return self.onboard(
            deadline=(date.today() + timedelta(days=60)).isoformat())

    def test_dashboard_exposes_every_unit_for_free_learning(self):
        self._onboard()
        d = services.dashboard(self.db, config.DEFAULT_USER)
        self.assertEqual(len(d["learning_units"]), d["counts"]["units"],
                         "全部单元都应能直接点开，不受计划日期限制")
        self.assertGreater(len(d["next_units"]), 0)

    def test_snooze_moves_unit_out_of_recommendations_and_back(self):
        self._onboard()
        user = config.DEFAULT_USER
        uid = services.dashboard(self.db, user)["learning_units"][0]["id"]
        out = services.snooze_unit(self.db, user, uid, True)
        self.assertTrue(out["snoozed"])
        d2 = services.dashboard(self.db, user)
        self.assertNotIn(uid, [u["id"] for u in d2["next_units"]])
        self.assertIn(uid, [u["id"] for u in d2["learning_units"]],
                      "稍后再学不是删除，自由学习列表里仍能看到")
        self.assertTrue(next(u for u in d2["learning_units"]
                             if u["id"] == uid)["snoozed"])
        services.snooze_unit(self.db, user, uid, False)
        d3 = services.dashboard(self.db, user)
        self.assertIn(uid, [u["id"] for u in d3["next_units"]],
                      "恢复后应重新出现在推荐队列")

    def test_unit_detail_has_prev_next_neighbors(self):
        self._onboard()
        d = services.dashboard(self.db, config.DEFAULT_USER)
        ids = [u["id"] for u in d["learning_units"]]
        first = services.unit_detail(self.db, config.DEFAULT_USER, ids[0])
        self.assertIsNone(first["neighbors"]["prev"])
        self.assertEqual(first["neighbors"]["next"], ids[1])
        self.assertFalse(first["snoozed"])
        middle = services.unit_detail(self.db, config.DEFAULT_USER, ids[1])
        self.assertEqual(middle["neighbors"]["prev"], ids[0])
        self.assertEqual(middle["neighbors"]["next"], ids[2])

    def test_replan_accepts_24h_and_persists_preferences(self):
        self._onboard()
        user = config.DEFAULT_USER
        out = services.update_goal_and_replan(self.db, user, {
            "daily_minutes": 1440, "unit_cap": 8, "rest_days_enabled": False})
        self.assertEqual(out["goal"]["daily_minutes"], 1440,
                         "24 小时不该被压回 240 分钟")
        self.assertEqual(out["plan_prefs"]["unit_cap"], 8)
        self.assertFalse(out["plan_prefs"]["rest_days_enabled"])
        # 设置页只带三个老字段，不该把强度档清掉
        out2 = services.update_goal_and_replan(self.db, user, {"daily_minutes": 60})
        self.assertEqual(out2["plan_prefs"]["unit_cap"], 8)
        self.assertFalse(out2["plan_prefs"]["rest_days_enabled"])

    def test_snooze_route_is_registered(self):
        routes = App(self.db).routes
        self.assertTrue(any(method == "POST" and "snooze" in pattern.pattern
                            for method, pattern, _ in routes))


if __name__ == "__main__":
    unittest.main()
