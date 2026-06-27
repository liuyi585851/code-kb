import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.feedback import JsonlFeedbackStore, parse_feedback_payload, summarize_feedback
from codekb.models import AnswerResult, CitationPack
from codekb.quality import (
    QualityItem,
    aggregate_quality,
    evaluate_quality,
    is_answer_correct,
    score_answer,
)

ROOT = Path(__file__).resolve().parents[1]
WIKI_MANIFEST = ROOT / "data" / "fixtures" / "sample_corpus.jsonl"
QUESTIONS = ROOT / "docs" / "p0-golden-questions.md"
ALIASES = ROOT / "data" / "entity_aliases.yaml"


class FeedbackQualityTests(unittest.TestCase):
    def test_feedback_store_appends_jsonl(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.jsonl"
            record = JsonlFeedbackStore(path).append(
                answer_id="answer-1",
                trace_id="trace-1",
                rating=1,
                reason="useful",
                user_id_hash="u_x",
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["feedback_id"], record.feedback_id)
            self.assertEqual(payload["rating"], 1)
            self.assertEqual(payload["reason"], "useful")

    def test_parse_feedback_payload_validates_required_fields(self):
        parsed = parse_feedback_payload({"answer_id": "a", "trace_id": "t", "rating": "0"})

        self.assertEqual(parsed["rating"], 0)
        with self.assertRaises(ValueError):
            parse_feedback_payload({"trace_id": "t", "rating": 1})
        with self.assertRaises(ValueError):
            parse_feedback_payload({"answer_id": "a", "trace_id": "t", "rating": 2})

    def test_feedback_summary_tracks_counts_and_badcases(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.jsonl"
            store = JsonlFeedbackStore(path)
            store.append(answer_id="answer-1", trace_id="trace-1", rating=1)
            store.append(answer_id="answer-1", trace_id="trace-2", rating=-1, reason="wrong")
            store.append(answer_id="answer-2", trace_id="trace-3", rating=0, corrected_answer="corrected")
            with path.open("a", encoding="utf-8") as file:
                file.write("{bad-json}\n")

            summary = summarize_feedback(path, badcase_limit=10)

            self.assertEqual(summary.total, 3)
            self.assertEqual(summary.positive, 1)
            self.assertEqual(summary.neutral, 1)
            self.assertEqual(summary.negative, 1)
            self.assertEqual(summary.corrected, 1)
            self.assertEqual(summary.invalid_lines, 1)
            self.assertEqual(summary.negative_rate, 0.333)
            self.assertEqual(len(summary.badcases), 2)
            self.assertEqual(summary.by_answer_id[0]["answer_id"], "answer-1")



def _citation(quote, *, docid="1000000014", anchor="a1"):
    return CitationPack(
        atom_id="atom-1",
        docid=docid,
        title="标题",
        anchor=anchor,
        section_path=("章节",),
        quote=quote,
        score=1.0,
    )


def _item(*, question_id, hit, refused, citation_present, faithfulness, answer_correct):
    return QualityItem(
        question_id=question_id,
        question="q",
        sub_kb="testing",
        expected_sources=("1000000014",),
        retrieved_sources=("1000000014",),
        hit=hit,
        citation_present=citation_present,
        refused=refused,
        faithfulness=faithfulness,
        answer_id="a",
        trace_id="t",
        citation_docids=("1000000014",),
        answer_correct=answer_correct,
    )


class PerItemFaithfulnessGuardTests(unittest.TestCase):
    def test_per_item_floor_fails_on_low_outlier(self):
        items = [
            _item(question_id="A", hit=True, refused=False, citation_present=True, faithfulness=0.95, answer_correct=True),
            _item(question_id="B", hit=True, refused=False, citation_present=True, faithfulness=0.30, answer_correct=True),
        ]
        # 默认闸门(未开启单条下限)只看均值,达标即通过。
        self.assertTrue(aggregate_quality(items, hit_rate=1.0, min_faithfulness=0.0).passed)
        # 开启单条下限后,即便均值没问题,0.30 这个离群值也会触发失败。
        gated = aggregate_quality(items, hit_rate=1.0, min_faithfulness=0.0, min_item_faithfulness=0.5)
        self.assertFalse(gated.passed)


class JudgeWiredQualityTests(unittest.TestCase):
    def test_fabricated_sentence_drops_faithfulness_below_one(self):
        citations = (_citation("回归测试需在发布前完成并归档报告。"),)
        faithful = AnswerResult(
            query="q",
            answer="回归测试需在发布前完成并归档报告。",
            citations=citations,
            refused=False,
        )
        fabricated = AnswerResult(
            query="q",
            answer="回归测试需在发布前完成并归档报告。所有设备必须立即断网并永久销毁数据。",
            citations=citations,
            refused=False,
        )

        # 旧的 quote[:80] 是否出现在答案里的算法对两者都给 1.0(自证);现在注入的
        # 评判器会惩罚那句脱离引用、凭空编造的话。
        self.assertEqual(score_answer(faithful).faithfulness, 1.0)
        self.assertLess(score_answer(fabricated).faithfulness, 1.0)

    def test_refused_answer_does_not_inflate_citation_rate(self):
        items = [
            _item(question_id="A", hit=True, refused=True, citation_present=False, faithfulness=1.0, answer_correct=False),
            _item(question_id="B", hit=True, refused=False, citation_present=False, faithfulness=0.0, answer_correct=False),
        ]
        metrics = aggregate_quality(items, hit_rate=1.0)
        # 分母只算未拒答的回答;唯一一条未拒答的回答没有引用,所以 citation_rate 为
        # 0.0(拒答掩盖不了它)。
        self.assertEqual(metrics.citation_rate, 0.0)
        self.assertEqual(metrics.refusal_rate, 0.5)

    def test_answer_correctness_can_differ_from_hit_rate(self):
        # 两条都召回了正确段落(命中),但其中一条回答不忠实,所以 answer_correctness
        # (0.5)与 hit_rate(1.0)对不上。
        items = [
            _item(question_id="A", hit=True, refused=False, citation_present=True, faithfulness=1.0, answer_correct=True),
            _item(question_id="B", hit=True, refused=False, citation_present=True, faithfulness=0.2, answer_correct=False),
        ]
        metrics = aggregate_quality(items, hit_rate=1.0)
        self.assertEqual(metrics.answer_correctness, 0.5)
        self.assertNotEqual(metrics.answer_correctness, 1.0)

    def test_is_answer_correct_joint_rule(self):
        self.assertTrue(is_answer_correct(hit=True, refused=False, faithfulness=0.8))
        self.assertFalse(is_answer_correct(hit=False, refused=False, faithfulness=1.0))
        self.assertFalse(is_answer_correct(hit=True, refused=True, faithfulness=1.0))
        self.assertFalse(is_answer_correct(hit=True, refused=False, faithfulness=0.3))


class RefusalGateTests(unittest.TestCase):
    def _items(self, refused_count, total=10):
        items = []
        for i in range(total):
            refused = i < refused_count
            items.append(
                _item(
                    question_id=f"Q{i}",
                    hit=True,
                    refused=refused,
                    citation_present=not refused,
                    faithfulness=1.0,
                    answer_correct=not refused,
                )
            )
        return items

    def test_refusal_rate_over_max_fails_gate(self):
        metrics = aggregate_quality(self._items(refused_count=3), hit_rate=1.0, max_refusal_rate=0.2)
        self.assertEqual(metrics.refusal_rate, 0.3)
        self.assertFalse(metrics.passed)

    def test_refusal_rate_within_max_passes_gate(self):
        metrics = aggregate_quality(self._items(refused_count=2), hit_rate=1.0, max_refusal_rate=0.2)
        self.assertEqual(metrics.refusal_rate, 0.2)
        self.assertTrue(metrics.passed)

    def test_min_answer_correctness_gate(self):
        items = self._items(refused_count=0)
        # 让一半回答不忠实,把 answer_correctness 压到 0.5。
        items = [
            _item(question_id=it.question_id, hit=True, refused=False, citation_present=True,
                  faithfulness=1.0 if idx % 2 == 0 else 0.1,
                  answer_correct=idx % 2 == 0)
            for idx, it in enumerate(items)
        ]
        # 放宽忠实度下限,只让 answer_correctness 这道闸门起作用。
        passing = aggregate_quality(items, hit_rate=1.0, min_faithfulness=0.0, min_answer_correctness=0.4)
        failing = aggregate_quality(items, hit_rate=1.0, min_faithfulness=0.0, min_answer_correctness=0.6)
        self.assertEqual(passing.answer_correctness, 0.5)
        self.assertTrue(passing.passed)
        self.assertFalse(failing.passed)


if __name__ == "__main__":
    unittest.main()
