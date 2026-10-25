"""跑完整套测试之后，全局配置必须还在原来的地方。

文件名以 zz 开头是有意的：`unittest discover` 按模块名排序，这个模块最后被导入、
最后被执行，于是它看到的是"所有用例都跑完之后的世界"。

为什么需要它：隔离基类的 setUp 是可以被子类跳过 super() 绕开的，绕开之后
`config.LLM_CONFIG_PATH` 会留在某个已删除的临时目录里 —— 下一个测试写配置就撞上
"目录没了"（3.10 与 3.12 的 GC 时机不同，所以这条只在 CI 的 3.10 job 上炸出来），
更糟的是在没有 BOIL_DATA_DIR 的开发机上，它可能直接写进真实的 data/llm.json。
这条守卫把"整套跑完不许留下被劫持的全局路径"变成断言，而不是靠人记得。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import config, embeddings  # noqa: E402

# 发现阶段会先导入所有测试模块再执行，所以这里抓到的仍是干净值
SNAPSHOT = {
    "DATA_DIR": str(config.DATA_DIR),
    "LLM_CONFIG_PATH": str(config.LLM_CONFIG_PATH),
    "LLM_STATE_PATH": str(config.LLM_STATE_PATH),
    "PACKS_DIR": str(config.PACKS_DIR),
    "EMB_CONFIG": str(embeddings._CONFIG_OVERRIDE) if embeddings._CONFIG_OVERRIDE else None,
    "EMB_CACHE": str(embeddings._CACHE_OVERRIDE) if embeddings._CACHE_OVERRIDE else None,
}


class ConfigPathLeakGuard(unittest.TestCase):
    def test_no_test_left_the_global_paths_hijacked(self):
        self.assertEqual(SNAPSHOT["DATA_DIR"], str(config.DATA_DIR))
        self.assertEqual(SNAPSHOT["LLM_CONFIG_PATH"], str(config.LLM_CONFIG_PATH))
        self.assertEqual(SNAPSHOT["LLM_STATE_PATH"], str(config.LLM_STATE_PATH))
        self.assertEqual(SNAPSHOT["PACKS_DIR"], str(config.PACKS_DIR))
        self.assertEqual(SNAPSHOT["EMB_CONFIG"],
                         str(embeddings._CONFIG_OVERRIDE) if embeddings._CONFIG_OVERRIDE else None)
        self.assertEqual(SNAPSHOT["EMB_CACHE"],
                         str(embeddings._CACHE_OVERRIDE) if embeddings._CACHE_OVERRIDE else None)

    def test_the_key_file_this_suite_might_write_is_not_the_real_one(self):
        """第二层保险：BOIL_DATA_DIR 没设时，允许写的那个 llm.json 不能是仓库里的 data/。"""
        target = Path(SNAPSHOT["LLM_CONFIG_PATH"])
        env = os.environ.get("BOIL_DATA_DIR")
        if env:
            self.assertIn(str(Path(env)), str(target), "设了 BOIL_DATA_DIR，可写路径就该落在它下面")
        repo_key = (ROOT / "data" / "llm.json").resolve()
        if target.exists() and target.resolve() == repo_key:
            self.fail(f"测试可写的 llm.json 指向了仓库里的真实密钥文件：{target}")


if __name__ == "__main__":
    unittest.main()
