import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import AnswerResult


class AnswerResultFieldTests(unittest.TestCase):
    def test_new_fields_have_defaults(self):
        result = AnswerResult(query="q", answer="a", citations=(), refused=False)
        self.assertEqual(result.generation_mode, "extractive")
        self.assertEqual(result.model_id, "")
        self.assertEqual(result.latency_ms, 0.0)
        self.assertEqual(result.input_tokens, 0)
        self.assertEqual(result.output_tokens, 0)
        self.assertEqual(result.fallback_reason, "")
        self.assertEqual(result.cited_indices, ())

    def test_existing_fields_unchanged(self):
        result = AnswerResult(query="q", answer="a", citations=(), refused=True)
        self.assertEqual(result.refusal_reason, "")
        self.assertEqual(result.answer_id, "")
        self.assertEqual(result.trace_id, "")
        self.assertEqual(result.confidence, 0.0)

    def test_can_set_new_fields(self):
        result = AnswerResult(
            query="q",
            answer="a [1]",
            citations=(),
            refused=False,
            generation_mode="generative",
            model_id="echo",
            latency_ms=12.5,
            input_tokens=7,
            output_tokens=3,
            fallback_reason="",
            cited_indices=(1,),
        )
        self.assertEqual(result.generation_mode, "generative")
        self.assertEqual(result.model_id, "echo")
        self.assertEqual(result.cited_indices, (1,))

    def test_is_frozen(self):
        result = AnswerResult(query="q", answer="a", citations=(), refused=False)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.generation_mode = "generative"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
