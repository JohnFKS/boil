"""版本与更新日志：CHANGELOG.md 是网页上「版本 + 更新内容」的唯一来源，必须有测试兜住。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, releases  # noqa: E402

SHA = "a" * 40

SAMPLE = """# 更新日志

说明段落，不该被当成版本。

## [Unreleased]

- 下一条准备做的事

## [1.2.0] - 2026-11-01

一句话：这版加了 X。

### 新增

- 甲能力
- 乙能力，说明写在
  下一行也要拼回同一条
- 丙能力

### 修复

- 修了某个只在手机上出现的问题

## [1.1.0] - 2026-10-01

摘要二。

### 改进

- 丁改进
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.entries = releases.parse(SAMPLE)

    def test_headers_and_order_follow_the_file(self):
        self.assertEqual([r["version"] for r in self.entries], ["unreleased", "1.2.0", "1.1.0"])
        self.assertTrue(self.entries[0]["unreleased"])
        self.assertEqual([r["version"] for r in releases.released(self.entries)], ["1.2.0", "1.1.0"])

    def test_summary_date_and_groups(self):
        entry = self.entries[1]
        self.assertEqual(entry["date"], "2026-11-01")
        self.assertEqual(entry["summary"], "一句话：这版加了 X。")
        self.assertEqual([g["title"] for g in entry["groups"]], ["新增", "修复"])
        self.assertEqual([len(g["items"]) for g in entry["groups"]], [3, 1])

    def test_wrapped_bullet_is_joined_back(self):
        items = self.entries[1]["groups"][0]["items"]
        self.assertEqual(items[1], "乙能力，说明写在 下一行也要拼回同一条")

    def test_pending_lists_unreleased_items(self):
        self.assertEqual(releases.pending(self.entries), ["下一条准备做的事"])

    def test_intro_paragraph_is_not_a_release(self):
        self.assertNotIn("说明段落", " ".join(r["summary"] for r in self.entries))

    def test_group_without_leading_heading_becomes_updates(self):
        parsed = releases.parse("## [2.0.0] - 2027-01-01\n摘要\n- 直接列条目\n")
        self.assertEqual(parsed[0]["groups"][0]["title"], "更新")
        self.assertEqual(parsed[0]["groups"][0]["items"], ["直接列条目"])

    def test_h1_resets_context(self):
        parsed = releases.parse("## [2.0.0] - 2027-01-01\n摘要\n# 附录\n- 不属于任何版本\n")
        self.assertEqual(parsed[0]["items"], [])

    def test_versions_without_brackets_and_with_prerelease(self):
        parsed = releases.parse("## 2.0.0-rc.1 - 2027-01-01\n摘要\n### 新增\n- 甲\n")
        self.assertEqual(parsed[0]["version"], "2.0.0-rc.1")
        self.assertEqual(releases.version_key("2.0.0-rc.1"), (2, 0, 0))

    def test_code_fence_examples_are_not_releases(self):
        """日志里放一段「该怎么写」的示例很常见，但那不是第 9.9.9 版。"""
        text = SAMPLE + "\n```markdown\n## [9.9.9] - 2030-01-01\n示例\n### 新增\n- 示例条目\n```\n"
        parsed = releases.parse(text)
        self.assertNotIn("9.9.9", [r["version"] for r in parsed])
        self.assertEqual(releases.changelog_problems(text=text, releases=parsed), [])

    def test_missing_file_returns_empty_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(releases.load(Path(tmp) / "nope.md"), [])


