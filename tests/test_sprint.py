"""备考冲刺三件套：错题本、考前速背、限时自测卷。

这三块都是从 kaobuddy（MIT）借来的能力，落在 Boil 自己的数据结构上。测试盯的是
"借来的语义有没有被实现歪"：错题本必须会退出、速背必须斩得掉、判分判不动时必须承认。
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, services  # noqa: E402
from boil.db import Database  # noqa: E402
from boil.server import App, Handler, Server, redact_path  # noqa: E402

PACK = "boil.xueshen.learning"


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-sprint-")
        base = Path(self.tmp.name)
        self.old = {k: getattr(config, k) for k in
                    ("DATA_DIR", "PACKS_DIR", "LLM_CONFIG_PATH", "LLM_STATE_PATH")}
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        self.db = Database(base / "sprint.db")
        self.user = config.DEFAULT_USER
        services.onboarding(self.db, {"name": "冲刺者", "pack_id": PACK,
                                      "goal_title": "期末", "daily_minutes": 30,
                                      "deadline": "2026-11-01"})

    def tearDown(self):
        self.db.close()
        for key, value in self.old.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def first_card(self):
        return services.review_queue(self.db, self.user, limit=1)["cards"][0]

    # ---------------- 错题本 ----------------
    def test_mistake_book_aggregates_tags_and_counts(self):
        card = self.first_card()
        services.rate_card(self.db, self.user, card["id"], 1, mistake_tags=["concept"])
        services.rate_card(self.db, self.user, card["id"], 2, mistake_tags=["concept", "boundary"])
        book = services.mistake_book(self.db, self.user)
        self.assertEqual(book["totals"]["cards"], 1)
        item = book["items"][0]
        self.assertEqual(item["wrongs"], 2)
        keys = {t["key"]: t["count"] for t in item["tag_counts"]}
        self.assertEqual(keys, {"concept": 2, "boundary": 1})
        self.assertEqual(item["front"], card["front"])

    def test_mistake_book_exits_after_you_get_it_right(self):
        """错题本不是历史档案：后来答对回去的必须退出去，否则它会永远吓唬人。"""
        card = self.first_card()
        services.rate_card(self.db, self.user, card["id"], 1, mistake_tags=["blank"])
        self.assertEqual(len(services.mistake_book(self.db, self.user)["items"]), 1)
        again = services.review_by_ids(self.db, self.user, [card["id"]])["cards"][0]
        services.rate_card(self.db, self.user, again["id"], 4)
        self.assertEqual(services.mistake_book(self.db, self.user)["items"], [])

    def test_practice_by_ids_reports_what_it_could_not_find(self):
        card = self.first_card()
        out = services.review_by_ids(self.db, self.user, [card["id"], "c_不存在", ""])
        self.assertEqual(len(out["cards"]), 1)
        self.assertEqual(out["missing"], 1)
        self.assertEqual(out["cards"][0]["due_label"], "错题重练")

    def test_long_card_ids_are_not_truncated(self):
        """卡片 id 里带包名与单元名，实测 67 字符；早先按 64 截断会查不到还谎报成功。"""
        card = self.first_card()
        self.assertGreater(len(card["id"]), 64, "演示包 id 不够长，测不到这个回归")
        out = services.review_by_ids(self.db, self.user, [card["id"]])
        self.assertEqual(len(out["cards"]), 1)

    # ---------------- 考前速背 ----------------
    def test_cram_counts_down_and_shot_removes_item(self):
        plan = services.cram_plan(self.db, self.user, limit=40)
        self.assertEqual(plan["total"], 8)
        self.assertEqual(plan["left"], 8)
        from boil.utils import days_between, now, parse_iso
        self.assertEqual(plan["days_left"],
                         int(round(days_between(now(), parse_iso("2026-11-01")))))
        target = plan["items"][0]["unit_id"]
        services.cram_shot(self.db, self.user, target)
        after = services.cram_plan(self.db, self.user, limit=40)
        self.assertEqual(after["left"], plan["left"] - 1)
        self.assertEqual(after["shot"], 1)
        self.assertNotIn(target, [i["unit_id"] for i in after["items"]])
        services.cram_shot(self.db, self.user, target, undo=True)
        self.assertEqual(services.cram_plan(self.db, self.user, limit=40)["left"], plan["left"])

    def test_cram_ranks_important_and_weak_first(self):
        plan = services.cram_plan(self.db, self.user, limit=40)
        top = plan["items"][0]
        self.assertEqual(top["importance"], max(i["importance"] for i in plan["items"]))
        self.assertTrue(top["reason"])
        self.assertTrue(top["essence"], "速背项没有本质，斩了也背不到东西")

    def test_cram_rejects_units_from_another_goal(self):
        with self.assertRaises(ValueError):
            services.cram_shot(self.db, self.user, "u_不属于你")

    def test_cram_reset_brings_everything_back(self):
        plan = services.cram_plan(self.db, self.user, limit=40)
        for item in plan["items"][:3]:
            services.cram_shot(self.db, self.user, item["unit_id"])
        self.assertEqual(services.cram_plan(self.db, self.user)["left"], plan["left"] - 3)
        out = services.cram_reset(self.db, self.user)
        self.assertEqual(out["removed"], 3)
        self.assertEqual(services.cram_plan(self.db, self.user)["left"], plan["left"])

    def test_cram_shot_accepts_real_long_unit_ids(self):
        """演示包的 unit id 恰好 63 字符，截断 bug 测不出来；用户材料的标题长，id 能到 96+。

        这条测试盯的是"发出去的 id 必须还能用"：按定长截断会把合法 id 变成查无此单元，
        然后回一句"这个单元不在你的目标里"，看起来像产品的错。
        """
        services.onboarding(self.db, {"name": "长标题", "pack_id": PACK,
                                      "goal_title": "另一个目标", "daily_minutes": 20,
                                      "deadline": "2026-12-31"})
        long_id = "u_" + "x" * 40 + "_" + "提取练习比重新阅读更有效-合意困难-难受不等于没用" * 2
        goal = self.db.one("SELECT id FROM goals WHERE user_id=? ORDER BY created_at DESC", (self.user,))
        self.db.insert("units", {
            "id": long_id, "goal_id": goal["id"], "pack_id": "test.long", "pack_unit_id": "x",
            "title": "长 id 单元", "essence": "本质", "difficulty": 3, "importance": 5,
            "estimated_minutes": 20, "order_index": 0, "created_at": services.now_iso(),
            "updated_at": services.now_iso(),
        })
        self.assertGreater(len(long_id), 80, "构造的 id 不够长，测不到这一类 bug")
        out = services.cram_shot(self.db, self.user, long_id)
        self.assertTrue(out["shot"])
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM cram_shots WHERE target_id=?", (long_id,), 0), 1)

    # ---------------- 自测卷 ----------------
    def test_exam_uses_only_compiled_probes_and_caps_per_unit(self):
        exam = services.exam_build(self.db, self.user, minutes=40, count=10)
        self.assertLessEqual(len(exam["questions"]), 10)
        per_unit: dict[str, int] = {}
        for q in exam["questions"]:
            per_unit[q["unit_id"]] = per_unit.get(q["unit_id"], 0) + 1
            self.assertTrue(q["prompt"])
            self.assertNotIn("answer", q, "题目响应里就带了答案")
        self.assertTrue(all(n <= 2 for n in per_unit.values()), per_unit)
        self.assertGreater(len(per_unit), 1, "一份卷子只考了一个单元")

    def test_exam_answers_are_hidden_until_build_and_come_back_on_grade(self):
        exam = services.exam_build(self.db, self.user, minutes=10, count=3)
        for question in exam["questions"]:
            self.assertNotIn("answer", question)
            self.assertNotIn("rubric", question)
        key = {k["id"]: k for k in json.loads(
            self.db.one("SELECT answer_key_json FROM exams WHERE id=?", (exam["exam_id"],))
            ["answer_key_json"])}
        probe_id = exam["questions"][0]["id"]
        graded = services.exam_grade(self.db, self.user, exam["exam_id"],
                                     [{"id": probe_id, "text": key[probe_id]["answer"]}])
        first = graded["result"]["items"][0]
        self.assertEqual(first["verdict"], "correct", f"照抄标准答案还不算对：{first}")
        self.assertTrue(first["answer"], "判分后不给标准答案，错题本怎么看")

    def test_exam_grading_distinguishes_blank_and_ungraded(self):
        """没写 = blank（算 0 分）；没有评分要点 = ungraded（不算判过，也不给分）。"""
        exam = services.exam_build(self.db, self.user, minutes=10, count=4)
        qs = exam["questions"]
        graded = services.exam_grade(self.db, self.user, exam["exam_id"], [
            {"id": qs[0]["id"], "text": "与答案完全无关的一段话，猫狗桌椅"},
            {"id": qs[1]["id"], "text": "   "},
        ])
        counts = graded["result"]["counts"]
        self.assertGreaterEqual(counts["blank"], 1)
        verdicts = {i["id"]: i["verdict"] for i in graded["result"]["items"]}
        self.assertEqual(verdicts[qs[1]["id"]], "blank")
        self.assertIn(verdicts[qs[0]["id"]], ("wrong", "partial", "ungraded"))
        for item in graded["result"]["items"]:
            if item["verdict"] == "ungraded":
                self.assertIsNone(item["score"], "判不动的题不能编一个分数出来")

    def test_exam_cannot_be_submitted_twice(self):
        exam = services.exam_build(self.db, self.user, minutes=10, count=3)
        services.exam_grade(self.db, self.user, exam["exam_id"], [])
        with self.assertRaises(ValueError):
            services.exam_grade(self.db, self.user, exam["exam_id"], [])

    def test_exam_submit_records_attempts_and_study_time(self):
        before = self.db.scalar("SELECT COALESCE(SUM(minutes),0) FROM study_days WHERE user_id=?",
                                (self.user,), 0)
        exam = services.exam_build(self.db, self.user, minutes=10, count=3)
        services.exam_grade(self.db, self.user, exam["exam_id"],
                            [{"id": exam["questions"][0]["id"], "text": "模仿 + 练习 → 可提取记忆"}])
        self.assertEqual(self.db.scalar(
            "SELECT COUNT(*) FROM attempts WHERE user_id=? AND kind='exam'", (self.user,), 0), 1)
        self.assertGreaterEqual(self.db.scalar(
            "SELECT COALESCE(SUM(minutes),0) FROM study_days WHERE user_id=?", (self.user,), 0), before)

    def test_exam_wrong_answers_surface_in_the_mistake_book(self):
        exam = services.exam_build(self.db, self.user, minutes=10, count=4)
        services.exam_grade(self.db, self.user, exam["exam_id"], [
            {"id": exam["questions"][0]["id"], "text": "完全不对的答案 猫"}])
        book = services.mistake_book(self.db, self.user)
        self.assertTrue(book["exam_items"], "卷子上丢分的题没进错题本")
        self.assertTrue(book["totals"]["exam_misses"])

    def test_exam_without_probes_or_goal_fails_readable(self):
        with self.assertRaises(ValueError):
            services.exam_build(self.db, "no-such-user")

    def test_exam_bounds_are_clamped(self):
        exam = services.exam_build(self.db, self.user, minutes=9999, count=9999)
        self.assertEqual(exam["minutes"], 180)
        self.assertLessEqual(len(exam["questions"]), 25)


class HttpTest(unittest.TestCase):
    """新端点的对外契约：形状、错误码、以及"日志里看不到密钥"。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-sprint-http-")
        base = Path(cls.tmp.name)
        cls.old = {k: getattr(config, k) for k in
                   ("DATA_DIR", "PACKS_DIR", "LLM_CONFIG_PATH", "LLM_STATE_PATH", "TOKEN")}
        config.DATA_DIR = base
        config.PACKS_DIR = base / "packs"
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        config.TOKEN = ""
        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        cls.db = Database(base / "http.db")
        Handler.app = App(cls.db)
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        # 目标在 setUpClass 里开：测试之间不依赖"谁先跑过"，不然先跑的用例只会拿到
        # "还没有目标"的 400，看起来像产品坏了
        services.onboarding(cls.db, {"name": "考生", "pack_id": PACK, "goal_title": "期末",
                                     "daily_minutes": 30, "deadline": "2026-11-01"})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.db.close()
        Handler.app = None
        for key, value in cls.old.items():
            setattr(config, key, value)
        cls.tmp.cleanup()

    def call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {},
                                     method=method)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def test_new_read_endpoints(self):
        for path in ("/api/mistakes", "/api/cram", "/api/exams"):
            out = self.call("GET", path)
            self.assertTrue(out["ok"], path)
        cram = self.call("GET", "/api/cram")["data"]
        self.assertEqual(cram["total"], 8)
        self.assertIn("days_left", cram)

    def test_shot_and_reset_roundtrip(self):
        unit_id = self.call("GET", "/api/cram")["data"]["items"][0]["unit_id"]
        left_before = self.call("GET", "/api/cram")["data"]["left"]
        self.assertTrue(self.call("POST", "/api/cram/shot", {"unit_id": unit_id})["data"]["shot"])
        self.assertEqual(self.call("GET", "/api/cram")["data"]["left"], left_before - 1)
        self.assertEqual(self.call("POST", "/api/cram/reset", {})["data"]["removed"], 1)

    def test_shot_rejects_foreign_unit_with_readable_400(self):
        try:
            self.call("POST", "/api/cram/shot", {"unit_id": "u_不存在"})
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
            self.assertEqual(exc.code, 400)
            self.assertTrue(body["error"]["message"])
        else:
            self.fail("不属于本目标的单元被接受了")

    def test_exam_build_and_submit_over_http(self):
        exam = self.call("POST", "/api/exam", {"minutes": 15, "count": 3})["data"]
        questions = exam["questions"]
        self.assertEqual(len(questions), 3)
        graded = self.call("POST", "/api/exam/submit", {
            "exam_id": exam["exam_id"],
            "answers": [{"id": questions[0]["id"], "text": "模仿 + 练习，形成可提取的记忆"}]})["data"]
        self.assertEqual(graded["result"]["graded_by"], "rules")
        listed = self.call("GET", "/api/exams")["data"]["exams"]
        self.assertEqual(listed[0]["status"], "graded")

    def test_exam_rejects_non_list_answers(self):
        exam = self.call("POST", "/api/exam", {"minutes": 10, "count": 3})["data"]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call("POST", "/api/exam/submit", {"exam_id": exam["exam_id"], "answers": "nope"})
        self.assertEqual(caught.exception.code, 400)

    def test_review_ids_param_is_the_practice_mode(self):
        card = self.call("GET", "/api/review?limit=1")["data"]["cards"][0]
        out = self.call("GET", "/api/review?ids=" + urllib.parse.quote(card["id"]))["data"]
        self.assertEqual(out["picked_from"], "mistakes")
        self.assertEqual(out["cards"][0]["due_label"], "错题重练")

    def test_redact_path_never_logs_secret_values(self):
        self.assertEqual(redact_path("/api/settings/llm?api_key=sk-very-real"),
                         "/api/settings/llm?api_key=***")
        self.assertIn("sk-very-real", redact_path("/api/search?q=sk-very-real"),
                      "普通查询值不该被当成密钥")


if __name__ == "__main__":
    unittest.main()
