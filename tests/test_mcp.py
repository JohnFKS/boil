from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import services
from boil.db import Database


class MCPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-mcp-")
        data = Path(cls.tmp.name)
        db = Database(data / "boil.db")
        services.ensure_user(db, "MCP")
        cls.goal = services.onboarding(db, {
            "name": "MCP", "pack_id": "boil.xueshen.learning",
            "goal_title": "MCP 学习", "daily_minutes": 40,
            "deadline": "2026-12-31",
        })
        cls.unit_id = cls.goal["next_units"][0]["id"]
        db.close()
        env = dict(os.environ, BOIL_DATA_DIR=str(data), BOIL_LLM_OFF="1",
                   BOIL_EMBEDDING_OFF="1", PYTHONPATH=str(ROOT))
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "boil.mcp_server"],
            cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.tmp.cleanup()

    def rpc(self, req_id, method, params=None):
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        self.assertTrue(line, f"no response for {method}")
        return json.loads(line)

    def tool(self, req_id, name, args=None):
        res = self.rpc(req_id, "tools/call", {"name": name, "arguments": args or {}})
        self.assertIn("result", res, res)
        self.assertFalse(res["result"].get("isError"), res)
        return json.loads(res["result"]["content"][0]["text"])

    def test_mcp_protocol_and_tools(self):
        init = self.rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "boil")
        tools = self.rpc(2, "tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("boil_get_advice", names)
        self.assertIn("boil_submit_review", names)
        self.assertIn("boil_socratic_reply", names)
        health = self.tool(3, "boil_health")
        self.assertEqual(health["app"], "Boil")
        advice = self.tool(4, "boil_get_advice", {"minutes": 30})
        self.assertIn("mode", advice)
        units = self.tool(5, "boil_list_units", {"limit": 10})
        self.assertTrue(units["units"])
        unit = self.tool(6, "boil_get_unit", {"unit_id": self.unit_id})
        self.assertTrue(unit["essence"])
        search = self.tool(7, "boil_search", {"q": "记忆"})
        self.assertTrue(search["units"])
        queue = self.tool(8, "boil_review_queue", {"limit": 5})
        self.assertTrue(queue["cards"])
        note = self.tool(9, "boil_save_note", {"unit_id": self.unit_id,
                                             "kind": "mnemonic",
                                             "content": "MCP 速记"})
        self.assertEqual(note["mnemonic"]["content"], "MCP 速记")
        soc = self.tool(10, "boil_start_socratic", {"unit_id": self.unit_id})
        self.assertTrue(soc["first_question"])
        reply = self.tool(11, "boil_socratic_reply", {
            "session_id": soc["session_id"],
            "message": "学习是模仿加练习，形成可提取的记忆。",
        })
        self.assertTrue(reply["reply"])
        report = self.tool(12, "boil_finish_socratic", {"session_id": soc["session_id"]})
        self.assertTrue(report["report"]["summary"])
        fey = self.tool(13, "boil_feynman_evaluate", {
            "unit_id": self.unit_id,
            "text": "学习是形成可提取的记忆。需要合书复述，也要及时复习。睡眠不足时效果会变差。",
        })
        self.assertGreater(fey["overall"], 0)
        insights = self.tool(14, "boil_insights")
        self.assertIn("mastery", insights)
        self.assertTrue(insights["records"])
        bad = self.rpc(15, "tools/call", {"name": "boil_nope", "arguments": {}})
        self.assertTrue(bad["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
