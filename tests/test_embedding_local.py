import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.embedding_config import EmbeddingConfig, resolve_embedder
from codekb.embedding_local import DEFAULT_LOCAL_EMBED_MODEL, LocalSentenceTransformerEmbedder


class LocalEmbedderConstructTests(unittest.TestCase):
    # 这些用例不加载模型(维度写死),因此不依赖 torch。
    def test_construct_with_pinned_dim(self):
        e = LocalSentenceTransformerEmbedder(model_name="x", dimensions=8)
        self.assertEqual(e.model_id, "x")
        self.assertEqual(e.dimensions, 8)

    def test_from_env(self):
        e = LocalSentenceTransformerEmbedder.from_env(
            env={"CODEKB_EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5", "CODEKB_EMBEDDING_DIM": "1024"}
        )
        self.assertEqual(e.model_id, "BAAI/bge-large-zh-v1.5")
        self.assertEqual(e.dimensions, 1024)

    def test_default_model(self):
        e = LocalSentenceTransformerEmbedder.from_env(env={"CODEKB_EMBEDDING_DIM": "1024"})
        self.assertEqual(e.model_id, DEFAULT_LOCAL_EMBED_MODEL)


class ResolveLocalEmbedderTests(unittest.TestCase):
    def test_config_local_provider(self):
        emb = resolve_embedder(EmbeddingConfig(provider="local", model_id="m", dimensions=1024))
        self.assertEqual(type(emb).__name__, "LocalSentenceTransformerEmbedder")
        self.assertEqual(emb.model_id, "m")
        self.assertEqual(emb.dimensions, 1024)


if __name__ == "__main__":
    unittest.main()
