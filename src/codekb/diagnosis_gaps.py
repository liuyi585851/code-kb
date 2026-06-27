from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable

from .candidate import APPROVED_STATUS, CandidateRecord, JsonCandidateStore, REJECTED_STATUS


DIAGNOSE_SOURCE_TYPE = "diagnose"
_TECH_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./:-]*|[0-9][A-Za-z0-9_./:-]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CJK_STOP_CHARS = set("的是了在和与及或也就都而及其这个那个什么怎么如何哪里哪个处理说明问题当前没有可以进行一个")


@dataclass(frozen=True)
class DiagnosticGapCluster:
    cluster_id: str
    cluster_key: str
    sub_kb_id: str
    representative_title: str
    total_candidates: int
    open_candidates: int
    status_counts: dict[str, int]
    priority_counts: dict[str, int]
    source_events: tuple[str, ...]
    suggested_owners: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    example_titles: tuple[str, ...]
    fingerprints: tuple[str, ...]
    similarity_terms: tuple[str, ...]
    first_created_at: str
    latest_updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "cluster_key": self.cluster_key,
            "sub_kb_id": self.sub_kb_id,
            "representative_title": self.representative_title,
            "total_candidates": self.total_candidates,
            "open_candidates": self.open_candidates,
            "status_counts": dict(self.status_counts),
            "priority_counts": dict(self.priority_counts),
            "source_events": list(self.source_events),
            "suggested_owners": list(self.suggested_owners),
            "candidate_ids": list(self.candidate_ids),
            "example_titles": list(self.example_titles),
            "fingerprints": list(self.fingerprints),
            "similarity_terms": list(self.similarity_terms),
            "first_created_at": self.first_created_at,
            "latest_updated_at": self.latest_updated_at,
        }


@dataclass(frozen=True)
class DiagnosticGapSummary:
    store_path: str
    total_candidates: int
    total_diagnostic_gaps: int
    clusters_total: int
    counts_by_status: dict[str, int]
    counts_by_sub_kb: dict[str, int]
    counts_by_priority: dict[str, int]
    counts_by_owner: dict[str, int]
    clusters: tuple[DiagnosticGapCluster, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_path": self.store_path,
            "total_candidates": self.total_candidates,
            "total_diagnostic_gaps": self.total_diagnostic_gaps,
            "clusters_total": self.clusters_total,
            "counts_by_status": dict(self.counts_by_status),
            "counts_by_sub_kb": dict(self.counts_by_sub_kb),
            "counts_by_priority": dict(self.counts_by_priority),
            "counts_by_owner": dict(self.counts_by_owner),
            "clusters": [cluster.to_dict() for cluster in self.clusters],
        }


def summarize_diagnostic_gaps(
    store: JsonCandidateStore,
    *,
    status: str | None = None,
    limit: int = 20,
) -> DiagnosticGapSummary:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    all_candidates = store.list(limit=100000)
    diagnostic_gaps = tuple(
        candidate
        for candidate in all_candidates
        if candidate.source_type == DIAGNOSE_SOURCE_TYPE and (not status or candidate.status == status)
    )
    clusters = _cluster_diagnostic_gaps(diagnostic_gaps)
    clusters = sorted(
        clusters,
        key=lambda item: (item.open_candidates, item.total_candidates, item.latest_updated_at),
        reverse=True,
    )
    return DiagnosticGapSummary(
        store_path=str(store.path),
        total_candidates=len(all_candidates),
        total_diagnostic_gaps=len(diagnostic_gaps),
        clusters_total=len(clusters),
        counts_by_status=_counter(candidate.status for candidate in diagnostic_gaps),
        counts_by_sub_kb=_counter(candidate.sub_kb_id for candidate in diagnostic_gaps),
        counts_by_priority=_counter(_metadata_value(candidate, "priority", "P2") for candidate in diagnostic_gaps),
        counts_by_owner=_counter(_metadata_value(candidate, "suggested_owner", "") for candidate in diagnostic_gaps),
        clusters=tuple(clusters[:limit]),
    )


def _cluster_diagnostic_gaps(candidates: Iterable[CandidateRecord]) -> tuple[DiagnosticGapCluster, ...]:
    grouped: dict[str, list[CandidateRecord]] = defaultdict(list)
    for candidate in candidates:
        grouped[_cluster_key(candidate)].append(candidate)
    return tuple(_build_cluster(key, items) for key, items in grouped.items())


def _build_cluster(cluster_key: str, candidates: list[CandidateRecord]) -> DiagnosticGapCluster:
    ordered = sorted(candidates, key=lambda item: item.updated_at, reverse=True)
    representative = ordered[0]
    status_counts = Counter(candidate.status for candidate in candidates)
    priority_counts = Counter(_metadata_value(candidate, "priority", "P2") for candidate in candidates)
    source_events = tuple(sorted({_metadata_value(candidate, "source_event", "") for candidate in candidates if _metadata_value(candidate, "source_event", "")}))
    suggested_owners = tuple(sorted({_metadata_value(candidate, "suggested_owner", "") for candidate in candidates if _metadata_value(candidate, "suggested_owner", "")}))
    fingerprints = tuple(sorted({_metadata_value(candidate, "gap_fingerprint", "") for candidate in candidates if _metadata_value(candidate, "gap_fingerprint", "")}))
    terms = _query_terms(_gap_query(representative))
    return DiagnosticGapCluster(
        cluster_id=_cluster_id(cluster_key),
        cluster_key=cluster_key,
        sub_kb_id=representative.sub_kb_id,
        representative_title=representative.title,
        total_candidates=len(candidates),
        open_candidates=sum(1 for candidate in candidates if candidate.status not in {APPROVED_STATUS, REJECTED_STATUS}),
        status_counts=dict(sorted(status_counts.items())),
        priority_counts=dict(sorted(priority_counts.items())),
        source_events=source_events,
        suggested_owners=suggested_owners,
        candidate_ids=tuple(candidate.candidate_id for candidate in ordered),
        example_titles=tuple(dict.fromkeys(candidate.title for candidate in ordered[:3])),
        fingerprints=fingerprints,
        similarity_terms=terms,
        first_created_at=min((candidate.created_at for candidate in candidates), default=""),
        latest_updated_at=max((candidate.updated_at for candidate in candidates), default=""),
    )


def _cluster_key(candidate: CandidateRecord) -> str:
    event = _metadata_value(candidate, "source_event", "diagnose")
    terms = _query_terms(_gap_query(candidate))
    if terms:
        signature = ",".join(terms)
    else:
        signature = _metadata_value(candidate, "gap_fingerprint", "") or candidate.dedupe_key
    return "|".join([candidate.sub_kb_id, event, signature])


def _cluster_id(cluster_key: str) -> str:
    return sha256(cluster_key.encode("utf-8")).hexdigest()[:16]


def _gap_query(candidate: CandidateRecord) -> str:
    title = candidate.title.strip()
    for prefix in ("KB Gap:", "KB Gap："):
        if title.startswith(prefix):
            return title[len(prefix):].strip()
    return title


def _query_terms(query: str) -> tuple[str, ...]:
    tech_terms = sorted({item.group(0).lower() for item in _TECH_TOKEN_RE.finditer(query)})
    if tech_terms:
        return tuple(tech_terms[:8])
    chars = [char for char in _CJK_RE.findall(query) if char not in _CJK_STOP_CHARS]
    deduped = tuple(sorted(set(chars)))
    return deduped[:12]


def _metadata_value(candidate: CandidateRecord, key: str, default: str) -> str:
    value = candidate.metadata.get(key, default)
    return str(value or default).strip()


def _counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(value or "" for value in values).items()))
