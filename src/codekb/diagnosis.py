from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Sequence
from uuid import uuid4

from .candidate import CandidateSubmission, JsonCandidateStore
from .diagnosis_context import DiagnosticContext
from .governance import GovernanceItem
from .models import AnswerResult, CitationPack


@dataclass(frozen=True)
class DiagnosticFinding:
    finding_type: str
    severity: str
    title: str
    summary: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_type": self.finding_type,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class DiagnosticResult:
    diagnosis_id: str
    answer_id: str
    trace_id: str
    query: str
    context: DiagnosticContext
    sub_kbs: tuple[str, ...]
    answer: str
    refused: bool
    refusal_reason: str
    confidence: float
    citations: tuple[CitationPack, ...]
    findings: tuple[DiagnosticFinding, ...]
    related_governance_items: tuple[GovernanceItem, ...]
    suggested_actions: tuple[str, ...]
    gap_candidate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "answer_id": self.answer_id,
            "trace_id": self.trace_id,
            "query": self.query,
            "context": self.context.to_dict(),
            "sub_kbs": list(self.sub_kbs),
            "answer": self.answer,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "confidence": self.confidence,
            "citations": [_citation_to_dict(citation) for citation in self.citations],
            "findings": [finding.to_dict() for finding in self.findings],
            "related_governance_items": [item.to_dict() for item in self.related_governance_items],
            "suggested_actions": list(self.suggested_actions),
            "gap_candidate": dict(self.gap_candidate),
        }


def build_diagnostic_result(
    answer: AnswerResult,
    *,
    sub_kbs: set[str] | None = None,
    governance_items: Sequence[GovernanceItem] = (),
    owner_groups: dict[str, str] | None = None,
    min_confidence: float = 0.35,
    related_item_limit: int = 10,
    context: DiagnosticContext | None = None,
) -> DiagnosticResult:
    if min_confidence < 0 or min_confidence > 1:
        raise ValueError("min_confidence must be between 0 and 1")
    if related_item_limit < 0:
        raise ValueError("related_item_limit must be non-negative")

    normalized_sub_kbs = tuple(sorted(sub_kbs or ()))
    related_items = _related_governance_items(
        answer.citations,
        governance_items=governance_items,
        limit=related_item_limit,
    )
    findings = _diagnostic_findings(answer, min_confidence=min_confidence, related_items=related_items)
    actions = _suggested_actions(answer, findings=findings, related_items=related_items)
    gap_candidate = _gap_candidate(
        answer,
        sub_kbs=normalized_sub_kbs,
        owner_groups=owner_groups or {},
        min_confidence=min_confidence,
    )
    return DiagnosticResult(
        diagnosis_id=str(uuid4()),
        answer_id=answer.answer_id,
        trace_id=answer.trace_id,
        query=answer.query,
        context=context or DiagnosticContext(),
        sub_kbs=normalized_sub_kbs,
        answer=answer.answer,
        refused=answer.refused,
        refusal_reason=answer.refusal_reason,
        confidence=answer.confidence,
        citations=answer.citations,
        findings=tuple(findings),
        related_governance_items=tuple(related_items),
        suggested_actions=tuple(actions),
        gap_candidate=gap_candidate,
    )


def submit_diagnostic_gap(
    diagnosis: DiagnosticResult,
    store: JsonCandidateStore,
    *,
    submitted_by_hash: str = "",
    allow_duplicate: bool = False,
) -> CandidateSubmission:
    if not diagnosis.gap_candidate:
        raise ValueError("diagnosis does not contain a gap candidate")
    sub_kb_id = str(diagnosis.gap_candidate.get("sub_kb_id", "") or "").strip()
    if not sub_kb_id:
        raise ValueError("gap candidate sub_kb_id is required")
    return store.submit(
        sub_kb_id=sub_kb_id,
        title=_diagnostic_gap_title(diagnosis),
        content=render_diagnostic_gap_content(diagnosis),
        source_type="diagnose",
        source_ref=diagnosis.trace_id,
        submitted_by_hash=submitted_by_hash,
        metadata=_diagnostic_gap_metadata(diagnosis),
        allow_duplicate=allow_duplicate,
    )


def render_diagnostic_gap_content(diagnosis: DiagnosticResult) -> str:
    lines = [
        f"# KB Gap: {diagnosis.query}",
        "",
        "## Query",
        "",
        diagnosis.query,
        "",
        "## Diagnosis",
        "",
        f"- refused: {str(diagnosis.refused).lower()}",
        f"- refusal_reason: {diagnosis.refusal_reason or '-'}",
        f"- confidence: {diagnosis.confidence:.3f}",
        "",
        "## Findings",
        "",
    ]
    if diagnosis.findings:
        for finding in diagnosis.findings:
            lines.append(f"- {finding.severity} {finding.finding_type}: {finding.summary}")
    else:
        lines.append("- none")

    if not diagnosis.context.is_empty():
        lines.extend(["", "## Context", ""])
        for key, value in diagnosis.context.non_empty_items():
            if isinstance(value, list):
                lines.append(f"- {key}: {', '.join(str(item) for item in value)}")
            elif isinstance(value, dict):
                lines.append(f"- {key}:")
                for link_key, link_value in value.items():
                    lines.append(f"  - {link_key}: {link_value}")
            else:
                lines.append(f"- {key}: {value}")

    lines.extend(["", "## Suggested Actions", ""])
    for action in diagnosis.suggested_actions:
        lines.append(f"- {action}")

    lines.extend(["", "## Existing Answer", ""])
    lines.append(diagnosis.answer)

    if diagnosis.citations:
        lines.extend(["", "## Citations", ""])
        for citation in diagnosis.citations:
            section = " / ".join(citation.section_path) or "-"
            lines.append(f"- {citation.docid} {citation.title} #{section}")

    if diagnosis.related_governance_items:
        lines.extend(["", "## Related Governance Items", ""])
        for item in diagnosis.related_governance_items:
            lines.append(f"- {item.severity} {item.item_type} {item.item_id}: {item.summary}")
    return "\n".join(lines).strip() + "\n"


