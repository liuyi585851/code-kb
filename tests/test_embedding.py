import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.embedding import Embedder, HashedLexicalEmbedder
from codekb.retrieval import hashed_lexical_vector


class HashedLexicalEmbedderTests(unittest.TestCase):
    def test_embed_query_matches_hashed_lexical_vector_elementwise(self):
        embedder = HashedLexicalEmbedder()
        for text in [
            "wiki 发布流程是什么",
            "ISSUE_TRACKER 工单回写状态",
            "Qdrant 向量召回",
            "",
            "ASCII only tokens here",
        ]:
            expected = hashed_lexical_vector(text)
            actual = embedder.embed_query(text)
            self.assertEqual(len(actual), len(expected))
            for got, want in zip(actual, expected):
                self.assertEqual(got, want)
            self.assertEqual(actual, expected)

    def test_embed_query_respects_aliases(self):
        aliases = {"kb": ("knowledge", "base")}
        embedder = HashedLexicalEmbedder(aliases=aliases)
        expected = hashed_lexical_vector("kb lookup", aliases=aliases)
        self.assertEqual(embedder.embed_query("kb lookup"), expected)

    def test_embed_query_respects_custom_dimensions(self):
        embedder = HashedLexicalEmbedder(dimensions=128)
        self.assertEqual(embedder.dimensions, 128)
        vector = embedder.embed_query("dimension check")
        self.assertEqual(len(vector), 128)
        self.assertEqual(vector, hashed_lexical_vector("dimension check", dimensions=128))

    def test_embed_documents_matches_per_item(self):
        embedder = HashedLexicalEmbedder()
        texts = ["alpha beta", "gamma 中文", ""]
        batch = embedder.embed_documents(texts)
        self.assertEqual(len(batch), len(texts))
        for text, vector in zip(texts, batch):
            self.assertEqual(vector, embedder.embed_query(text))

    def test_default_metadata(self):
        embedder = HashedLexicalEmbedder()
        self.assertEqual(embedder.dimensions, 64)
        self.assertEqual(embedder.model_id, "hashed-lexical-v1")

    def test_custom_model_id(self):
        embedder = HashedLexicalEmbedder(model_id="custom-hashed")
        self.assertEqual(embedder.model_id, "custom-hashed")

    def test_satisfies_embedder_protocol(self):
        embedder = HashedLexicalEmbedder()
        self.assertIsInstance(embedder, Embedder)


if __name__ == "__main__":
    unittest.main()
