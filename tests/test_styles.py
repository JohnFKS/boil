from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import styles


class StylesTest(unittest.TestCase):
    def test_questions_shape_is_stable(self):
        payload = styles.questions_payload()
        self.assertEqual(len(payload["questions"]), 5)
        for q in payload["questions"]:
            self.assertTrue(q["text"])
            self.assertGreaterEqual(len(q["options"]), 3)
            for opt in q["options"]:
                self.assertTrue(opt["text"])
            # 前端只展示 text，不泄漏权重，否则会有暗示答案之嫌
            self.assertNotIn("weights", q["options"][0])
        self.assertEqual(len(payload["personas"]), len(styles.PERSONAS))

    def test_every_question_scores_every_persona_somewhere(self):
        covered = set()
        for q in styles.QUESTIONS:
            for opt in q["options"]:
                covered |= set(opt["weights"])
        self.assertEqual(covered, set(styles.PERSONAS), "有 persona 永远测不出来")

    def test_persona_is_deterministic_and_normalized(self):
        answers = [0, 1, 0, 3, 2]
        first = styles.compute_persona(answers)
        second = styles.compute_persona(answers)
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(first["scores"], second["scores"])
        self.assertAlmostEqual(sum(first["scores"].values()), 1.0, delta=0.01)
        self.assertTrue(first["label"])
        self.assertTrue(first["strategy"])
        self.assertEqual(first["answered"], 5)
        self.assertEqual(first["source"], "rules")

    def test_distinct_answers_lead_to_distinct_personas(self):
        keys = {styles.compute_persona(a)["key"] for a in [
            [1, 0, 0, 2, 0],
            [2, 2, 1, 1, 1],
            [3, 1, 3, 3, 3],
            [0, 3, 2, 0, 3],
        ]}
        self.assertGreater(len(keys), 2, "四种不同取向应该测出不同人设")

    def test_invalid_answers_rejected(self):
        for bad in ([], [0, 1], [0, 1, 2, 3, 9], [0, 1, 2, 3, "x"], None, "01234"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                styles.compute_persona(bad)

    def test_prompt_block_empty_without_result(self):
        self.assertEqual(styles.prompt_block(None), "")
        self.assertEqual(styles.prompt_block({}), "")
        self.assertEqual(styles.prompt_block({"strategy": ""}), "")
        block = styles.prompt_block({"label": "小步快跑型", "strategy": "拆得更碎",
                                     "secondary_label": "对比辨析型"})
        self.assertIn("小步快跑型", block)
        self.assertIn("次倾向：对比辨析型", block)
        self.assertIn("拆得更碎", block)


if __name__ == "__main__":
    unittest.main()
