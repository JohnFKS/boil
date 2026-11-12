"""苏格拉底连续对话：多轮不串位、结束后可原地继续。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from boil import socratic, services  # noqa: E402
from boil.server import App           # noqa: E402
from test_core import TempCase        # noqa: E402


class ContinuousDialogueTest(TempCase):
    def _session(self):
        out = self.onboard()
        unit_id = out["next_units"][0]["id"]
        s = socratic.start_session(self.db, "local", out["goal"]["id"], unit_id)
        return s["session"]["id"]

    def test_three_turns_stay_active_and_each_answer_is_a_new_message(self):
        sid = self._session()
        for i in range(1, 4):
            events = list(socratic.stream_reply(
                self.db, "local", sid, f"第 {i} 轮：我的理解是价格上升时需求量下降。"))
            done = [e for e in events if e["type"] == "done"][-1]
            self.assertIsNone(done["report"], f"第 {i} 轮不该提前收束")
            s = socratic.get_session(self.db, sid)["session"]
            self.assertEqual(s["status"], "active")
            self.assertEqual(s["turn"], i)
        final = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(len(final["messages"]), 7, "首问 + 3 组问答")
        self.assertEqual([m["role"] for m in final["messages"]].count("assistant"), 4)

    def test_reopen_after_finish_keeps_history_and_allows_more_turns(self):
        sid = self._session()
        list(socratic.stream_reply(self.db, "local", sid, "学习是形成可提取的记忆。"))
        socratic.finish_session(self.db, "local", sid)
        before = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(before["status"], "completed")
        history_len = len(before["messages"])

        reopened = socratic.reopen_session(self.db, "local", sid)["session"]
        self.assertEqual(reopened["status"], "active")
        self.assertEqual(reopened["ended_at"], None)
        self.assertEqual(len(reopened["messages"]), history_len, "历史一句都不能丢")
        self.assertIsNone(reopened["meta"].get("report"))
        self.assertEqual(len(reopened["meta"].get("prior_reports") or []), 1)
        self.assertGreater(reopened["meta"]["max_turns"], before["turn"])

        events = list(socratic.stream_reply(
            self.db, "local", sid, "继续追问：那为什么输出比输入更有效？"))
        done = [e for e in events if e["type"] == "done"][-1]
        self.assertIsNone(done["report"], "续聊后不该立刻又收束")
        after = socratic.get_session(self.db, sid)["session"]
        self.assertEqual(after["status"], "active")
        self.assertEqual(after["turn"], before["turn"] + 1)
        self.assertEqual(len(after["messages"]), history_len + 2)

    def test_six_turn_auto_close_can_be_continued(self):
        sid = self._session()
        texts = [f"第 {i} 次作答：本质、条件、边界我再展开一点。" for i in range(1, 7)]
        for t in texts:
            events = list(socratic.stream_reply(self.db, "local", sid, t))
        self.assertIsNotNone([e for e in events if e["type"] == "done"][-1]["report"])
        self.assertEqual(socratic.get_session(self.db, sid)["session"]["status"],
                         "completed")

        socratic.reopen_session(self.db, "local", sid)
        events = list(socratic.stream_reply(self.db, "local", sid,
                                            "我还想再问一个反例。"))
        self.assertIsNone([e for e in events if e["type"] == "done"][-1]["report"])
        self.assertEqual(socratic.get_session(self.db, sid)["session"]["status"],
                         "active")

    def test_reopen_is_idempotent_for_active_session(self):
        sid = self._session()
        first = socratic.reopen_session(self.db, "local", sid)["session"]
        second = socratic.reopen_session(self.db, "local", sid)["session"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "active")
        self.assertEqual(len(second["messages"]), 1, "幂等调用不该凭空加消息")

    def test_reopen_route_is_registered(self):
        routes = App(self.db).routes
        self.assertTrue(any(method == "POST" and "reopen" in pattern.pattern
                            for method, pattern, _ in routes))


class SocraticFrontendGuardTest(unittest.TestCase):
    """前端曾因重复 DOM id 让第二轮回答写进上一轮气泡，这里守住静态特征。"""

    def test_stream_bubble_uses_local_references_not_duplicate_ids(self):
        src = (ROOT / "static/js/views/socratic.js").read_text(encoding="utf-8")
        self.assertNotIn('getElementById("stream-', src,
                         "重复 id 会让增量永远写进第一轮气泡")
        self.assertIn('class="msg assistant stream-msg"', src)
        self.assertIn("currentPlaceholder", src)

    def test_aborted_stream_unlocks_the_composer(self):
        src = (ROOT / "static/js/views/socratic.js").read_text(encoding="utf-8")
        self.assertIn('if (e.name === "AbortError") {', src)
        self.assertGreater(
            src.index('if (e.name === "AbortError") {'),
            src.index("setBusy(false);\n      input.focus();"),
            "AbortError 分支必须自己解锁，否则输入框永久禁用")

    def test_stream_api_cleans_up_on_every_exit_path(self):
        src = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
        self.assertIn("if (activeStream === controller) activeStream = null;", src)
        self.assertIn("reader?.releaseLock()", src)
        self.assertIn("if (e instanceof ApiError) throw e;", src)


if __name__ == "__main__":
    unittest.main()
