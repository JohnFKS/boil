from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import markdown_fix


class FixTest(unittest.TestCase):
    def fix(self, text, options=None):
        return markdown_fix.fix_markdown(text, options)

    def test_word_bullets_become_markdown_lists(self):
        out, applied = self.fix("要点一\n• 提取练习\n● 间隔复习\n－ 交错练习")
        self.assertIn("- 提取练习", out)
        self.assertIn("- 间隔复习", out)
        self.assertIn("- 交错练习", out)
        self.assertIn("项目符号归一为 -", applied)

    def test_full_width_space_makes_heading_valid(self):
        # 全角空格转半角后标题本来就合法，所以不该额外报「标题补空格」
        out, applied = self.fix("#　极限的定义\n极限是　趋近过程。")
        self.assertIn("# 极限的定义", out)
        self.assertNotIn("　", out)
        self.assertIn("全角空格转半角", applied)
        self.assertNotIn("标题补空格", applied)

    def test_heading_without_any_space_is_fixed(self):
        out, applied = self.fix("#极限的定义")
        self.assertEqual(out, "# 极限的定义")
        self.assertIn("标题补空格", applied)

    def test_heading_without_space_is_fixed(self):
        out, _ = self.fix("##连续与可导")
        self.assertEqual(out.splitlines()[0], "## 连续与可导")

    def test_math_delimiters_outside_code(self):
        out, applied = self.fix("公式 \\[x^2 + y^2\\] 与 \\(a+b\\) 出现。")
        self.assertIn("$$x^2 + y^2$$", out)
        self.assertIn("$a+b$", out)
        self.assertIn("LaTeX 定界符转为 $/$$", applied)

    def test_math_inside_code_is_untouched(self):
        src = "```\\[x\\]```\n行内 `\\[y\\]` 保持原样\n正文 \\[z\\]"
        out, _ = self.fix(src)
        self.assertIn("```\\[x\\]```", out)
        self.assertIn("`\\[y\\]`", out)
        self.assertIn("$$z$$", out)

    def test_newlines_and_trailing_space(self):
        out, applied = self.fix("甲  \r\n乙\r丙\n\n\n\n\n丁")
        self.assertIn("甲\n乙", out)
        self.assertFalse(any(line != line.rstrip() for line in out.split("\n")))
        self.assertIn("换行统一为 LF", applied)
        self.assertIn("去掉行尾空格", applied)

    def test_zero_width_removed(self):
        out, applied = self.fix("提取\u200b练习\ufeff")
        self.assertEqual(out, "提取练习")
        self.assertIn("去掉零宽字符", applied)

    def test_numbered_without_space(self):
        out, _ = self.fix("1.第一步\n2、第二步")
        self.assertEqual(out.splitlines()[0], "1. 第一步")
        self.assertEqual(out.splitlines()[1], "2. 第二步")

    def test_empty_code_fence_removed(self):
        out, applied = self.fix("正文\n```\n```\n更多")
        self.assertNotIn("```\n```", out)
        self.assertIn("删掉空代码围栏", applied)

    def test_options_can_disable(self):
        out, applied = self.fix("• 项目", {"bullets": False})
        self.assertTrue(out.startswith("•"))
        self.assertEqual(applied, [])

    def test_empty_input(self):
        self.assertEqual(self.fix("   \n  "), ("", []))

    def test_content_is_never_invented_or_dropped(self):
        src = "# 主题\n这是核心结论，包含数字 3.14 与 $x$。\n- 步骤一\n- 步骤二"
        out, _ = self.fix(src)
        for token in ("主题", "核心结论", "3.14", "$x$", "步骤一", "步骤二"):
            self.assertIn(token, out)

    def test_unknown_option_key_is_ignored(self):
        out, applied = self.fix("• 项目", {"nonexistent": True})
        self.assertIn("- 项目", out)


if __name__ == "__main__":
    unittest.main()
