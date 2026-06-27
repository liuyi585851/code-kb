from __future__ import annotations

from dataclasses import dataclass

from .evaluator import Granularity
from .faithfulness import FaithfulnessJudge
from .quality import QualityReport, evaluate_quality

DEFAULT_FIXTURES = "data/fixtures/sample_corpus.jsonl"
DEFAULT_QUESTIONS = "data/fixtures/golden_questions.md"
DEFAULT_ALIASES = "data/entity_aliases.yaml"


@dataclass(frozen=True)
class QualityGateResult:
    """答案质量门禁的结果:通过与否,外加结构化的失败原因。

    门禁通过时 ``reasons`` 为空;否则每一项说明哪个阈值被突破,方便调用方或 CI
    看清楚到底为什么没过。
    """

    passed: bool
    reasons: tuple[str, ...]
    report: QualityReport

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "report": self.report.to_dict(),
        }


def run_quality_gate(
    *,
    fixture_path: str = DEFAULT_FIXTURES,
    questions_path: str = DEFAULT_QUESTIONS,
    aliases_path: str = DEFAULT_ALIASES,
    include_prefixes: set[str] | None = None,
    top_k: int = 4,
    min_hit_rate: float = 0.75,
    min_citation_rate: float = 1.0,
    min_faithfulness: float = 0.7,
    max_refusal_rate: float = 0.2,
    min_answer_correctness: float | None = None,
    skip_missing_expected: bool = False,
    granularity: Granularity = "docid",
    judge: FaithfulnessJudge | None = None,
    output_path: str | None = None,
) -> QualityGateResult:
    """跑 P2 质量评估,并据此给出通过/不通过的门禁结论。

    复用 :func:`evaluate_quality`(PR-12)拿到各项指标和是否达标的标志,再为每个被
    突破的阈值生成一句人能看懂的原因。"""
    report = evaluate_quality(
        fixture_path=fixture_path,
        questions_path=questions_path,
        aliases_path=aliases_path,
        include_prefixes=include_prefixes,
        top_k=top_k,
        min_hit_rate=min_hit_rate,
        min_citation_rate=min_citation_rate,
        min_faithfulness=min_faithfulness,
        max_refusal_rate=max_refusal_rate,
        min_answer_correctness=min_answer_correctness,
        skip_missing_expected=skip_missing_expected,
        granularity=granularity,
        judge=judge,
        output_path=output_path,
    )

    reasons: list[str] = []
    if report.hit_rate < min_hit_rate:
        reasons.append(f"hit_rate {report.hit_rate:.3f} < min_hit_rate {min_hit_rate:.3f}")
    if report.citation_rate < min_citation_rate:
        reasons.append(
            f"citation_rate {report.citation_rate:.3f} < min_citation_rate {min_citation_rate:.3f}"
        )
    if report.faithfulness < min_faithfulness:
        reasons.append(
            f"faithfulness {report.faithfulness:.3f} < min_faithfulness {min_faithfulness:.3f}"
        )
    if report.refusal_rate > max_refusal_rate:
        reasons.append(
            f"refusal_rate {report.refusal_rate:.3f} > max_refusal_rate {max_refusal_rate:.3f}"
        )
    if min_answer_correctness is not None and report.answer_correctness < min_answer_correctness:
        reasons.append(
            f"answer_correctness {report.answer_correctness:.3f} "
            f"< min_answer_correctness {min_answer_correctness:.3f}"
        )

    return QualityGateResult(passed=report.passed, reasons=tuple(reasons), report=report)