class ProblemsTest(unittest.TestCase):
    def test_clean_sample_has_no_problems(self):
        self.assertEqual(releases.changelog_problems(text=SAMPLE,
                                                    releases=releases.parse(SAMPLE)), [])

    def test_missing_date_items_and_summary_are_reported(self):
        text = "## [9.9.9]\n"
        problems = releases.changelog_problems(text=text, releases=releases.parse(text))
        joined = " ".join(problems)
        self.assertIn("缺少日期", joined)
        self.assertIn("没有任何条目", joined)
        self.assertIn("缺少一句话摘要", joined)

    def test_empty_group_is_reported(self):
        text = "## [9.9.9] - 2026-01-01\n摘要\n### 新增\n\n## [9.8.0] - 2025-01-01\n摘要\n### 新增\n- 甲\n"
        problems = releases.changelog_problems(text=text, releases=releases.parse(text))
        self.assertTrue(any("分组「新增」是空的" in p for p in problems), problems)

    def test_duplicate_version_is_reported(self):
        text = ("## [9.9.9] - 2026-01-01\n摘要\n### 新增\n- 甲\n\n"
                "## [9.9.9] - 2026-01-01\n摘要\n### 新增\n- 乙\n")
        problems = releases.changelog_problems(text=text, releases=releases.parse(text))
        self.assertTrue(any("重复出现了 2 次" in p for p in problems), problems)

    def test_order_check_uses_numeric_versions_not_text(self):
        forward = ("## [1.10.0] - 2026-11-01\n摘要\n### 新增\n- 甲\n\n"
                   "## [1.9.0] - 2026-10-01\n摘要\n### 新增\n- 乙\n")
        self.assertEqual(releases.changelog_problems(text=forward, releases=releases.parse(forward)), [])
        backward = ("## [1.9.0] - 2026-10-01\n摘要\n### 新增\n- 乙\n\n"
                    "## [1.10.0] - 2026-11-01\n摘要\n### 新增\n- 甲\n")
        problems = releases.changelog_problems(text=backward, releases=releases.parse(backward))
        self.assertTrue(any("版本号却更大" in p for p in problems), problems)

    def test_unparseable_version_heading_is_reported(self):
        text = "## 版本 1.1.0（2026年10月24日）\n摘要\n### 新增\n- 甲\n"
        problems = releases.changelog_problems(text=text, releases=releases.parse(text))
        self.assertTrue(any("无法解析的版本标题" in p for p in problems), problems)

    def test_sample_logs_are_not_measured_against_config_version(self):
        """格式自检和版本自检是两件事：示例日志不该因为版本号被判红。"""
        self.assertEqual(releases.changelog_problems(text=SAMPLE, releases=releases.parse(SAMPLE)), [])
        self.assertTrue(any("config.VERSION" in p
                            for p in releases.version_problems(releases=releases.parse(SAMPLE))))

    def test_bumping_version_without_changelog_is_caught(self):
        """改了 config.VERSION 忘了写日志 —— 这条最容易忘，必须红。"""
        real = config.VERSION
        try:
            config.VERSION = "9.9.9"
            self.assertEqual(len(releases.version_problems()), 1)
            self.assertTrue(any("config.VERSION" in p for p in releases.all_problems()))
        finally:
            config.VERSION = real
        self.assertEqual(releases.version_problems(), [])

    def test_empty_file_reports_instead_of_crashing(self):
        problems = releases.changelog_problems(text="", releases=[])
        self.assertTrue(any("没解析出任何版本" in p for p in problems), problems)


