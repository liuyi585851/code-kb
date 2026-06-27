import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.embedding import HashedLexicalEmbedder
from codekb.embedding_config import (
    EmbeddingConfig,
    load_embedding_config,
    resolve_embedder,
)


class LoadEmbeddingConfigTests(unittest.TestCase):
    def test_defaults_preserve_online_behavior(self):
        config = load_embedding_config(env={})
        self.assertEqual(config.provider, "hashed")
        self.assertEqual(config.dimensions, 64)

    def test_reads_dimensions_from_env(self):
        config = load_embedding_config(env={"CODEKB_EMBEDDING_DIM": "768"})
        self.assertEqual(config.dimensions, 768)

    def test_reads_provider_model_endpoint_apikey(self):
        config = load_embedding_config(
            env={
                "CODEKB_EMBEDDING_PROVIDER": "remote",
                "CODEKB_EMBEDDING_MODEL": "bge-m3",
                "CODEKB_EMBEDDING_ENDPOINT": "https://example/embed",
                "CODEKB_EMBEDDING_API_KEY": "secret",
            }
        )
        self.assertEqual(config.provider, "remote")
        self.assertEqual(config.model_id, "bge-m3")
        self.assertEqual(config.endpoint, "https://example/embed")
        self.assertEqual(config.api_key, "secret")

    def test_falls_back_to_legacy_qdrant_vector_size(self):
        config = load_embedding_config(env={"CODEKB_QDRANT_VECTOR_SIZE": "256"})
        self.assertEqual(config.dimensions, 256)

    def test_explicit_dim_overrides_legacy_vector_size(self):
        config = load_embedding_config(
            env={
                "CODEKB_EMBEDDING_DIM": "128",
                "CODEKB_QDRANT_VECTOR_SIZE": "256",
            }
        )
        self.assertEqual(config.dimensions, 128)

    def test_invalid_dimensions_falls_back_to_default(self):
        config = load_embedding_config(env={"CODEKB_EMBEDDING_DIM": "not-a-number"})
        self.assertEqual(config.dimensions, 64)

    def test_env_file_overrides_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "embedding.env"
            env_file.write_text(
                "CODEKB_EMBEDDING_DIM=768\n"
                'CODEKB_EMBEDDING_MODEL="bge-large"\n',
                encoding="utf-8",
            )
            config = load_embedding_config(
                env={"CODEKB_EMBEDDING_DIM": "64", "CODEKB_EMBEDDING_MODEL": "old"},
                env_file=str(env_file),
            )
        self.assertEqual(config.dimensions, 768)
        self.assertEqual(config.model_id, "bge-large")

    def test_uses_os_environ_when_env_is_none(self):
        # 不应抛异常;缺少这些键时走默认值。
        config = load_embedding_config()
        self.assertIsInstance(config, EmbeddingConfig)


class ResolveEmbedderTests(unittest.TestCase):
    def test_default_resolves_hashed_dim_64(self):
        embedder = resolve_embedder()
        self.assertIsInstance(embedder, HashedLexicalEmbedder)
        self.assertEqual(embedder.dimensions, 64)

    def test_resolves_configured_dimensions(self):
        config = EmbeddingConfig(provider="hashed", dimensions=768)
        embedder = resolve_embedder(config)
        self.assertEqual(embedder.dimensions, 768)

    def test_unknown_provider_falls_back_to_hashed_with_reason(self):
        config = EmbeddingConfig(provider="mystery", dimensions=64)
        embedder = resolve_embedder(config)
        self.assertIsInstance(embedder, HashedLexicalEmbedder)
        reason = getattr(embedder, "fallback_reason", None)
        self.assertTrue(reason)
        self.assertIn("mystery", reason)

    def test_hashed_provider_has_no_fallback_reason(self):
        embedder = resolve_embedder(EmbeddingConfig())
        self.assertFalse(getattr(embedder, "fallback_reason", ""))

    def test_passes_aliases_through(self):
        aliases = {"kb": ("knowledge",)}
        embedder = resolve_embedder(EmbeddingConfig(), aliases=aliases)
        from codekb.retrieval import hashed_lexical_vector

        self.assertEqual(
            embedder.embed_query("kb"),
            hashed_lexical_vector("kb", aliases=aliases),
        )

    def test_remote_provider_falls_back_without_network(self):
        # 远端 embedder 留待后续 PR;眼下必须降级到 hashed,不联网也不需要凭据。
        config = EmbeddingConfig(provider="remote", dimensions=64, endpoint="https://x")
        embedder = resolve_embedder(config)
        self.assertIsInstance(embedder, HashedLexicalEmbedder)
        self.assertTrue(getattr(embedder, "fallback_reason", ""))


if __name__ == "__main__":
    unittest.main()
