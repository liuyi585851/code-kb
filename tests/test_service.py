import sys
import unittest
import json
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.service import OfflineKbService
from codekb.diagnosis_context import DiagnosticContext


ROOT = Path(__file__).resolve().parents[1]


class _StubEmbedder:
    dimensions = 8
    model_id = "stub-embedder"

    def embed_query(self, text):
        return [1.0] * self.dimensions

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


class OfflineKbServiceEmbedderTests(unittest.TestCase):
    def test_default_embedder_is_hashed_dim_64(self):
        service = OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
        )
        self.assertEqual(service.embedder.dimensions, 64)
        self.assertEqual(service.embedder.model_id, "hashed-lexical-v1")

    def test_injected_embedder_is_passed_through_to_qdrant_retriever(self):
        stub = _StubEmbedder()
        service = OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
            retriever_mode="qdrant-lite",
            qdrant_url="http://qdrant.invalid:6333",
            embedder=stub,
        )
        self.assertIs(service.embedder, stub)
        self.assertIs(service.retriever.embedder, stub)


class OfflineKbServiceTests(unittest.TestCase):

    def test_ask_refuses_when_no_sub_kb_match(self):
        service = OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
        )

        answer = service.ask("DEVICE_SEQ 是什么？", sub_kbs={"release"})

        self.assertTrue(answer.refused)
        self.assertTrue(answer.answer_id)
        self.assertTrue(answer.trace_id)
        self.assertEqual(answer.refusal_reason, "NO_CITATION")



    def test_postgres_atom_store_mode_uses_postgres_store(self):
        fake_store = object()
        with patch("codekb.service.PostgresAtomStore", return_value=fake_store) as store_cls:
            service = OfflineKbService(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                atom_store_mode="postgres",
                postgres_dsn="postgresql://example",
            )

        store_cls.assert_called_once_with("postgresql://example")
        self.assertIs(service.store, fake_store)

    def test_postgres_atom_store_mode_requires_dsn(self):
        with self.assertRaises(ValueError):
            OfflineKbService(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                atom_store_mode="postgres",
                postgres_dsn="",
            )

    def test_diagnose_wraps_answer_and_gap_candidate(self):
        service = OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
        )

        diagnosis = service.diagnose(
            "DEVICE_SEQ 是什么？",
            sub_kbs={"release"},
            owner_groups={"release": "release_owner"},
        )

        self.assertTrue(diagnosis.refused)
        self.assertTrue(diagnosis.answer_id)
        self.assertTrue(diagnosis.trace_id)
        self.assertEqual(diagnosis.gap_candidate["suggested_owner"], "release_owner")

    def test_diagnose_preserves_context(self):
        service = OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
        )

        diagnosis = service.diagnose(
            "DEVICE_SEQ 构建失败",
            sub_kbs={"testing"},
            context=DiagnosticContext(surface="ci", repo="ym/app", error_code="DEVICE_SEQ"),
        )

        self.assertEqual(diagnosis.context.surface, "ci")
        self.assertEqual(diagnosis.to_dict()["context"]["repo"], "ym/app")


class _StubLlmClient:
    """确定性、不走网络的 LlmClient，始终引用 [1]。"""

    def generate(self, req):
        from codekb.llm import GenerationResult

        return GenerationResult(
            text="DEVICE_SEQ 是平台内置的设备序号 [1]。",
            model="stub-model",
            latency_ms=9.5,
            input_tokens=11,
            output_tokens=4,
            finish_reason="end_turn",
        )


class GenerativeServiceModeTests(unittest.TestCase):
    def _service(self, **kwargs):
        return OfflineKbService(
            fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
            **kwargs,
        )



    def test_extractive_default_keeps_legacy_keys(self):
        with TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "ext-trace.jsonl"
            service = self._service(trace_log_path=str(trace_path))
            self.assertEqual(service.answer_mode, "extractive")
            self.assertIsNone(service.llm_client)

            answer = service.ask("DEVICE_SEQ 是什么？", sub_kbs={"testing"})
            self.assertEqual(answer.generation_mode, "extractive")

            payload = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["generation_mode"], "extractive")
            self.assertEqual(payload["fallback_reason"], "")

    def test_generative_without_key_degrades_without_raising(self):
        with patch.dict("os.environ", {}, clear=True):
            # 既没注入 client 也没有 ANTHROPIC_API_KEY，from_env() 返回 None。
            service = self._service(answer_mode="generative")

        self.assertEqual(service.answer_mode, "extractive")
        self.assertIsNone(service.llm_client)
        self.assertEqual(service.answer_mode_downgrade_reason, "no_llm_client")

        answer = service.ask("DEVICE_SEQ 是什么？", sub_kbs={"testing"})
        self.assertEqual(answer.generation_mode, "extractive")

    def test_answer_mode_reads_env_default(self):
        with patch.dict("os.environ", {"CODEKB_ANSWER_MODE": "generative"}, clear=True):
            # 通过环境变量要求 generative，但没有 key，优雅降级到 extractive。
            service = self._service()
        self.assertEqual(service.answer_mode, "extractive")
        self.assertEqual(service.answer_mode_downgrade_reason, "no_llm_client")


if __name__ == "__main__":
    unittest.main()
