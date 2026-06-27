import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.aliases import alias_tokens, load_aliases
from codekb.models import RawDocument
from codekb.pipeline import ingest_raw_document
from codekb.retrieval import Bm25LiteRetriever, tokenize
from codekb.store import InMemoryAtomStore


ROOT = Path(__file__).resolve().parents[1]


class AliasTests(unittest.TestCase):
    def test_load_aliases(self):
        aliases = load_aliases(ROOT / "data" / "entity_aliases.yaml")

        self.assertIn("先遣", aliases)
        self.assertIn("xianqian", aliases["先遣"])

    def test_alias_tokens(self):
        aliases = {"loading卡死": ("进局卡住",)}

        self.assertEqual(alias_tokens("玩家进局卡住", aliases), ["alias:loading卡死"])

    def test_retrieval_uses_aliases(self):
        store = InMemoryAtomStore()
        raw = RawDocument(
            docid="incident-1",
            title="问题复盘",
            content_type="DOC",
            body="## 现象\n\n峡谷表现为从局外匹配后进局内 loading 卡死。",
        )
        ingest_raw_document(raw, sub_kb_id="incident", store=store)

        no_alias = Bm25LiteRetriever(store).retrieve("进局卡住", sub_kbs={"incident"})
        with_alias = Bm25LiteRetriever(
            store,
            aliases={"loading卡死": ("进局卡住", "loading 卡死")},
        ).retrieve("进局卡住", sub_kbs={"incident"})

        self.assertGreaterEqual(with_alias.top_atoms[0].score, no_alias.top_atoms[0].score)
        self.assertIn("alias:loading卡死", tokenize("进局卡住", aliases={"loading卡死": ("进局卡住",)}))


if __name__ == "__main__":
    unittest.main()