class ShippedChangelogTest(unittest.TestCase):
    """仓库里真正给用户看的那份文件。"""

    def setUp(self):
        self.entries = releases.released()

    def test_no_format_problems(self):
        self.assertEqual(releases.changelog_problems(), [])

    def test_newest_release_is_the_running_version(self):
        self.assertEqual(config.VERSION, self.entries[0]["version"])

    def test_every_release_is_readable(self):
        self.assertGreaterEqual(len(self.entries), 2)
        for entry in self.entries:
            with self.subTest(version=entry["version"]):
                self.assertRegex(entry["date"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(entry["summary"].strip())
                self.assertGreaterEqual(len(entry["items"]), 3)
                for item in entry["items"]:
                    self.assertGreaterEqual(len(item), 8, f"{entry['version']} 有条目太短，没说清改了啥")
                    self.assertFalse(item.startswith(("**", "- ")), "条目不该混进 markdown 噪声")

    def test_current_release_announces_the_changelog_itself(self):
        items = " ".join(self.entries[0]["items"])
        self.assertIn("版本", items)


class BuildIdentityTest(unittest.TestCase):
    def _repo(self, head: str, refs: dict[str, str] | None = None, packed: str = ""):
        tmp = tempfile.TemporaryDirectory(prefix="boil-git-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        sha = SHA
        git = root / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text(head, encoding="utf-8")
        for name, value in (refs or {}).items():
            path = git / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{value}\n", encoding="utf-8")
        if packed:
            (git / "packed-refs").write_text(packed, encoding="utf-8")
        return root, sha

    def test_reads_branch_and_short_sha(self):
        root, sha = self._repo("ref: refs/heads/main\n", {"refs/heads/main": SHA})
        out = releases.build_identity(root)
        self.assertEqual(out["commit"], sha[:7])
        self.assertEqual(out["ref"], "refs/heads/main")
        self.assertTrue(out["source"])

    def test_falls_back_to_packed_refs(self):
        root, sha = self._repo("ref: refs/heads/main\n", {}, packed=f"{SHA} refs/heads/main\n")
        self.assertEqual(releases.build_identity(root)["commit"], sha[:7])

    def test_detached_head(self):
        root, sha = self._repo(f"{SHA}\n")
        out = releases.build_identity(root)
        self.assertEqual(out["commit"], sha[:7])
        self.assertEqual(out["ref"], "")

    def test_worktree_pointer_file(self):
        tmp = tempfile.TemporaryDirectory(prefix="boil-wt-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        worktree = root / "gitstore" / "worktrees" / "x"
        (worktree / "refs" / "heads").mkdir(parents=True)
        (worktree / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
        (worktree / "refs" / "heads" / "feature").write_text(f"{SHA}\n", encoding="utf-8")
        # 指针路径写成相对的，也要按仓库根解析，不能跟着进程当前目录跑
        (root / ".git").write_text("gitdir: gitstore/worktrees/x\n", encoding="utf-8")
        out = releases.build_identity(root)
        self.assertEqual(out["ref"], "refs/heads/feature")
        self.assertEqual(out["commit"], SHA[:7])
        self.assertTrue(out["source"])

    def test_missing_git_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = releases.build_identity(Path(tmp))
            self.assertEqual(out, {"commit": "", "ref": "", "source": False})

    def test_broken_head_does_not_invent_a_build(self):
        root, _ = self._repo("garbage\n")
        self.assertEqual(releases.build_identity(root)["commit"], "")

    def test_ref_path_cannot_escape_the_git_dir(self):
        secret = tempfile.TemporaryDirectory(prefix="boil-outside-")
        self.addCleanup(secret.cleanup)
        (Path(secret.name) / "evil").write_text("b" * 40, encoding="utf-8")
        root, _ = self._repo("ref: refs/heads/main\n", {})
        (root / ".git" / "HEAD").write_text(f"ref: {secret.name}/evil\n", encoding="utf-8")
        self.assertEqual(releases.build_identity(root)["commit"], "")


class PayloadTest(unittest.TestCase):
    def test_payload_shape(self):
        out = releases.payload()
        self.assertEqual(out["version"], config.VERSION)
        self.assertEqual(out["app"], config.APP_NAME)
        self.assertEqual(out["changelog_source"], "CHANGELOG.md")
        self.assertIsInstance(out["schema_version"], int)
        self.assertEqual(out["changelog_issues"], [])
        self.assertEqual(out["releases"][0]["version"], config.VERSION)
        self.assertTrue(out["released_at"])
        self.assertTrue(out["summary"])
        self.assertIsInstance(out["unreleased"], list)

    def test_payload_is_json_serialisable(self):
        import json
        json.dumps(releases.payload())


if __name__ == "__main__":
    unittest.main()
