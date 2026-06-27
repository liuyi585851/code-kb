from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import AnswerResult, RetrievalResult


@dataclass(frozen=True)
class TraceContext:
    answer_id: str
    trace_id: str
    query: str
    sub_kbs: tuple[str, ...]
    top_k: int


class JsonlTraceLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write(self, *, context: TraceContext, retrieval: RetrievalResult, answer: AnswerResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "answer_id": context.answer_id,
            "trace_id": context.trace_id,
            "query": context.query,
            "sub_kbs": list(context.sub_kbs),
            "top_k": context.top_k,
            "refused": answer.refused,
            "refusal_reason": answer.refusal_reason,
            "confidence": answer.confidence,
            "generation_mode": answer.generation_mode,
            "model": answer.model_id,
            "latency_ms": answer.latency_ms,
            "fallback_reason": answer.fallback_reason,
            "retriever": retrieval.retriever,
            "sparse_hits": list(retrieval.sparse_hits),
            "dense_hits": list(retrieval.dense_hits),
            "rrf_top20": list(retrieval.rrf_top20),
            "rerank_hits": list(retrieval.rerank_hits),
            "citations": [
                {
                    "atom_id": citation.atom_id,
                    "docid": citation.docid,
                    "title": citation.title,
                    "anchor": citation.anchor,
                    "section_path": list(citation.section_path),
                    "score": citation.score,
                }
                for citation in answer.citations
            ],
            "hits": [
                {
                    "rank": rank,
                    "atom_id": hit.atom.atom_id,
                    "docid": hit.atom.source_docid,
                    "title": hit.atom.draft.source_title,
                    "anchor": hit.atom.draft.source_anchor,
                    "section_path": list(hit.atom.draft.section_path),
                    "sub_kb_id": hit.atom.sub_kb_id,
                    "score": hit.score,
                    "matched_terms": list(hit.matched_terms),
                }
                for rank, hit in enumerate(retrieval.top_atoms, start=1)
            ],
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
