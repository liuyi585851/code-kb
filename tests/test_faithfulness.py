import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.faithfulness import (
    DeterministicFaithfulnessJudge,
    FaithfulnessJudge,
    JudgeVerdict,
    LlmFaithfulnessJudge,
)


def _citation(quote):
    return SimpleNamespace(quote=quote)


class DeterministicFaithfulnessJudgeTests(unittest.TestCase):
    def test_protocol_conformance(self):
        self.assertIsInstance(DeterministicFaithfulnessJudge(), FaithfulnessJudge)

    def test_answer_built_from_citations_scores_full(self):
        citations = (
            _citation("回归测试需在发布前完成并归档报告。"),
            _citation("DEVICE_SEQ 表示设备序号。"),
        )
        answer = "回归测试需在发布前完成并归档报告。DEVICE_SEQ 表示设备序号。"
        verdict = DeterministicFaithfulnessJudge().judge(answer=answer, citations=citations)

        self.assertIsInstance(verdict, JudgeVerdict)
        self.assertEqual(verdict.score, 1.0)
        self.assertTrue(all(verdict.supported))
        self.assertEqual(verdict.judge, "deterministic")

    def test_fabricated_sentence_lowers_score(self):
        # 这是防"子串自证 1.0"的核心保证:回答即便逐字照抄了引用,只要再追加一句
        # 编造的断言,分数就必须低于 1.0,且那句编造的话不算有据可依。
        citations = (_citation("回归测试需在发布前完成并归档报告。"),)
        faithful = DeterministicFaithfulnessJudge().judge(
            answer="回归测试需在发布前完成并归档报告。",
            citations=citations,
        )
        fabricated = DeterministicFaithfulnessJudge().judge(
            answer="回归测试需在发布前完成并归档报告。所有设备必须立刻断网并销毁数据。",
            citations=citations,
        )

        self.assertEqual(faithful.score, 1.0)
        self.assertLess(fabricated.score, 1.0)
        self.assertFalse(fabricated.supported[-1])

    def test_runs_without_citations(self):
        verdict = DeterministicFaithfulnessJudge().judge(
            answer="这是一个没有任何引用支撑的回答。",
            citations=(),
        )
        self.assertLess(verdict.score, 1.0)
        self.assertFalse(any(verdict.supported))

    def test_empty_answer(self):
        verdict = DeterministicFaithfulnessJudge().judge(answer="", citations=(_citation("x"),))
        self.assertEqual(verdict.score, 0.0)
        self.assertEqual(verdict.supported, ())


class LlmFaithfulnessJudgeTests(unittest.TestCase):
    def test_stores_client(self):
        client = object()
        judge = LlmFaithfulnessJudge(client=client)
        self.assertIs(judge.client, client)

    def test_judge_is_placeholder(self):
        judge = LlmFaithfulnessJudge(client=object())
        with self.assertRaises(NotImplementedError):
            judge.judge(answer="a", citations=())


if __name__ == "__main__":
    unittest.main()
