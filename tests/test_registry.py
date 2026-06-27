import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.registry import load_registry


class RegistryTests(unittest.TestCase):
    def test_load_registry(self):
        registry = load_registry(Path(__file__).resolve().parents[1] / "docs" / "kb-registry.draft.yaml")

        self.assertEqual(registry.version, "0.1")
        self.assertEqual(registry.defaults.rerank_top_k, 4)
        self.assertEqual({kb.id for kb in registry.pilot_sub_kbs()}, {"release", "testing", "incident"})
        self.assertEqual(registry.get_sub_kb("testing").source_docs[0].docid, "1000000014")


if __name__ == "__main__":
    unittest.main()

