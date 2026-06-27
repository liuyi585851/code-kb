from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Layer(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class AtomStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    CURATED = "curated"
    STALE = "stale"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class SourceDocConfig:
    system: str
    docid: str
    title: str
    mode: str
    priority: str


@dataclass(frozen=True)
class SubKbConfig:
    id: str
    name: str
    owner_group: str
    status: str
    description: str
    source_docs: tuple[SourceDocConfig, ...] = ()


@dataclass(frozen=True)
class RetrievalDefaults:
    dense_top_k: int
    sparse_top_k: int
    rrf_top_k: int
    rerank_top_k: int
    max_atoms: int
    max_atom_tokens: int
    contextual_prefix_tokens: int
    citation_required: bool
    refuse_without_citation: bool
    layers_for_production_answer: tuple[str, ...]


@dataclass(frozen=True)
class KbRegistry:
    version: str
    updated_at: str
    status: str
    defaults: RetrievalDefaults
    sub_kbs: tuple[SubKbConfig, ...]

    def get_sub_kb(self, sub_kb_id: str) -> SubKbConfig:
        for sub_kb in self.sub_kbs:
            if sub_kb.id == sub_kb_id:
                return sub_kb
        raise KeyError(f"unknown sub_kb: {sub_kb_id}")

    def pilot_sub_kbs(self) -> tuple[SubKbConfig, ...]:
        return tuple(sub_kb for sub_kb in self.sub_kbs if sub_kb.status == "pilot")


@dataclass(frozen=True)
class RawDocument:
    docid: str
    title: str
    content_type: str
    body: str
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedSection:
    path: tuple[str, ...]
    anchor: str
    text: str


@dataclass(frozen=True)
class NormalizedDocument:
    docid: str
    title: str
    sections: tuple[NormalizedSection, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtomDraft:
    sub_kb_id: str
    source_docid: str
    source_title: str
    source_anchor: str
    section_path: tuple[str, ...]
    text: str
    contextual_prefix: str
    layer: Layer = Layer.L2
    status: AtomStatus = AtomStatus.CURATED


@dataclass(frozen=True)
class AtomRecord:
    atom_id: str
    draft: AtomDraft
    created_at: datetime
    updated_at: datetime

    @property
    def text(self) -> str:
        return self.draft.text

    @property
    def sub_kb_id(self) -> str:
        return self.draft.sub_kb_id

    @property
    def source_docid(self) -> str:
        return self.draft.source_docid


@dataclass(frozen=True)
class GoldenQuestion:
    question_id: str
    question: str
    expected_sources: tuple[str, ...]
    focus: str
    expected_anchors: tuple[str, ...] = ()
    expected_section_keywords: tuple[str, ...] = ()
    holdout: bool = False
    paraphrase: str = ""


@dataclass(frozen=True)
class Citation:
    docid: str
    title: str
    anchor: str
    modified_at: datetime | None = None


@dataclass(frozen=True)
class CitationPack:
    atom_id: str
    docid: str
    title: str
    anchor: str
    section_path: tuple[str, ...]
    quote: str
    score: float
    # 可选的代码定位字段(仅代码原子会通过 code_location.parse_code_location 填充);
    # 文档引用时为空串/0。
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    language: str = ""
    repo_id: str = ""
    qualified_symbol: str = ""


@dataclass(frozen=True)
class RetrievedAtom:
    atom: AtomRecord
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    top_atoms: tuple[RetrievedAtom, ...]
    sparse_hits: tuple[str, ...] = ()
    dense_hits: tuple[str, ...] = ()
    rrf_top20: tuple[str, ...] = ()
    rerank_hits: tuple[str, ...] = ()
    retriever: str = "bm25-lite"


@dataclass(frozen=True)
class AnswerResult:
    query: str
    answer: str
    citations: tuple[CitationPack, ...]
    refused: bool
    refusal_reason: str = ""
    answer_id: str = ""
    trace_id: str = ""
    confidence: float = 0.0
    generation_mode: str = "extractive"
    model_id: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    fallback_reason: str = ""
    cited_indices: tuple[int, ...] = ()
