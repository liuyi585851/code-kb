from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .aliases import load_aliases
from .evaluator import Granularity, evaluate_golden_questions
from .faithfulness import DeterministicFaithfulnessJudge, FaithfulnessJudge
from .golden import load_golden_questions
from .models import AnswerResult
from .service import OfflineKbService

# 只有忠实度过了这道下限(即评审认为大多数论断都有据可依),这条回答才计入
# answer_correctness。注意它和报告级的 min_faithfulness 门槛是两回事。
CORRECTNESS_MIN_FAITHFULNESS = 0.5


@dataclass(frozen=True)
class AnswerQuality:
    citation_present: bool
    refused: bool
    faithfulness: float
    supported: tuple[bool, ...] = ()


@dataclass(frozen=True)
class QualityItem:
    question_id: str
    question: str
    sub_kb: str
    expected_sources: tuple[str, ...]
    retrieved_sources: tuple[str, ...]
    hit: bool
    citation_present: bool
    refused: bool
    faithfulness: float
    answer_id: str
    trace_id: str
    citation_docids: tuple[str, ...]
    refusal_reason: str = ""
    answer_correct: bool = False

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "sub_kb": self.sub_kb,
            "expected_sources": list(self.expected_sources),
            "retrieved_sources": list(self.retrieved_sources),
            "hit": self.hit,
            "citation_present": self.citation_present,
            "refused": self.refused,
            "faithfulness": self.faithfulness,
            "answer_correct": self.answer_correct,
            "answer_id": self.answer_id,
            "trace_id": self.trace_id,
            "citation_docids": list(self.citation_docids),
            "refusal_reason": self.refusal_reason,
        }


@dataclass(frozen=True)
class QualityMetrics:
    evaluated: int
    citation_rate: float
    refusal_rate: float
    faithfulness: float
    answer_correctness: float
    passed: bool


@dataclass(frozen=True)
class QualityReport:
    total: int
    evaluated: int
    citation_rate: float
    refusal_rate: float
    faithfulness: float
    hit_rate: float
    passed: bool
    answer_correctness: float = 0.0
    skipped: int = 0
    items: tuple[QualityItem, ...] = ()

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "evaluated": self.evaluated,
            "skipped": self.skipped,
            "citation_rate": self.citation_rate,
            "refusal_rate": self.refusal_rate,
            "faithfulness": self.faithfulness,
            "answer_correctness": self.answer_correctness,
            "hit_rate": self.hit_rate,
            "passed": self.passed,
            "items": [item.to_dict() for item in self.items],
        }


def score_answer(answer: AnswerResult, *, judge: FaithfulnessJudge | None = None) -> AnswerQuality:
    """给单条回答打分。忠实度现在交给可注入的评审来算,不再用老的
    ``quote[:80] in answer`` 子串判断 —— 那种判断在回答本就是用自己的引用片段拼出来时
    几乎恒为 1.0,毫无意义。"""
    judge = judge or DeterministicFaithfulnessJudge()
    if answer.refused:
        return AnswerQuality(citation_present=False, refused=True, faithfulness=1.0)
    citation_present = bool(answer.citations)
    if not citation_present:
        return AnswerQuality(citation_present=False, refused=False, faithfulness=0.0)
    verdict = judge.judge(answer=answer.answer, citations=answer.citations)
    return AnswerQuality(
        citation_present=True,
        refused=False,
        faithfulness=verdict.score,
        supported=verdict.supported,
    )


def aggregate_quality(
    items: list[QualityItem],
    *,
    hit_rate: float,
    min_hit_rate: float = 0.75,
    min_citation_rate: float = 1.0,
    min_faithfulness: float = 0.7,
    max_refusal_rate: float = 0.2,
    min_answer_correctness: float | None = None,
    min_item_faithfulness: float = 0.0,
) -> QualityMetrics:
    """把逐条质量纯聚合成报告级指标并给出门禁结论。

    - citation_rate 的分母是未拒答的回答(拒答不再算引用成功,免得把比率拉高)。
    - faithfulness 只对未拒答的回答(即真正给出论断的那些)取平均。
    - answer_correctness = 既命中正确片段(hit)、又过了忠实度下限的条目占比,
      和 hit_rate 不是一回事。
    """
    evaluated = len(items)
    non_refused = [item for item in items if not item.refused]

    if non_refused:
        citation_rate = sum(1 for item in non_refused if item.citation_present) / len(non_refused)
        faithfulness = sum(item.faithfulness for item in non_refused) / len(non_refused)
    else:
        citation_rate = 1.0
        faithfulness = 1.0

    refusal_rate = sum(1 for item in items if item.refused) / evaluated if evaluated else 0.0
    answer_correctness = (
        sum(1 for item in items if item.answer_correct) / evaluated if evaluated else 0.0
    )

    # 聚合后的忠实度平均值可能把某条严重缺乏依据的回答掩盖掉。这里加一道可选的逐条
    # 下限来兜底(默认 0.0 即关闭,所以默认门禁行为不变)—— 比如设成:只要有任何一条
    # 未拒答回答低于硬下限,不管平均值多高都判失败。
    passed = (
        hit_rate >= min_hit_rate
        and citation_rate >= min_citation_rate
        and faithfulness >= min_faithfulness
        and refusal_rate <= max_refusal_rate
        and (min_answer_correctness is None or answer_correctness >= min_answer_correctness)
        and (
            min_item_faithfulness <= 0.0
            or all(item.faithfulness >= min_item_faithfulness for item in non_refused)
        )
    )
    return QualityMetrics(
        evaluated=evaluated,
        citation_rate=round(citation_rate, 3),
        refusal_rate=round(refusal_rate, 3),
        faithfulness=round(faithfulness, 3),
        answer_correctness=round(answer_correctness, 3),
        passed=passed,
    )


