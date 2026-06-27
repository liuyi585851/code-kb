from __future__ import annotations

from dataclasses import replace

from .answer_prompt import build_answer_messages, enforce_cite_or_die
from .citation import build_citation_pack
from .llm import GenerationRequest, LlmClient
from .models import AnswerResult, CitationPack, RetrievalResult


def answer_from_retrieval(
    query: str,
    retrieval: RetrievalResult,
    *,
    llm_client: LlmClient | None = None,
    mode: str = "extractive",
) -> AnswerResult:
    citations = build_citation_pack(retrieval)
    if not citations:
        return AnswerResult(
            query=query,
            answer="我现在没有足够可引用的知识来源来回答这个问题。",
            citations=(),
            refused=True,
            refusal_reason="NO_CITATION",
        )

    if mode != "generative" or llm_client is None:
        return _deterministic_answer(query, citations)

    return _generative_answer(query, citations, llm_client)


def _deterministic_answer(
    query: str, citations: tuple[CitationPack, ...]
) -> AnswerResult:
    lines = ["根据当前可引用知识，先给出可追溯摘要："]
    for idx, citation in enumerate(citations, start=1):
        lines.append(f"{idx}. {_display_quote(citation)}（来源：{_citation_label(citation)}）")

    return AnswerResult(
        query=query,
        answer="\n".join(lines),
        citations=citations,
        refused=False,
        confidence=_confidence(citations[0].score),
    )


def _citation_label(citation: CitationPack) -> str:
    """来源标签:代码用 repo/path:Lx-y;文档用 doc/<docid>《title》#section。"""
    if getattr(citation, "start_line", 0):
        return f"{citation.file_path}:L{citation.start_line}-{citation.end_line}"
    section = " / ".join(citation.section_path)
    return f"{_source_label(citation.docid)}/{citation.docid}《{citation.title}》#{section}"


def _display_quote(citation: CitationPack) -> str:
    """去掉引用里的自描述代码头 «...»,file:line 标签里已经有了。"""
    quote = citation.quote or ""
    if quote.startswith("« "):
        end = quote.find("»")
        if end != -1:
            return quote[end + 1 :].lstrip("\n ")
    return quote


def _generative_answer(
    query: str,
    citations: tuple[CitationPack, ...],
    llm_client: LlmClient,
) -> AnswerResult:
    system, prompt = build_answer_messages(query, citations)
    request = GenerationRequest(system=system, prompt=prompt)

    try:
        result = llm_client.generate(request)
    except Exception as exc:  # noqa: BLE001 - 生成出错也不能让回答流程崩掉
        return _fallback(query, citations, f"llm_error:{type(exc).__name__}")

    check = enforce_cite_or_die(result.text, len(citations))
    if not check.ok:
        return _fallback(query, citations, _fallback_reason(check))

    cited = tuple(
        citations[i - 1] for i in check.used_indices if 1 <= i <= len(citations)
    )
    confidence_source = max(cited, key=lambda c: c.score) if cited else citations[0]
    return AnswerResult(
        query=query,
        answer=result.text,
        citations=cited or citations,
        refused=False,
        confidence=_confidence(confidence_source.score),
        generation_mode="generative",
        model_id=result.model,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cited_indices=check.used_indices,
    )


def _fallback(
    query: str, citations: tuple[CitationPack, ...], reason: str
) -> AnswerResult:
    base = _deterministic_answer(query, citations)
    return replace(
        base,
        generation_mode="extractive_fallback",
        fallback_reason=reason,
    )


def _fallback_reason(check) -> str:
    if check.refused:
        return "refused"
    if check.out_of_range:
        return "out_of_range"
    if check.has_uncited_claim:
        return "uncited_claim"
    return "guard_failed"


def _confidence(score: float) -> float:
    return round(min(0.95, max(0.1, score / (score + 5.0))), 3)


def _source_label(docid: str) -> str:
    return "doc" if docid.isdigit() else "pending"
