import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.answer import answer_from_retrieval
from codekb.llm import GenerationRequest, GenerationResult
from codekb.models import RetrievalResult
from codekb.pipeline import ingest_raw_document
from codekb.retrieval import Bm25LiteRetriever
from codekb.models import RawDocument
from codekb.store import InMemoryAtomStore


class _StubLlmClient:
    """返回固定文本(或抛异常),全程不碰网络。"""

    def __init__(self, *, text=None, exc=None):
        self._text = text
        self._exc = exc
        self.last_request = None

    def generate(self, req: GenerationRequest) -> GenerationResult:
        self.last_request = req
        if self._exc is not None:
            raise self._exc
        return GenerationResult(
            text=self._text,
            model="stub-model",
            latency_ms=12.5,
            input_tokens=7,
            output_tokens=3,
            finish_reason="end_turn",
        )


def _retrieval_with_citation():
    store = InMemoryAtomStore()
    raw = RawDocument(
        docid="1000000014",
        title="示例UDT自动化测试使用说明",
        content_type="DOC",
        body="## 参数\n\nDEVICE_SEQ 是平台内置环境变量，表示当前设备序号。" * 5,
    )
    ingest_raw_document(raw, sub_kb_id="testing", store=store)
    return Bm25LiteRetriever(store).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"})


class AnswerTests(unittest.TestCase):
    def test_refuses_without_citation(self):
        answer = answer_from_retrieval("未知问题", RetrievalResult(query="未知问题", top_atoms=()))

        self.assertTrue(answer.refused)
        self.assertEqual(answer.refusal_reason, "NO_CITATION")
        self.assertEqual(answer.citations, ())

    def test_answer_includes_citation(self):
        store = InMemoryAtomStore()
        raw = RawDocument(
            docid="1000000014",
            title="示例UDT自动化测试使用说明",
            content_type="DOC",
            body="## 参数\n\nDEVICE_SEQ 是平台内置环境变量，表示当前设备序号。" * 5,
        )
        ingest_raw_document(raw, sub_kb_id="testing", store=store)
        retrieval = Bm25LiteRetriever(store).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"})

        answer = answer_from_retrieval("DEVICE_SEQ 是什么", retrieval)

        self.assertFalse(answer.refused)
        self.assertEqual(answer.citations[0].docid, "1000000014")
        self.assertIn("doc/1000000014", answer.answer)


class GenerativeAnswerTests(unittest.TestCase):
    def test_default_no_client_is_byte_identical_to_extractive(self):
        retrieval = _retrieval_with_citation()
        baseline = answer_from_retrieval("DEVICE_SEQ 是什么", retrieval)
        # 默认 mode + 不带 client,输出必须和原来的摘录模式逐字一致。
        explicit = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=None, mode="extractive"
        )
        self.assertEqual(baseline.answer, explicit.answer)
        self.assertEqual(baseline.generation_mode, "extractive")
        self.assertEqual(baseline.fallback_reason, "")
        self.assertEqual(baseline.cited_indices, ())

    def test_generative_mode_without_client_stays_extractive(self):
        retrieval = _retrieval_with_citation()
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, mode="generative", llm_client=None
        )
        self.assertEqual(result.generation_mode, "extractive")

    def test_generative_uses_llm_text_with_valid_citation(self):
        retrieval = _retrieval_with_citation()
        client = _StubLlmClient(text="DEVICE_SEQ 是平台内置的设备序号 [1]。")
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=client, mode="generative"
        )
        self.assertFalse(result.refused)
        self.assertEqual(result.generation_mode, "generative")
        self.assertEqual(result.answer, "DEVICE_SEQ 是平台内置的设备序号 [1]。")
        self.assertEqual(result.model_id, "stub-model")
        self.assertEqual(result.latency_ms, 12.5)
        self.assertEqual(result.input_tokens, 7)
        self.assertEqual(result.output_tokens, 3)
        self.assertEqual(result.cited_indices, (1,))
        self.assertEqual(len(result.citations), 1)

    def test_generative_refusal_falls_back(self):
        retrieval = _retrieval_with_citation()
        client = _StubLlmClient(text="NO_SUPPORT")
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=client, mode="generative"
        )
        self.assertFalse(result.refused)
        self.assertEqual(result.generation_mode, "extractive_fallback")
        self.assertEqual(result.fallback_reason, "refused")
        self.assertIn("doc/1000000014", result.answer)

    def test_generative_uncited_claim_falls_back(self):
        retrieval = _retrieval_with_citation()
        client = _StubLlmClient(text="DEVICE_SEQ 是设备序号。")  # 没有 [n] 引用标记
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=client, mode="generative"
        )
        self.assertEqual(result.generation_mode, "extractive_fallback")
        self.assertEqual(result.fallback_reason, "uncited_claim")

    def test_generative_out_of_range_falls_back(self):
        retrieval = _retrieval_with_citation()
        client = _StubLlmClient(text="结论 [9]。")
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=client, mode="generative"
        )
        self.assertEqual(result.generation_mode, "extractive_fallback")
        self.assertEqual(result.fallback_reason, "out_of_range")

    def test_generative_client_exception_falls_back_without_raising(self):
        retrieval = _retrieval_with_citation()
        client = _StubLlmClient(exc=RuntimeError("boom"))
        result = answer_from_retrieval(
            "DEVICE_SEQ 是什么", retrieval, llm_client=client, mode="generative"
        )
        self.assertEqual(result.generation_mode, "extractive_fallback")
        self.assertTrue(result.fallback_reason.startswith("llm_error:"))
        self.assertIn("doc/1000000014", result.answer)


if __name__ == "__main__":
    unittest.main()