def is_answer_correct(*, hit: bool, refused: bool, faithfulness: float) -> bool:
    return hit and not refused and faithfulness >= CORRECTNESS_MIN_FAITHFULNESS


def evaluate_quality(
    *,
    fixture_path: str,
    questions_path: str,
    aliases_path: str = "data/entity_aliases.yaml",
    include_prefixes: set[str] | None = None,
    top_k: int = 4,
    min_hit_rate: float = 0.75,
    min_citation_rate: float = 1.0,
    min_faithfulness: float = 0.7,
    max_refusal_rate: float = 0.2,
    min_answer_correctness: float | None = None,
    min_item_faithfulness: float = 0.0,
    skip_missing_expected: bool = False,
    granularity: Granularity = "docid",
    judge: FaithfulnessJudge | None = None,
    output_path: str | None = None,
) -> QualityReport:
    prefixes = include_prefixes or {"REL", "TST", "INC"}
    aliases = load_aliases(aliases_path) if aliases_path else None
    eval_report = evaluate_golden_questions(
        fixture_path=fixture_path,
        questions_path=questions_path,
        top_k=top_k,
        include_prefixes=prefixes,
        aliases=aliases,
        skip_missing_expected=skip_missing_expected,
        granularity=granularity,
    )
    service = OfflineKbService(fixture_path=fixture_path, aliases_path=aliases_path)
    judge = judge or DeterministicFaithfulnessJudge()
    items: list[QualityItem] = []
    questions_by_id = {question.question_id: question for question in load_golden_questions(questions_path)}
    for eval_result in eval_report.results:
        if eval_result.skipped:
            continue
        question = questions_by_id[eval_result.question_id]
        prefix = eval_result.question_id.split("-", 1)[0]
        sub_kb = {"REL": "release", "TST": "testing", "INC": "incident"}.get(prefix)
        if sub_kb is None:
            continue
        answer = service.ask(question.question, sub_kbs={sub_kb}, top_k=top_k)
        quality = score_answer(answer, judge=judge)
        answer_correct = is_answer_correct(
            hit=eval_result.hit,
            refused=quality.refused,
            faithfulness=quality.faithfulness,
        )
        items.append(
            QualityItem(
                question_id=question.question_id,
                question=question.question,
                sub_kb=sub_kb,
                expected_sources=eval_result.expected_sources,
                retrieved_sources=eval_result.retrieved_sources,
                hit=eval_result.hit,
                citation_present=quality.citation_present,
                refused=quality.refused,
                faithfulness=round(quality.faithfulness, 3),
                answer_id=answer.answer_id,
                trace_id=answer.trace_id,
                citation_docids=tuple(citation.docid for citation in answer.citations),
                refusal_reason=answer.refusal_reason,
                answer_correct=answer_correct,
            )
        )

    metrics = aggregate_quality(
        items,
        hit_rate=eval_report.hit_rate,
        min_hit_rate=min_hit_rate,
        min_citation_rate=min_citation_rate,
        min_faithfulness=min_faithfulness,
        max_refusal_rate=max_refusal_rate,
        min_answer_correctness=min_answer_correctness,
        min_item_faithfulness=min_item_faithfulness,
    )
    report = QualityReport(
        total=eval_report.total,
        evaluated=metrics.evaluated,
        citation_rate=metrics.citation_rate,
        refusal_rate=metrics.refusal_rate,
        faithfulness=metrics.faithfulness,
        answer_correctness=metrics.answer_correctness,
        hit_rate=round(eval_report.hit_rate, 3),
        passed=metrics.passed,
        skipped=eval_report.skipped,
        items=tuple(items),
    )
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report
