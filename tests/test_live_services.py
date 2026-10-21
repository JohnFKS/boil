from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, feynman, llm, services, socratic
from boil.db import Database

PACK_ID = "boil.xueshen.learning"


REPORT = {
    "summary": "你能说出核心，但在边界上还模糊。",
    "understood": ["学习形成可提取记忆", "需要输出"],
    "gaps": ["刺激频度没有说清"],
    "misconceptions": [],
    "facets": {"recall": 0.8, "explain": 0.7, "apply": 0.6, "transfer": 0.45},
    "next_step": "用 90 秒讲清及时复习的作用。",
    "confidence": 0.8,
}

FEYNMAN = {
    "overall": 0.8,
    "dimensions": {
        "correctness": {"score": 0.85, "reason": "核心正确"},
        "completeness": {"score": 0.7, "reason": "少了一个点"},
        "structure": {"score": 0.8, "reason": "有因果链"},
        "simplicity": {"score": 0.8, "reason": "表达清楚"},
        "boundary": {"score": 0.6, "reason": "边界偏弱"},
        "analogy": {"score": 0.7, "reason": "有类比"},
    },
    "gaps": ["刺激频度"],
    "jargon": [],
    "model_answer": "学习是形成可提取的记忆，需要强度与频度。",
    "followups": ["如果睡眠不足会怎样？", "举一个反例。"],
    "next_step": "补上及时复习。",
}

PACK = {
    "name": "AI 生成示例包",
    "description": "用于测试",
    "subject": "学习科学",
    "license": "user-provided",
    "units": [{
        "title": "记忆",
        "essence": "学习是形成可提取的记忆。",
        "analogy": "",
        "example": "合书复述。",
        "steps": ["拆成最小单元", "合书复述"],
        "boundaries": ["睡眠不足时效果差"],
        "misconceptions": ["看懂等于记住"],
        "faq": [{"q": "为什么要复述？", "a": "为了提取。"}],
        "key_points": ["可提取的记忆", "合书复述", "睡眠"],
        "difficulty": 2,
        "importance": 4,
        "estimated_minutes": 35,
        "probes": [
            {"kind": "recall", "prompt": "核心是什么？", "answer": "形成可提取的记忆。", "rubric": [], "hints": []},
            {"kind": "explain", "prompt": "为什么？", "answer": "因为提取强化回路。", "rubric": [], "hints": []},
        ],
        "cards": [{"front": "核心是什么？", "back": "形成可提取的记忆。"}],
    }],
}


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages") or []
        text = "\n".join(str(m.get("content", "")) for m in messages)
        stream = bool(payload.get("stream"))
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ("你提到了核心。", "为什么它成立？"):
                self.wfile.write(f"data: {json.dumps({'choices':[{'delta':{'content':piece}}]}, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        if payload.get("response_format"):
            if "费曼" in text or "评分标准" in text:
                content = json.dumps(FEYNMAN, ensure_ascii=False)
            elif "LearningPack" in text or "知识单元" in text and "units" in text:
                content = json.dumps(PACK, ensure_ascii=False)
            else:
                content = json.dumps(REPORT, ensure_ascii=False)
        else:
            content = "先用自己的话说说：这个单元最核心的一句话是什么？"
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class LiveServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-live-")
        base = Path(self.tmp.name)
        self.old = {
            "DATA_DIR": config.DATA_DIR, "PACKS_DIR": config.PACKS_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH, "LLM_STATE_PATH": config.LLM_STATE_PATH,
            "LLM_OFF": config.LLM_OFF,
        }
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.PACKS_DIR.mkdir()
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.LLM_OFF = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        llm.set_config({
            "base_url": f"http://127.0.0.1:{self.server.server_address[1]}/v1",
            "api_key": "sk-live-test-1234567890",
            "model": "fake-live", "deep_model": "fake-live",
        })
        self.db = Database(base / "live.db")
        services.ensure_user(self.db, "Live")
        self.goal = services.onboarding(self.db, {
            "name": "Live", "pack_id": PACK_ID, "goal_title": "Live 学习",
            "daily_minutes": 40, "deadline": "2026-12-31",
        })
        self.unit_id = self.goal["next_units"][0]["id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def test_socratic_live(self):
        s = socratic.start_session(self.db, "local", self.goal["goal"]["id"], self.unit_id)
        first = s["session"]["messages"][0]["content"]
        self.assertIn("最核心", first)
        events = list(socratic.stream_reply(self.db, "local", s["session"]["id"],
                                            "学习是形成可提取的记忆。"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["engine"], "live")
        rep = socratic.finish_session(self.db, "local", s["session"]["id"])
        self.assertEqual(rep["report"]["engine"], "live")
        self.assertGreater(rep["report"]["facets"]["recall"], 0.5)
        self.assertTrue(rep["report"]["gaps"])

    def test_feynman_live(self):
        row = self.db.one("SELECT * FROM units WHERE id=?", (self.unit_id,))
        from boil.packs import unit_from_row
        unit = unit_from_row(row)
        res = feynman.evaluate(self.db, "local", unit, "学习是形成可提取的记忆。需要合书复述，也要及时复习。睡眠不足时效果会变差。", "peer")
        self.assertEqual(res["engine"], "live")
        self.assertGreater(res["overall"], 0.7)
        self.assertTrue(res["dimensions"]["correctness"]["reason"])

    def test_compiler_live(self):
        res = services.compile_preview(self.db, "AI 材料", "学习科学",
                                       "## 记忆\n学习是形成可提取的记忆。合上材料复述，比反复阅读更有效。\n- 合书复述：不看材料，用自己的话重建知识。\n- 及时复习：在遗忘前回测，而不是等忘光了再学。\n注意：睡眠不足时，记忆巩固会变差，先恢复状态。\n", use_llm=True)
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["pack"]["name"], "AI 生成示例包")
        self.assertTrue(res["pack"]["units"][0]["probes"])


if __name__ == "__main__":
    unittest.main()
