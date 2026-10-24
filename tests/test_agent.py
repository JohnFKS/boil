"""全局 agent：能力白名单、参数校验、危险动作闸门、文档检索与离线回退。

这里最重要的不是"模型能做什么"，而是"模型说什么都不能越界"——
所以大量用例是恶意的模型输出。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import agent, config, embeddings, llm  # noqa: E402
from boil.db import Database  # noqa: E402


class CapabilityRegistryTest(unittest.TestCase):
    def test_every_capability_is_well_formed(self):
        seen = set()
        for cap in agent.CAPABILITIES:
            with self.subTest(cap["id"]):
                self.assertNotIn(cap["id"], seen, "能力 id 不能重复")
                seen.add(cap["id"])
                self.assertIn(cap["risk"], ("read", "write", "danger"))
                self.assertIn(cap["kind"], ("api", "ui"))
                self.assertTrue(cap["desc"])
                self.assertLessEqual(len(cap["desc"]), 80)
                for name, spec in cap["args"].items():
                    self.assertTrue(agent._ARG_NAME.match(name), name)
                    self.assertIn(spec.rstrip("?"),
                                  ("str", "int", "bool", "rating", "minutes", "route", "confirm_reset"), spec)
                if cap["kind"] == "api":
                    self.assertTrue(cap["path"].startswith("/api/"), cap["path"])
                    for name in re.findall(r"\{(\w+)\}", cap["path"]):
                        self.assertIn(name, cap["args"], f"{cap['id']} 路径里的 {name} 没有声明参数")
                else:
                    self.assertIn(cap["action"], ("navigate", "open_unit", "open_search"))

    def test_no_capability_touches_secrets(self):
        blob = json.dumps(agent.capabilities(), ensure_ascii=False)
        for forbidden in ("settings/llm", "settings/embeddings", "api_key", "data/llm.json"):
            self.assertNotIn(forbidden, blob)

    def test_danger_list_is_short_and_explicit(self):
        dangers = [c["id"] for c in agent.CAPABILITIES if c["risk"] == "danger"]
        self.assertEqual(dangers, ["data.reset"])


class ValidateActionTest(unittest.TestCase):
    def test_unknown_action_is_refused(self):
        out = agent.validate_action({"id": "shell.exec", "args": {"cmd": "rm -rf /"}})
        self.assertFalse(out["ok"])
        self.assertIn("不在能力清单里", out["reason"])

    def test_non_dict_and_missing_id(self):
        self.assertFalse(agent.validate_action("rm -rf")["ok"])
        self.assertFalse(agent.validate_action({"args": {}})["ok"])
        self.assertFalse(agent.validate_action({"id": "view.map", "args": "not-a-dict"})["ok"])

    def test_rating_must_be_in_range(self):
        good = agent.validate_action({"id": "review.rate", "args": {"card_id": "c1", "rating": 3}})
        self.assertTrue(good["ok"])
        self.assertEqual(good["path"], "/api/review/{card_id}")
        self.assertEqual(good["path_args"], ["card_id"])
        for bad in (0, 5, "很简単", None):
            out = agent.validate_action({"id": "review.rate", "args": {"card_id": "c1", "rating": bad}})
            self.assertFalse(out["ok"], bad)

    def test_minutes_are_limited_to_the_supported_presets(self):
        self.assertTrue(agent.validate_action({"id": "focus.start", "args": {"minutes": 25}})["ok"])
        self.assertFalse(agent.validate_action({"id": "focus.start", "args": {"minutes": 7}})["ok"])

    def test_route_names_are_validated(self):
        self.assertTrue(agent.validate_action({"id": "go.route", "args": {"route": "#/map"}})["ok"])
        self.assertFalse(agent.validate_action({"id": "go.route", "args": {"route": "../../etc"}})["ok"])

    def test_danger_requires_typed_confirm_and_flags_itself(self):
        out = agent.validate_action({"id": "data.reset", "args": {"confirm": "RESET"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["risk"], "danger")
        self.assertTrue(out["requires_typed_confirm"])
        # 模型自己填的确认词会被抹掉：动作照样提议，但按钮只能由人亲手解锁
        self.assertEqual(agent.validate_action({"id": "data.reset", "args": {}})["args"], {})
        sneaky = agent.validate_action({"id": "data.reset", "args": {"confirm": "RESET"}})
        self.assertTrue(sneaky["ok"])
        self.assertEqual(sneaky["args"], {})
        self.assertEqual(sneaky["confirm_suppressed"], ["confirm"])
        self.assertNotIn('"confirm": "RESET"', json.dumps(sneaky, ensure_ascii=False))

    def test_write_without_why_is_flagged(self):
        out = agent.validate_action({"id": "note.create", "args": {"content": "一句话"}})
        self.assertTrue(out["ok"])
        self.assertIn("why_missing", out)

    def test_extra_args_are_reported_not_silently_used(self):
        out = agent.validate_action({"id": "view.search", "args": {"q": "复习", "limit": 9999}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["ignored_args"], ["limit"])
        self.assertNotIn("limit", out["args"])

    def test_path_args_are_filled_and_sanitised(self):
        out = agent.validate_action({"id": "view.unit", "args": {"unit_id": "u/../x"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["args"]["unit_id"], "u/../x")   # 原样保留，编码交给前端 encodeURIComponent
        self.assertEqual(out["path"], "/api/units/{unit_id}")
        self.assertNotIn("..", out["path"], "路径模板本身不许被参数改写")

    def test_missing_path_argument_is_refused(self):
        out = agent.validate_action({"id": "units.unmerge", "args": {}})
        self.assertFalse(out["ok"])

    def test_long_strings_are_capped(self):
        out = agent.validate_action({"id": "note.create", "args": {"content": "字" * 5000}})
        self.assertTrue(out["ok"])
        self.assertLessEqual(len(out["args"]["content"]), 600)


class DocSearchTest(unittest.TestCase):
    """检索用例必须离线：不然每条断言都在赌云端 embedding 的脾气，还会真花钱。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-agent-docs-")
        cls.old_emb = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        base = Path(cls.tmp.name)
        (base / "embedding.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "cache.json"

    @classmethod
    def tearDownClass(cls):
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = cls.old_emb
        cls.tmp.cleanup()

    def test_index_is_built_from_repo_docs(self):
        chunks = agent.doc_chunks()
        self.assertGreater(len(chunks), 50)
        sources = {c["source"] for c in chunks}
        self.assertIn("API.md", sources)
        self.assertIn("CHANGELOG.md", sources)

    def test_questions_land_on_the_right_section(self):
        best = agent.search_docs("合并单元会不会丢内容", limit=3)
        self.assertTrue(best)
        self.assertTrue(any("合并" in b["section"] for b in best), [b["section"] for b in best])

    def test_endpoint_questions_find_the_endpoint(self):
        hits = agent.search_docs("GET /api/releases 返回什么", limit=3)
        self.assertTrue(any("releases" in h["section"] for h in hits), [h["section"] for h in hits])

    def test_garbage_question_returns_nothing(self):
        self.assertEqual(agent.search_docs("喵呜喵呜喵呜喵喵喵", limit=3), [])

    def test_short_question_cannot_sneak_in_by_accidental_bigrams(self):
        # 「我还有几张卡到期」不该把「划词菜单」那种段落顶上来
        hits = agent.search_docs("我还有几张卡到期", limit=2)
        self.assertTrue(all(("卡" in h["section"] or "复习" in h["section"] or "review" in h["text"])
                            or h["lexical"] >= 1.5 for h in hits))

    def test_index_rebuilds_when_a_document_changes(self):
        before = agent.doc_chunks()
        stamp = agent._INDEX["stamp"]
        real = agent.DOC_FILES
        try:
            agent.DOC_FILES = ("README.md", "API.md")
            agent._INDEX["stamp"] = ""
            self.assertNotEqual(agent._INDEX["stamp"], stamp)
            self.assertTrue(agent.doc_chunks())
        finally:
            agent.DOC_FILES = real
            agent._INDEX["stamp"] = ""
            self.assertGreaterEqual(len(agent.doc_chunks()), len(before))


class IsolatedLlmConfig(unittest.TestCase):
    """测试绝不能写本机 data/llm.json —— 那里存着真实密钥。

    真踩过：`llm.set_config({"api_key": ""})` 在 tearDown 里把开发机上的真 key 清成了空串。
    所以路径重定向写在基类里，并且断言它生效，而不是指望每个测试自己记得。
    （`make test` 与 CI 另外把 BOIL_DATA_DIR 指到临时目录，做第二层保险。）
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="boil-agent-cfg-")
        base = Path(self._tmp.name)
        self._old = {
            "DATA_DIR": config.DATA_DIR,
            "LLM_CONFIG_PATH": config.LLM_CONFIG_PATH,
            "LLM_STATE_PATH": config.LLM_STATE_PATH,
            "EMB_CONFIG": embeddings._CONFIG_OVERRIDE,
            "EMB_CACHE": embeddings._CACHE_OVERRIDE,
        }
        config.DATA_DIR = base
        config.LLM_CONFIG_PATH = base / "llm.json"
        config.LLM_STATE_PATH = base / "llm_state.json"
        (base / "embedding.json").write_text('{"enabled": false}', encoding="utf-8")
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"
        self.assertIn("boil-agent-cfg-", str(config.LLM_CONFIG_PATH))
        super().setUp()

    def tearDown(self):
        for key, value in self._old.items():
            setattr(config, key, value)
        self._tmp.cleanup()
        super().tearDown()

    @staticmethod
    def nonsense():
        """造一个确定不在语料里的词 —— 用写死的"呜啦啦"会被我自己的文档收留。"""
        blob = " ".join(c["text"] + c["section"] for c in agent.doc_chunks())
        for candidate in ("呜啦啦咕噜噜", "叽里噜咕噜", "噼啪噜噜噼"):
            if candidate not in blob:
                return candidate
        return "ｆｇｈｊｋｌ"


class PlanOfflineTest(IsolatedLlmConfig):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="boil-agent-")
        cls.db = Database(Path(cls.tmp.name) / "a.db")
        from boil import services
        services.ensure_user(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def setUp(self):
        self._old = llm.enabled()
        llm.set_config({"api_key": ""})

    def tearDown(self):
        if not self._old:
            llm.set_config({"api_key": ""})

    def plan(self, q):
        return agent.plan(self.db, "local", q)

    def test_help_lists_the_three_things_it_can_do(self):
        out = self.plan("你能做什么")
        self.assertEqual(out["engine"], "rules")
        for word in ("查", "回答", "执行"):
            self.assertIn(word, out["reply"])

    def test_navigation_request_produces_one_ui_action(self):
        out = self.plan("打开地图")
        self.assertEqual([a["id"] for a in out["actions"]], ["go.route"])
        self.assertEqual(out["actions"][0]["args"], {"route": "map"})
        self.assertFalse(out["citations"], "纯指令不该附带文档讲座")

    def test_concept_question_quotes_a_document_with_source(self):
        out = self.plan("记忆健康度是怎么算的")
        self.assertTrue(out["citations"])
        self.assertIn("记忆健康度", out["reply"])

    def test_data_question_without_goal_says_so_instead_of_inventing_numbers(self):
        out = self.plan("我还有几张卡到期")
        self.assertIn("还没有学习目标", out["reply"])
        self.assertNotIn("None", out["reply"])

    def test_unmatched_question_admits_it(self):
        out = self.plan(self.nonsense())
        self.assertIn("没对上", out["reply"])

    def test_plan_requires_a_question(self):
        with self.assertRaises(ValueError):
            agent.plan(self.db, "local", "   ")


class PlanModelTest(IsolatedLlmConfig):
    """模型输出的形状由代码负责兜住：坏 JSON、超纲动作、编造出处都不能变成执行。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="boil-agent-")
        self.db = Database(Path(self.tmp.name) / "a.db")
        from boil import services
        services.ensure_user(self.db)
        self._old_enabled = llm.enabled()
        llm.set_config({"api_key": "sk-test-not-a-real-key"})

    def tearDown(self):
        llm.set_config({"api_key": ""})
        self.db.close()
        self.tmp.cleanup()

    def _patch(self, value, raises=None):
        original = llm.chat_json

        def fake(messages, *args, **kwargs):
            if raises:
                raise raises
            return value

        llm.chat_json = fake
        self.addCleanup(lambda: setattr(llm, "chat_json", original))

    def test_out_of_scope_action_is_rejected_not_executed(self):
        self._patch({"reply": "好的", "actions": [
            {"id": "settings.write", "args": {"api_key": "sk-steal"}, "why": "换模型"},
            {"id": "view.map", "args": {}, "why": "看看地图"},
        ]})
        out = agent.plan(self.db, "local", "帮我把地图打开")
        self.assertEqual([a["id"] for a in out["actions"]], ["view.map"])
        self.assertEqual(len(out["rejected"]), 1)
        self.assertIn("不在能力清单里", out["rejected"][0]["reason"])
        self.assertNotIn("sk-steal", json.dumps(out, ensure_ascii=False))

    def test_invented_citation_is_dropped_and_said_so(self):
        self._patch({"reply": "按 SECRET.md 说的", "actions": [], "citations": ["SECRET.md · 不存在"]})
        out = agent.plan(self.db, "local", "有没有一份 SECRET.md")
        self.assertEqual(out["citations"], [])
        self.assertTrue(any("找不到" in r["reason"] for r in out["rejected"]), out["rejected"])

    def test_action_overflow_is_capped(self):
        self._patch({"reply": "都给你排上", "actions": [
            {"id": "view.map", "args": {}, "why": f"第{i}步"} for i in range(20)]})
        out = agent.plan(self.db, "local", "把所有东西都看一遍")
        self.assertLessEqual(len(out["actions"]), agent.MAX_ACTIONS)

    def test_model_failure_degrades_to_rules_instead_of_a_500(self):
        self._patch(None, raises=llm.LLMError("网关 502"))
        out = agent.plan(self.db, "local", "记忆健康度是怎么算的")
        self.assertEqual(out["engine"], "rules")
        self.assertIn("模型这次没答上", out["reply"])
        self.assertTrue(out["ai_note"])

    def test_total_turns_are_capped(self):
        self._patch({"reply": "继续", "actions": [{"id": "view.map", "args": {}, "why": "看一眼"}]})
        out = agent.plan(self.db, "local", "然后呢", turn=agent.MAX_TURNS)
        self.assertFalse(out["can_continue"])

    def test_snapshot_never_leaks_the_key(self):
        self._patch({"reply": "ok", "actions": []})
        out = agent.plan(self.db, "local", "现在什么状态")
        self.assertNotIn("sk-test", json.dumps(out, ensure_ascii=False))
        self.assertIn("version", out["snapshot"])


if __name__ == "__main__":
    unittest.main()