def _diagnostic_findings(
    answer: AnswerResult,
    *,
    min_confidence: float,
    related_items: Sequence[GovernanceItem],
) -> list[DiagnosticFinding]:
    findings: list[DiagnosticFinding] = []
    if answer.refused:
        findings.append(
            DiagnosticFinding(
                finding_type="no_citation",
                severity="P1",
                title="No citable KB evidence",
                summary="diagnosis query could not be answered from indexed knowledge",
                evidence={"refusal_reason": answer.refusal_reason or "NO_CITATION"},
            )
        )
    elif answer.confidence < min_confidence:
        findings.append(
            DiagnosticFinding(
                finding_type="low_confidence",
                severity="P2",
                title="Low answer confidence",
                summary="answer has citations but retrieval confidence is below the diagnostic threshold",
                evidence={"confidence": answer.confidence, "threshold": min_confidence},
            )
        )

    if related_items:
        p1_count = sum(1 for item in related_items if item.severity == "P1")
        findings.append(
            DiagnosticFinding(
                finding_type="related_governance_risk",
                severity="P1" if p1_count else "P2",
                title="Related KB governance items",
                summary="cited sources have open stale, owner, or registry governance items",
                evidence={
                    "related_items": len(related_items),
                    "p1_items": p1_count,
                    "item_types": sorted({item.item_type for item in related_items}),
                },
            )
        )
    return findings


def _related_governance_items(
    citations: Sequence[CitationPack],
    *,
    governance_items: Sequence[GovernanceItem],
    limit: int,
) -> list[GovernanceItem]:
    if limit == 0:
        return []
    cited_docids = {citation.docid for citation in citations}
    if not cited_docids:
        return []
    related: list[GovernanceItem] = []
    for item in governance_items:
        docid = str(item.evidence.get("docid", "") or "")
        if docid in cited_docids:
            related.append(item)
            if len(related) >= limit:
                break
    return related


def _suggested_actions(
    answer: AnswerResult,
    *,
    findings: Sequence[DiagnosticFinding],
    related_items: Sequence[GovernanceItem],
) -> list[str]:
    action_set: list[str] = []
    finding_types = {finding.finding_type for finding in findings}
    if "no_citation" in finding_types:
        action_set.append("create a KB gap candidate with the query and expected troubleshooting context")
        action_set.append("ask the responsible sub KB owner to add or link the missing runbook")
    elif "low_confidence" in finding_types:
        action_set.append("review the cited source and add a clearer FAQ or troubleshooting section")
    else:
        action_set.append("continue troubleshooting with the cited KB sections")

    if related_items:
        action_set.append("resolve related governance items before treating this source as authoritative")
    return action_set


def _gap_candidate(
    answer: AnswerResult,
    *,
    sub_kbs: tuple[str, ...],
    owner_groups: dict[str, str],
    min_confidence: float,
) -> dict[str, Any]:
    if not answer.refused and answer.confidence >= min_confidence:
        return {}
    sub_kb_id = sub_kbs[0] if sub_kbs else ""
    source_event = "ask_refusal" if answer.refused else "low_confidence"
    return {
        "source_event": source_event,
        "sub_kb_id": sub_kb_id,
        "summary": answer.query,
        "example_queries": [answer.query],
        "suggested_owner": owner_groups.get(sub_kb_id, "curator"),
        "priority": "P1" if answer.refused else "P2",
        "status": "open",
    }


def _diagnostic_gap_title(diagnosis: DiagnosticResult) -> str:
    query = " ".join(diagnosis.query.split())
    if len(query) > 80:
        query = query[:77].rstrip() + "..."
    return f"KB Gap: {query}"


def _diagnostic_gap_metadata(diagnosis: DiagnosticResult) -> dict[str, Any]:
    return {
        "diagnosis_id": diagnosis.diagnosis_id,
        "answer_id": diagnosis.answer_id,
        "trace_id": diagnosis.trace_id,
        "gap_fingerprint": _diagnostic_gap_fingerprint(diagnosis),
        "context": diagnosis.context.to_dict(),
        "source_event": diagnosis.gap_candidate.get("source_event", ""),
        "priority": diagnosis.gap_candidate.get("priority", ""),
        "suggested_owner": diagnosis.gap_candidate.get("suggested_owner", ""),
        "findings": [finding.to_dict() for finding in diagnosis.findings],
        "citation_docids": [citation.docid for citation in diagnosis.citations],
        "related_governance_item_ids": [item.item_id for item in diagnosis.related_governance_items],
    }


def _diagnostic_gap_fingerprint(diagnosis: DiagnosticResult) -> str:
    payload = "|".join(
        [
            str(diagnosis.gap_candidate.get("source_event", "")),
            str(diagnosis.gap_candidate.get("sub_kb_id", "")),
            diagnosis.context.surface.lower(),
            diagnosis.context.repo.lower(),
            diagnosis.context.error_code.lower(),
            " ".join(diagnosis.query.split()).lower(),
            ",".join(sorted(finding.finding_type for finding in diagnosis.findings)),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _citation_to_dict(citation: CitationPack) -> dict[str, Any]:
    return {
        "atom_id": citation.atom_id,
        "docid": citation.docid,
        "title": citation.title,
        "anchor": citation.anchor,
        "section_path": list(citation.section_path),
        "quote": citation.quote,
        "score": citation.score,
    }
