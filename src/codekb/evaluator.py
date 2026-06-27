from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .golden import load_golden_questions
from .models import GoldenQuestion, RetrievalResult
from .pipeline import ingest_raw_document
from .retrieval import Bm25LiteRetriever
from .source import FixtureSourceConnector, load_fixture_sub_kbs, load_source_bundle
from .store import InMemoryAtomStore

_PREFIX_TO_SUB_KB = {
    "REL": "release",
    "TST": "testing",
    "INC": "incident",
    "GOV": None,
}


Granularity = Literal["docid", "passage"]


@dataclass(frozen=True)
class GoldenEvalResult:
    question_id: str
    expected_sources: tuple[str, ...]
    retrieved_sources: tuple[str, ...]
    hit: bool
    skipped: bool = False
    reason: str = ""
    retrieved_anchors: tuple[str, ...] = ()
    hit_granularity: str = "docid"


@dataclass(frozen=True)
class GoldenEvalReport:
    total: int
    evaluated: int
    skipped: int
    hits: int
    hit_rate: float
    results: tuple[GoldenEvalResult, ...]


def build_fixture_store(fixture_path: str) -> InMemoryAtomStore:
    bundle = load_source_bundle(fixture_path)
    store = InMemoryAtomStore()
    for raw in bundle.documents:
        ingest_raw_document(raw, sub_kb_id=bundle.sub_kbs[raw.docid], store=store)
    return store


def evaluate_golden_questions(
    *,
    fixture_path: str,
    questions_path: str,
    top_k: int = 4,
    include_prefixes: set[str] | None = None,
    aliases: dict[str, tuple[str, ...]] | None = None,
    skip_missing_expected: bool = False,
    granularity: Granularity = "docid",
) -> GoldenEvalReport:
    bundle = load_source_bundle(fixture_path)
    present_docids = set(bundle.sub_kbs)
    store = InMemoryAtomStore()
    for raw in bundle.documents:
        ingest_raw_document(raw, sub_kb_id=bundle.sub_kbs[raw.docid], store=store)
    retriever = Bm25LiteRetriever(store, aliases=aliases)
    questions = load_golden_questions(questions_path)
    results: list[GoldenEvalResult] = []

    for question in questions:
        prefix = question.question_id.split("-", 1)[0]
        if include_prefixes is not None and prefix not in include_prefixes:
            results.append(_skip(question, "prefix_excluded"))
            continue

        sub_kb = _PREFIX_TO_SUB_KB.get(prefix)
        if sub_kb is None:
            results.append(_skip(question, "no_sub_kb_route"))
            continue
        if skip_missing_expected and not set(question.expected_sources).intersection(present_docids):
            results.append(_skip(question, "expected_source_not_loaded"))
            continue

        result = retriever.retrieve(question.question, sub_kbs={sub_kb}, top_k=top_k)
        results.append(evaluate_question(question, result, granularity=granularity))

    evaluated_results = [item for item in results if not item.skipped]
    hits = sum(1 for item in evaluated_results if item.hit)
    evaluated = len(evaluated_results)
    return GoldenEvalReport(
        total=len(results),
        evaluated=evaluated,
        skipped=len(results) - evaluated,
        hits=hits,
        hit_rate=hits / evaluated if evaluated else 0.0,
        results=tuple(results),
    )


def evaluate_question(
    question: GoldenQuestion,
    result: RetrievalResult,
    *,
    granularity: Granularity = "docid",
) -> GoldenEvalResult:
    """拿一条检索结果和一道 golden 题对分。

    docid 模式:只要召回的来源里出现任一期望 docid 就算命中。passage 模式:
    还额外要求来自期望文档的某个召回 atom 命中期望的 anchor 或章节关键词 ——
    也就是定位到对的段落/atom,而不只是对的文档。
    """
    expected_docs = set(question.expected_sources)
    retrieved_sources = tuple(dict.fromkeys(item.atom.source_docid for item in result.top_atoms))
    retrieved_anchors = tuple(
        dict.fromkeys(
            item.atom.draft.source_anchor
            for item in result.top_atoms
            if item.atom.draft.source_anchor
        )
    )

    if granularity == "passage":
        hit = _passage_hit(question, result, expected_docs)
    else:
        hit = bool(expected_docs.intersection(retrieved_sources))

    return GoldenEvalResult(
        question_id=question.question_id,
        expected_sources=question.expected_sources,
        retrieved_sources=retrieved_sources,
        hit=hit,
        retrieved_anchors=retrieved_anchors,
        hit_granularity=granularity,
    )


def _passage_hit(question: GoldenQuestion, result: RetrievalResult, expected_docs: set[str]) -> bool:
    has_criteria = bool(question.expected_anchors or question.expected_section_keywords)
    matched = False
    docid_hit = False
    for item in result.top_atoms:
        draft = item.atom.draft
        if draft.source_docid not in expected_docs:
            continue
        docid_hit = True
        if question.expected_anchors and draft.source_anchor in question.expected_anchors:
            matched = True
            break
        if question.expected_section_keywords:
            haystack = " ".join(draft.section_path) + " " + draft.text
            if any(keyword in haystack for keyword in question.expected_section_keywords):
                matched = True
                break
    # 没有 anchor/章节关键词时,passage 模式退化为只看 docid 是否出现。
    if not has_criteria:
        return docid_hit
    return matched


def _skip(question: GoldenQuestion, reason: str) -> GoldenEvalResult:
    return GoldenEvalResult(
        question_id=question.question_id,
        expected_sources=question.expected_sources,
        retrieved_sources=(),
        hit=False,
        skipped=True,
        reason=reason,
    )
