from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boil import embeddings


class EmbeddingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="boil-emb-local-")
        base = Path(cls._tmp.name)
        cls._old = (embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE)
        embeddings._CONFIG_OVERRIDE = base / "embedding.json"
        embeddings._CACHE_OVERRIDE = base / "embedding_cache.json"

    @classmethod
    def tearDownClass(cls):
        embeddings._CONFIG_OVERRIDE, embeddings._CACHE_OVERRIDE = cls._old
        cls._tmp.cleanup()

    def test_deterministic_and_related(self):
        a = embeddings.embed("极限与连续")
        b = embeddings.embed("极限与连续")
        c = embeddings.embed("矩阵的秩")
        self.assertEqual(a, b)
        self.assertTrue(0.0 <= embeddings.cosine(a, b) <= 1.001)
        units = [
            {"id": "limit", "title": "极限与连续", "essence": "极限是趋近过程，连续要求极限等于函数值。"},
            {"id": "matrix", "title": "矩阵的秩", "essence": "秩是线性无关列的最大数量。"},
            {"id": "memory", "title": "记忆", "essence": "学习是形成可提取的记忆。"},
        ]
        rel = embeddings.related_units(units[0], units, 2)
        self.assertTrue(rel)
        self.assertNotEqual(rel[0]["id"], "limit")
        ranked = embeddings.rank("矩阵的秩", units, embeddings.unit_text)
        self.assertEqual(ranked[0][1]["id"], "matrix")

    def test_semantic_groups(self):
        paragraphs = [
            "极限是趋近过程，不是到达。连续要求极限存在且等于函数值。",
            "导数描述函数的变化率，可导必连续。",
            "矩阵的秩描述线性无关列的最大数量，行列式判断可逆。",
            "行列式为零则矩阵不可逆。",
        ]
        groups = embeddings.semantic_groups(paragraphs, max_size=2, threshold=0.12)
        self.assertGreaterEqual(len(groups), 2)
        self.assertEqual(sum(len(g) for g in groups), len(paragraphs))


class MathPackTest(unittest.TestCase):
    def test_math_pack_has_formulas_and_katex_assets(self):
        import json
        pack = json.loads((ROOT / "content/packs/math-calculus-ch5.json").read_text(encoding="utf-8"))
        formula_units = [u for u in pack["units"] if "$" in (u.get("essence") or "")]
        self.assertGreaterEqual(len(formula_units), 3)
        self.assertTrue((ROOT / "static/vendor/katex/katex.min.js").exists())
        self.assertTrue((ROOT / "static/vendor/katex/katex.min.css").exists())
        self.assertTrue((ROOT / "static/vendor/katex/fonts").is_dir())


if __name__ == "__main__":
    unittest.main()
