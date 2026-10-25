"""仓库体检：大文件与密钥文件不该出现在推送里。

起因很实在：测试崩溃时在仓库根目录留下过两个进程内存转储（`core.277173` 391 MB、
`core.278606` 936 MB）。`git add .` 一次就能把 1.3 GB 推上 GitHub —— 而崩溃转储里
可能带着内存中的 API Key。`.gitignore` 写了 `core.*` 只是第一层；这一条是第二层：
**跟踪列表里出现超限文件就直接失败**，不用等到 push 之后才发现。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 截图是最大的合法文件（实测单张 ≤1.3 MB），2 MB 既留了余量又拦得住转储/压缩包
MAX_TRACKED_BYTES = 2 * 1024 * 1024
# 崩溃转储、日志、真实数据目录：这些一旦出现就该被忽略，而不是被提交
MUST_IGNORE = ("core.*", "*.log", "data/", "__pycache__/")
SECRET_NAMES = ("llm.json", "embedding.json", ".env", "id_rsa", ".pem")


def tracked_files() -> list[str] | None:
    """git 不可用（比如打包分发时解压跑测试）就返回 None，让用例自己跳过。"""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT),
                             capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in out.stdout.decode("utf-8", "replace").split("\0") if name]


class RepoHygieneTest(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()
        if self.files is None:
            self.skipTest("不在 git 仓库里（源码包），体检跳过")

    def test_no_oversized_file_is_tracked(self):
        big, missing, checked = [], [], 0
        for name in self.files:
            path = ROOT / name
            if not path.is_file():
                missing.append(name)
                continue
            checked += 1
            if path.stat().st_size > MAX_TRACKED_BYTES:
                big.append((name, path.stat().st_size))
        # 空跑不能算通过：这条守卫必须真的量过整个跟踪列表
        self.assertGreater(checked, 50,
                           f"只量到 {checked} 个文件（缺失 {missing[:3]}），守卫基本没起作用")
        self.assertEqual([], sorted(big),
                         f"这些文件超过 {MAX_TRACKED_BYTES // 1024 // 1024} MB，别把它们推上去："
                         f"{[(n, f'{s / 1024 / 1024:.1f}MB') for n, s in big]}"
                         "；崩溃转储请交给 .gitignore 的 core.*")

    def test_junk_and_secrets_stay_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in MUST_IGNORE:
            self.assertIn(pattern, ignore, f".gitignore 少了 {pattern}")
        checked = {name.lower() for name in self.files}
        for name in self.files:
            leaf = name.rsplit("/", 1)[-1]
            self.assertFalse(leaf.startswith("core."), f"崩溃转储被跟踪了：{name}")
            self.assertNotIn(leaf, {"llm.json", "embedding.json"},
                             f"密钥配置文件被跟踪了：{name}")
            self.assertFalse(leaf.endswith(".pem") or leaf == ".env",
                             f"疑似凭据文件被跟踪了：{name}")
        self.assertTrue(checked, "跟踪列表为空？那这条守卫没在起作用")

    def test_no_secret_looking_path_is_tracked(self):
        hits = [name for name in self.files
                if any(token in name for token in SECRET_NAMES)]
        self.assertEqual([], hits, f"这些路径像存密钥，不该进仓库：{hits}")


if __name__ == "__main__":
    unittest.main()
