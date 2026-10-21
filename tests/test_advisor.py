from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import advisor, config, heat, services, socratic
from boil.db import Database
from boil.packs import all_available_packs, pack_quality

PACK_ID = "boil.xueshen.learning"
UTC = timezone.utc


class AdvisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-adv-")
        base = Path(self.tmp.name)
        self.old = {
            "PACKS_DIR": config.PACKS_DIR, "DATA_DIR": config.DATA_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH, "LLM_STATE_PATH": config.LLM_STATE_PATH,
        }
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.PACKS_DIR.mkdir()
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        self.db = Database(base / "test.db")
        services.ensure_user(self.db, "A")

    def tearDown(self):
        self.db.close()
        for k, v in self.old.items():
            setattr(config, k, v)
        self.tmp.cleanup()

    def onboard(self):
        return services.onboarding(self.db, {
            "name": "A", "pack_id": PACK_ID, "goal_title": "测试",
            "daily_minutes": 40, "deadline": "2026-12-31",
        })

    def set_mastery(self, unit_id, score=0.6, reps=3):
        from boil.utils import now_iso
        ts = now_iso()
        for facet in ("recall", "explain", "apply", "transfer"):
            self.db.upsert("mastery", {
                "user_id": "local", "unit_id": unit_id, "facet": facet,
                "score": score, "confidence": 0.7, "reps": reps, "lapses": 0,
                "last_evidence_at": ts, "updated_at": ts,
            }, "user_id,unit_id,facet")

    def insert_completed_report(self, goal_id, unit_id, gaps):
        from boil.utils import new_id, now_iso
        sid = new_id("ss")
        meta = {"stage": "done", "report": {
            "summary": "test", "gaps": gaps, "facets": {},
            "confidence": 0.8, "engine": "demo", "next_step": "x",
        }, "evidence_added": True}
        ts = now_iso()
        future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
        self.db.insert("sessions", {
            "id": sid, "user_id": "local", "goal_id": goal_id, "unit_id": unit_id,
            "kind": "socratic", "status": "completed", "stage": "done", "turn": 3,
            "meta_json": json.dumps(meta, ensure_ascii=False),
            "started_at": ts, "ended_at": future,
        })

    def test_01_no_goal_and_new_unit(self):
        a = advisor.build_advice(self.db, "local")
        self.assertEqual(a["mode"], "onboarding")
        out = self.onboard()
        a = advisor.build_advice(self.db, "local", out["goal"]["id"], 40)
        self.assertEqual(a["mode"], "digest")
        self.assertEqual(a["target"]["heat"], 0.0)
        self.assertTrue(a["ladder"])
        low = advisor.build_advice(self.db, "local", out["goal"]["id"], 8)
        self.assertEqual(low["mode"], "audio")
        self.assertEqual(low["cost_minutes"], 5)

    def test_02_socratic_then_feynman_then_benchmark(self):
        out = self.onboard()
        goal = out["goal"]["id"]
        unit = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", goal, unit)
        list(socratic.stream_reply(self.db, "local", s["session"]["id"],
                                   "学习是模仿加练习，形成可提取的记忆。"))
        socratic.finish_session(self.db, "local", s["session"]["id"])
        a = advisor.build_advice(self.db, "local", goal, 40)
        self.assertIn(a["mode"], ("socratic", "digest"))
        self.assertTrue(a["pn"]["missing"])

        self.set_mastery(unit, 0.62)
        a = advisor.build_advice(self.db, "local", goal, 40)
        self.assertEqual(a["mode"], "feynman", a)
        self.assertGreaterEqual(a["heat"], 40)
        self.assertTrue(a["pn"]["missing"])

        # 清掉 PN 缺口并升温 → 极致对标
        self.insert_completed_report(goal, unit, [])
        self.set_mastery(unit, 0.79, reps=3)
        a = advisor.build_advice(self.db, "local", goal, 40)
        self.assertEqual(a["mode"], "benchmark", a)
        self.assertIn("mode=examiner", a["href"])

    def test_03_due_priority_and_feedback(self):
        out = self.onboard()
        goal = out["goal"]["id"]
        unit = out["next_units"][0]["id"]
        from boil.utils import now_iso
        card = self.db.one("SELECT * FROM cards WHERE unit_id=? LIMIT 1", (unit,))
        self.db.execute("UPDATE cards SET state='review', due_at=? WHERE id=?",
                        ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), card["id"]))
        a = advisor.build_advice(self.db, "local", goal, 40)
        self.assertEqual(a["mode"], "recall")
        self.assertGreater(a["due_total"], 0)
        self.assertTrue(a["reason"])
        accepted = advisor.feedback(self.db, "local", {"action": "accepted", "advice": a})
        self.assertTrue(accepted["ok"])
        dismissed = advisor.feedback(self.db, "local", {"action": "dismissed", "advice": a})
        self.assertTrue(dismissed["ok"])
        events = self.db.query("SELECT type FROM events WHERE user_id='local' ORDER BY seq DESC LIMIT 2")
        self.assertEqual({e["type"] for e in events}, {"advice.accepted", "advice.dismissed"})
        with self.assertRaises(ValueError):
            advisor.feedback(self.db, "local", {"action": "maybe", "advice": a})

    def test_04_heat_decay_and_penalty(self):
        from boil.utils import now_iso
        old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        m = {"reps": 2, "facets": {f: {"score": 0.6, "last_evidence_at": old}
                                    for f in ("recall", "explain", "apply", "transfer")}}
        warm = heat.compute_heat(m, 0)
        due = heat.compute_heat(m, 3)
        fresh = heat.compute_heat({**m, "facets": {f: {"score": 0.6, "last_evidence_at": now_iso()}
                                                   for f in ("recall", "explain", "apply", "transfer")}}, 0)
        self.assertLess(warm, fresh)
        self.assertLess(due, warm)
        self.assertGreaterEqual(due, 0)
        self.assertEqual(heat.compute_heat({"reps": 0}), 0)

    def test_05_records_and_quality(self):
        out = self.onboard()
        goal = out["goal"]["id"]
        unit = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", goal, unit)
        list(socratic.stream_reply(self.db, "local", s["session"]["id"], "学习是记忆。"))
        socratic.finish_session(self.db, "local", s["session"]["id"])
        recs = heat.history(self.db, "local", unit_id=unit)
        self.assertTrue(recs)
        self.assertEqual(recs[0]["mode"], "socratic")
        self.assertGreater(recs[0]["heat_after"], recs[0]["heat_before"])
        pack = next(p for p in all_available_packs() if p["id"] == PACK_ID)
        q = pack_quality(pack)
        self.assertEqual(q["atomic_rate"], 1.0)
        self.assertEqual(q["probe_coverage"], 1.0)
        self.assertTrue(q["dag_ok"])

    def test_06_boundary_logic(self):
        units = [
            {"id": "b", "title": "B", "order_index": 0},
            {"id": "a", "title": "A", "order_index": 1},
        ]
        states = {"b": {"status": "new", "reps": 0, "heat": 0},
                  "a": {"status": "new", "reps": 0, "heat": 0}}
        rels = [{"from_unit": "a", "to_unit": "b", "kind": "prerequisite"}]
        target, boundary = advisor._next_target(units, states, rels)
        self.assertTrue(boundary)
        self.assertEqual(target["id"], "a")


if __name__ == "__main__":
    unittest.main()
