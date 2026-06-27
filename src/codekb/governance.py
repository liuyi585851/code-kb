from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence
from uuid import uuid4

import yaml

from .candidate import JsonCandidateStore, NEEDS_REVISION_STATUS, PENDING_STATUS, REJECTED_STATUS
from .feedback import load_feedback_records
from .models import RawDocument
from .registry import load_registry
from .source import SourceBundle, load_combined_source_bundle


GOVERNANCE_STATE_SCHEMA_VERSION = 1
TICKET_TARGETS = {"manual", "issue_tracker", "git"}
TICKET_WRITE_TOOLS = {"issue_tracker_create_ticket", "git_create_issue"}


class GovernanceTicketClient(Protocol):
    def create_issue_tracker_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        assignee: str,
        labels: tuple[str, ...],
    ) -> dict[str, Any]: ...

    def create_git_issue(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        assignee: str,
        labels: tuple[str, ...],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GovernancePolicy:
    path: str = ""
    exists: bool = False
    owner_replacements: dict[str, str] = field(default_factory=dict)
    owner_group_assignees: dict[str, str] = field(default_factory=dict)
    item_type_assignees: dict[str, str] = field(default_factory=dict)
    stale_after_days: int | None = None
    stale_after_days_by_sub_kb: dict[str, int] = field(default_factory=dict)
    stale_after_days_by_priority: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "owner_replacements": dict(self.owner_replacements),
            "owner_group_assignees": dict(self.owner_group_assignees),
            "item_type_assignees": dict(self.item_type_assignees),
            "stale_after_days": self.stale_after_days,
            "stale_after_days_by_sub_kb": dict(self.stale_after_days_by_sub_kb),
            "stale_after_days_by_priority": dict(self.stale_after_days_by_priority),
        }


@dataclass(frozen=True)
class GovernanceItem:
    item_id: str
    item_type: str
    severity: str
    sub_kb_id: str
    title: str
    summary: str
    suggested_owner: str
    source_ref: str
    evidence: dict[str, Any]
    status: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "severity": self.severity,
            "sub_kb_id": self.sub_kb_id,
            "title": self.title,
            "summary": self.summary,
            "suggested_owner": self.suggested_owner,
            "source_ref": self.source_ref,
            "evidence": dict(self.evidence),
            "status": self.status,
        }


@dataclass(frozen=True)
class OwnerSummary:
    owner: str
    display_name: str
    status: str
    sub_kbs: tuple[str, ...]
    source_documents: int
    stale_documents: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "display_name": self.display_name,
            "status": self.status,
            "sub_kbs": list(self.sub_kbs),
            "source_documents": self.source_documents,
            "stale_documents": self.stale_documents,
        }


@dataclass(frozen=True)
class SubKbGovernanceSummary:
    sub_kb_id: str
    owner_group: str
    source_documents: int
    stale_documents: int
    open_items: int
    p1_items: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_kb_id": self.sub_kb_id,
            "owner_group": self.owner_group,
            "source_documents": self.source_documents,
            "stale_documents": self.stale_documents,
            "open_items": self.open_items,
            "p1_items": self.p1_items,
        }


@dataclass(frozen=True)
class GovernanceReport:
    generated_at: str
    source_paths: tuple[str, ...]
    registry_path: str
    policy_path: str
    feedback_log_path: str
    candidate_store_path: str
    stale_after_days: int
    candidate_sla_days: int
    source_documents: int
    counts_by_type: dict[str, int]
    counts_by_severity: dict[str, int]
    sub_kbs: tuple[SubKbGovernanceSummary, ...]
    owners: tuple[OwnerSummary, ...]
    items: tuple[GovernanceItem, ...]

    def to_dict(self, *, item_limit: int | None = None) -> dict[str, Any]:
        items = self.items if item_limit is None else self.items[: max(0, item_limit)]
        return {
            "generated_at": self.generated_at,
            "source_paths": list(self.source_paths),
            "registry_path": self.registry_path,
            "policy_path": self.policy_path,
            "feedback_log_path": self.feedback_log_path,
            "candidate_store_path": self.candidate_store_path,
            "stale_after_days": self.stale_after_days,
            "candidate_sla_days": self.candidate_sla_days,
            "source_documents": self.source_documents,
            "counts_by_type": dict(self.counts_by_type),
            "counts_by_severity": dict(self.counts_by_severity),
            "sub_kbs": [item.to_dict() for item in self.sub_kbs],
            "owners": [item.to_dict() for item in self.owners],
            "items": [item.to_dict() for item in items],
            "items_truncated": item_limit is not None and len(self.items) > max(0, item_limit),
        }


@dataclass(frozen=True)
class GovernanceTicketOperation:
    tool: str
    params: dict[str, Any]
    risk: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "params": dict(self.params),
            "risk": self.risk,
        }


@dataclass(frozen=True)
class GovernanceTicketPlan:
    ticket_id: str
    item_id: str
    item_type: str
    severity: str
    sub_kb_id: str
    title: str
    target: str
    assignee: str
    status: str
    description: str
    operations: tuple[GovernanceTicketOperation, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "severity": self.severity,
            "sub_kb_id": self.sub_kb_id,
            "title": self.title,
            "target": self.target,
            "assignee": self.assignee,
            "status": self.status,
            "description": self.description,
            "operations": [operation.to_dict() for operation in self.operations],
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class GovernanceTicketOperationResult:
    tool: str
    status: str
    detail: str
    params: dict[str, Any]
    response: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "detail": self.detail,
            "params": dict(self.params),
            "response": dict(self.response),
        }


@dataclass(frozen=True)
class GovernanceTicketPlanResult:
    ticket_id: str
    item_id: str
    target: str
    status: str
    operations: tuple[GovernanceTicketOperationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "item_id": self.item_id,
            "target": self.target,
            "status": self.status,
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass(frozen=True)
class GovernanceTicketOutboxReport:
    outbox_path: str
    execute: bool
    write_enabled: bool
    total: int
    processed: int
    invalid_lines: int
    executed_operations: int
    blocked_operations: int
    status: str
    results: tuple[GovernanceTicketPlanResult, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbox_path": self.outbox_path,
            "execute": self.execute,
            "write_enabled": self.write_enabled,
            "total": self.total,
            "processed": self.processed,
            "invalid_lines": self.invalid_lines,
            "executed_operations": self.executed_operations,
            "blocked_operations": self.blocked_operations,
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class GovernanceStateSyncResult:
    state_path: str
    items_seen: int
    new_items: int
    updated_items: int
    not_seen_items: int
    ticket_plans: int
    ticketed_items: int
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_path": self.state_path,
            "items_seen": self.items_seen,
            "new_items": self.new_items,
            "updated_items": self.updated_items,
            "not_seen_items": self.not_seen_items,
            "ticket_plans": self.ticket_plans,
            "ticketed_items": self.ticketed_items,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CuratorWeeklyReport:
    generated_at: str
    title: str
    summary: dict[str, Any]
    top_items: tuple[dict[str, Any], ...]
    markdown: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "title": self.title,
            "summary": dict(self.summary),
            "top_items": [dict(item) for item in self.top_items],
            "markdown": self.markdown,
        }


def load_governance_policy(path: str | Path | None) -> GovernancePolicy:
    if not path:
        return GovernancePolicy()
    policy_path = Path(path)
    if not policy_path.exists():
        return GovernancePolicy(path=str(policy_path))
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("governance policy must be a mapping")
    return GovernancePolicy(
        path=str(policy_path),
        exists=True,
        owner_replacements=_string_mapping(data.get("owner_replacements"), "owner_replacements"),
        owner_group_assignees=_string_mapping(data.get("owner_group_assignees"), "owner_group_assignees"),
        item_type_assignees=_string_mapping(data.get("item_type_assignees"), "item_type_assignees"),
        stale_after_days=_optional_non_negative_int(data.get("stale_after_days"), "stale_after_days"),
        stale_after_days_by_sub_kb=_non_negative_int_mapping(
            data.get("stale_after_days_by_sub_kb"),
            "stale_after_days_by_sub_kb",
        ),
        stale_after_days_by_priority=_non_negative_int_mapping(
            data.get("stale_after_days_by_priority"),
            "stale_after_days_by_priority",
        ),
    )


def build_governance_report(
    source_paths: Sequence[str | Path],
    *,
    registry_path: str | Path,
    policy_path: str | Path | None = None,
    policy: GovernancePolicy | None = None,
    feedback_log_path: str | Path | None = None,
    candidate_store_path: str | Path | None = None,
    pending_docs_dir: str | Path | None = None,
    stale_after_days: int = 180,
    candidate_sla_days: int = 3,
    now: datetime | str | None = None,
) -> GovernanceReport:
    if stale_after_days < 0:
        raise ValueError("stale_after_days must be non-negative")
    if candidate_sla_days < 0:
        raise ValueError("candidate_sla_days must be non-negative")

    policy = policy if policy is not None else load_governance_policy(policy_path)
    effective_stale_after_days = (
        policy.stale_after_days if policy.stale_after_days is not None else stale_after_days
    )
    paths = _source_paths(source_paths, pending_docs_dir)
    bundle = load_combined_source_bundle(paths)
    registry = load_registry(registry_path)
    now_dt = _parse_now(now)

    registry_sources = _registry_sources(registry)
    owner_groups = {sub_kb.id: sub_kb.owner_group for sub_kb in registry.sub_kbs}
    loaded_docids = {doc.docid for doc in bundle.documents}

    items: list[GovernanceItem] = []
    stale_docids: set[str] = set()
    for raw in bundle.documents:
        sub_kb_id = bundle.sub_kbs[raw.docid]
        registry_source = registry_sources.get(raw.docid, {})
        source_stale_after_days = _policy_stale_after_days(
            policy,
            sub_kb_id=sub_kb_id,
            priority=str(registry_source.get("priority", "")),
            default=effective_stale_after_days,
        )
        source_items, is_stale = _source_governance_items(
            raw,
            sub_kb_id=sub_kb_id,
            owner_group=owner_groups.get(sub_kb_id, ""),
            priority=str(registry_source.get("priority", "")),
            stale_after_days=source_stale_after_days,
            now=now_dt,
            policy=policy,
        )
        items.extend(source_items)
        if is_stale:
            stale_docids.add(raw.docid)

    items.extend(
        _missing_registry_source_items(
            registry_sources.values(),
            loaded_docids=loaded_docids,
            owner_groups=owner_groups,
            policy=policy,
        )
    )
    if feedback_log_path:
        items.extend(_feedback_items(feedback_log_path, policy=policy))
    if candidate_store_path:
        items.extend(
            _candidate_items(
                candidate_store_path,
                pending_docs_dir=pending_docs_dir or "",
                candidate_sla_days=candidate_sla_days,
                now=now_dt,
                owner_groups=owner_groups,
                policy=policy,
            )
        )

    items = sorted(items, key=_item_sort_key)
    counts_by_type = Counter(item.item_type for item in items)
    counts_by_severity = Counter(item.severity for item in items)

    return GovernanceReport(
        generated_at=_format_time(now_dt),
        source_paths=tuple(str(path) for path in paths),
        registry_path=str(registry_path),
        policy_path=policy.path,
        feedback_log_path=str(feedback_log_path or ""),
        candidate_store_path=str(candidate_store_path or ""),
        stale_after_days=effective_stale_after_days,
        candidate_sla_days=candidate_sla_days,
        source_documents=len(bundle.documents),
        counts_by_type=dict(sorted(counts_by_type.items())),
        counts_by_severity=dict(sorted(counts_by_severity.items())),
        sub_kbs=_sub_kb_summaries(bundle, owner_groups=owner_groups, stale_docids=stale_docids, items=items),
        owners=_owner_summaries(bundle, stale_docids=stale_docids),
        items=tuple(items),
    )


def write_governance_report(report: GovernanceReport, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_curator_weekly_report(
    report: GovernanceReport,
    *,
    state_summary: dict[str, Any] | None = None,
    item_limit: int = 20,
) -> CuratorWeeklyReport:
    if item_limit < 0:
        raise ValueError("item_limit must be non-negative")
    state_summary = dict(state_summary or {})
    inactive_owners = tuple(owner for owner in report.owners if owner.status != "active")
    top_items = tuple(item.to_dict() for item in report.items[:item_limit])
    summary = {
        "source_documents": report.source_documents,
        "open_items": len(report.items),
        "p1_items": report.counts_by_severity.get("P1", 0),
        "stale_items": report.counts_by_type.get("stale_source", 0),
        "owner_inactive_items": report.counts_by_type.get("owner_inactive", 0),
        "source_missing_items": report.counts_by_type.get("source_missing", 0),
        "feedback_badcases": report.counts_by_type.get("feedback_badcase", 0),
        "inactive_owners": len(inactive_owners),
        "state_items": int(state_summary.get("items", 0) or 0),
        "state_tickets": int(state_summary.get("tickets", 0) or 0),
    }
    title = "Code-KB Curator Weekly Report"
    markdown = render_curator_weekly_markdown(
        report,
        title=title,
        summary=summary,
        state_summary=state_summary,
        top_items=top_items,
        inactive_owners=inactive_owners,
    )
    return CuratorWeeklyReport(
        generated_at=report.generated_at,
        title=title,
        summary=summary,
        top_items=top_items,
        markdown=markdown,
    )


def write_curator_weekly_report(weekly_report: CuratorWeeklyReport, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(weekly_report.markdown, encoding="utf-8")


def render_curator_weekly_markdown(
    report: GovernanceReport,
    *,
    title: str,
    summary: dict[str, Any],
    state_summary: dict[str, Any],
    top_items: tuple[dict[str, Any], ...],
    inactive_owners: tuple[OwnerSummary, ...],
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- generated_at: {report.generated_at}",
        f"- source_documents: {summary['source_documents']}",
        f"- open_items: {summary['open_items']}",
        f"- p1_items: {summary['p1_items']}",
        f"- state_items: {summary['state_items']}",
        f"- state_tickets: {summary['state_tickets']}",
        "",
        "## Counts By Type",
        "",
        "| item_type | count |",
        "|---|---:|",
    ]
    lines.extend(f"| {_md_cell(key)} | {value} |" for key, value in sorted(report.counts_by_type.items()))
    lines.extend(
        [
            "",
            "## Counts By Severity",
            "",
            "| severity | count |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {_md_cell(key)} | {value} |" for key, value in sorted(report.counts_by_severity.items()))
    lines.extend(
        [
            "",
            "## Sub KB Health",
            "",
            "| sub_kb | owner_group | source_docs | stale_docs | open_items | p1_items |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    sub_kbs = sorted(report.sub_kbs, key=lambda item: (item.p1_items, item.open_items, item.sub_kb_id), reverse=True)
    lines.extend(
        (
            f"| {_md_cell(item.sub_kb_id or '-')} | {_md_cell(item.owner_group or '-')} | "
            f"{item.source_documents} | {item.stale_documents} | {item.open_items} | {item.p1_items} |"
        )
        for item in sub_kbs
    )
    lines.extend(
        [
            "",
            "## Inactive Owners",
            "",
            "| owner | display_name | sub_kbs | source_docs | stale_docs |",
            "|---|---|---|---:|---:|",
        ]
    )
    if inactive_owners:
        lines.extend(
            (
                f"| {_md_cell(owner.owner)} | {_md_cell(owner.display_name or '-')} | "
                f"{_md_cell(','.join(owner.sub_kbs) or '-')} | {owner.source_documents} | {owner.stale_documents} |"
            )
            for owner in inactive_owners
        )
    else:
        lines.append("| - | - | - | 0 | 0 |")
    lines.extend(
        [
            "",
            "## Top Governance Items",
            "",
            "| severity | type | sub_kb | owner | title | source |",
            "|---|---|---|---|---|---|",
        ]
    )
    if top_items:
        lines.extend(
            (
                f"| {_md_cell(item['severity'])} | {_md_cell(item['item_type'])} | "
                f"{_md_cell(item['sub_kb_id'] or '-')} | {_md_cell(item['suggested_owner'] or '-')} | "
                f"{_md_cell(item['title'])} | {_md_cell(item['source_ref'] or '-')} |"
            )
            for item in top_items
        )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## State Snapshot",
            "",
            f"- item_status: {_inline_json(state_summary.get('item_status', {}))}",
            f"- ticket_status: {_inline_json(state_summary.get('ticket_status', {}))}",
            f"- ticket_targets: {_inline_json(state_summary.get('ticket_targets', {}))}",
            "",
            "## Suggested Actions",
            "",
            "1. Confirm owner replacements for inactive owners.",
            "2. Review P1 stale sources and refresh high-priority documents.",
            "3. Convert feedback badcases and source gaps into ticket plans after owner confirmation.",
            "4. Keep external writes disabled until ISSUE_TRACKER/Git mappings and permissions are confirmed.",
            "",
        ]
    )
    return "\n".join(lines)


def load_governance_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {"version": GOVERNANCE_STATE_SCHEMA_VERSION, "items": [], "tickets": [], "updated_at": ""}
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"governance state must be a JSON object: {state_path}")
    return {
        "version": int(payload.get("version", GOVERNANCE_STATE_SCHEMA_VERSION)),
        "items": list(payload.get("items", [])),
        "tickets": list(payload.get("tickets", [])),
        "updated_at": str(payload.get("updated_at", "")),
    }


def governance_state_ticketed_item_ids(
    path: str | Path,
    *,
    targets: Sequence[str] | None = None,
) -> set[str]:
    state = load_governance_state(path)
    target_set = {str(target).strip() for target in targets or () if str(target).strip()}
    item_ids: set[str] = set()
    for ticket in state["tickets"]:
        target = str(ticket.get("target", "")).strip()
        status = str(ticket.get("status", "")).strip()
        if target_set and target not in target_set:
            continue
        if status in {"planned", "manual_required", "validated", "executed", "blocked"}:
            item_id = str(ticket.get("item_id", "")).strip()
            if item_id:
                item_ids.add(item_id)
    return item_ids


def summarize_governance_state(path: str | Path) -> dict[str, Any]:
    state = load_governance_state(path)
    item_status = Counter(str(item.get("status", "")) for item in state["items"])
    item_types = Counter(str(item.get("item_type", "")) for item in state["items"])
    ticket_status = Counter(str(ticket.get("status", "")) for ticket in state["tickets"])
    ticket_targets = Counter(str(ticket.get("target", "")) for ticket in state["tickets"])
    return {
        "state_path": str(path),
        "updated_at": state["updated_at"],
        "items": len(state["items"]),
        "tickets": len(state["tickets"]),
        "item_status": dict(sorted(item_status.items())),
        "item_types": dict(sorted(item_types.items())),
        "ticket_status": dict(sorted(ticket_status.items())),
        "ticket_targets": dict(sorted(ticket_targets.items())),
    }


def sync_governance_state(
    report: GovernanceReport,
    path: str | Path,
    *,
    ticket_plans: Sequence[GovernanceTicketPlan] | None = None,
) -> GovernanceStateSyncResult:
    state_path = Path(path)
    state = load_governance_state(state_path)
    now = _now()
    existing_items = {str(item.get("item_id", "")): dict(item) for item in state["items"]}
    existing_tickets = {str(ticket.get("ticket_id", "")): dict(ticket) for ticket in state["tickets"]}
    seen_item_ids = {item.item_id for item in report.items}
    new_items = 0
    updated_items = 0

    for item in report.items:
        previous = existing_items.get(item.item_id)
        evidence_hash = _stable_json_hash(item.evidence)
        if previous is None:
            new_items += 1
            previous = {
                "first_seen_at": report.generated_at,
                "ticket_ids": [],
                "status": item.status,
            }
        else:
            updated_items += 1
        status = str(previous.get("status") or item.status or "open")
        if status == "not_seen":
            status = item.status or "open"
        existing_items[item.item_id] = {
            **item.to_dict(),
            "status": status,
            "first_seen_at": str(previous.get("first_seen_at") or report.generated_at),
            "last_seen_at": report.generated_at,
            "evidence_hash": evidence_hash,
            "ticket_ids": list(previous.get("ticket_ids", [])),
            "ticket_count": len(previous.get("ticket_ids", [])),
            "updated_at": now,
        }

    not_seen_items = 0
    for item_id, item in list(existing_items.items()):
        if item_id in seen_item_ids:
            continue
        if str(item.get("status", "")) in {"open", "ticket_planned"}:
            item["status"] = "not_seen"
            item["updated_at"] = now
            not_seen_items += 1

    ticket_plans = tuple(ticket_plans or ())
    for plan in ticket_plans:
        existing_tickets[plan.ticket_id] = {
            "ticket_id": plan.ticket_id,
            "item_id": plan.item_id,
            "target": plan.target,
            "status": plan.status,
            "assignee": plan.assignee,
            "title": plan.title,
            "operation_tools": [operation.tool for operation in plan.operations],
            "external_ref": "",
            "planned_at": plan.created_at,
            "updated_at": now,
        }
        item = existing_items.get(plan.item_id)
        if item is not None:
            ticket_ids = list(item.get("ticket_ids", []))
            if plan.ticket_id not in ticket_ids:
                ticket_ids.append(plan.ticket_id)
            item["ticket_ids"] = ticket_ids
            item["ticket_count"] = len(ticket_ids)
            item["status"] = "ticket_planned"
            item["updated_at"] = now

    payload = {
        "version": GOVERNANCE_STATE_SCHEMA_VERSION,
        "updated_at": now,
        "items": sorted(existing_items.values(), key=lambda item: (str(item.get("status", "")), str(item.get("item_id", "")))),
        "tickets": sorted(existing_tickets.values(), key=lambda ticket: str(ticket.get("ticket_id", ""))),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ticketed_items = sum(1 for item in payload["items"] if item.get("ticket_ids"))
    return GovernanceStateSyncResult(
        state_path=str(state_path),
        items_seen=len(report.items),
        new_items=new_items,
        updated_items=updated_items,
        not_seen_items=not_seen_items,
        ticket_plans=len(ticket_plans),
        ticketed_items=ticketed_items,
        updated_at=now,
    )


def sync_governance_state_from_ticket_results(
    report: GovernanceTicketOutboxReport,
    path: str | Path,
) -> dict[str, Any]:
    """把已执行 outbox 里真实的外部工单 id 回写到 state。

    凡是带有真实 ``response.ticket_id`` 的已执行计划结果,都会把外部 id 记到
    ``items[].ticket_ids`` / ``items[].ticket_count``,以及对应 ``tickets[]`` 条目的
    ``external_ref`` / ``status`` 上。没真正执行的计划(dry-run / 被拦截 / 没有 id)
    不动 state,从而保持默认不写入的行为。
    """

    state_path = Path(path)
    state = load_governance_state(state_path)
    now = _now()
    items_by_id = {str(item.get("item_id", "")): dict(item) for item in state["items"]}
    tickets_by_id = {str(ticket.get("ticket_id", "")): dict(ticket) for ticket in state["tickets"]}

    recorded: list[dict[str, str]] = []
    updated_items = 0
    updated_tickets = 0
    for result in report.results:
        if result.status != "executed":
            continue
        external_id = _extract_external_ticket_id(result)
        if not external_id:
            continue

        ticket = tickets_by_id.get(result.ticket_id)
        if ticket is None:
            ticket = {
                "ticket_id": result.ticket_id,
                "item_id": result.item_id,
                "target": result.target,
                "operation_tools": [operation.tool for operation in result.operations],
                "planned_at": "",
            }
        ticket["external_ref"] = external_id
        ticket["status"] = "executed"
        ticket["updated_at"] = now
        tickets_by_id[result.ticket_id] = ticket
        updated_tickets += 1

        item = items_by_id.get(result.item_id)
        if item is not None:
            # 用真实的外部工单 id 替换内部计划 id(uuid4),免得同一张逻辑工单
            # 被两个 id 重复计数。
            ticket_ids = [tid for tid in item.get("ticket_ids", []) if tid != result.ticket_id]
            if external_id not in ticket_ids:
                ticket_ids.append(external_id)
            item["ticket_ids"] = ticket_ids
            item["ticket_count"] = len(ticket_ids)
            item["status"] = "ticketed"
            item["updated_at"] = now
            items_by_id[result.item_id] = item
            updated_items += 1

        recorded.append(
            {
                "item_id": result.item_id,
                "ticket_id": result.ticket_id,
                "external_ticket_id": external_id,
                "target": result.target,
            }
        )

    payload = {
        "version": GOVERNANCE_STATE_SCHEMA_VERSION,
        "updated_at": now,
        "items": sorted(
            items_by_id.values(),
            key=lambda item: (str(item.get("status", "")), str(item.get("item_id", ""))),
        ),
        "tickets": sorted(tickets_by_id.values(), key=lambda ticket: str(ticket.get("ticket_id", ""))),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "state_path": str(state_path),
        "executed_results": sum(1 for result in report.results if result.status == "executed"),
        "recorded_tickets": updated_tickets,
        "updated_items": updated_items,
        "recorded": recorded,
        "updated_at": now,
    }


def _extract_external_ticket_id(result: GovernanceTicketPlanResult) -> str:
    for operation in result.operations:
        if operation.status != "executed":
            continue
        response = operation.response or {}
        for key in ("ticket_id", "issue_id", "iid", "id"):
            value = response.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def build_governance_ticket_plans(
    report: GovernanceReport,
    *,
    target: str = "manual",
    min_severity: str = "P1",
    include_types: Sequence[str] | None = None,
    skip_item_ids: set[str] | None = None,
    limit: int = 50,
) -> tuple[GovernanceTicketPlan, ...]:
    target = _normalize_ticket_target(target)
    min_rank = _severity_rank(min_severity)
    if limit < 0:
        raise ValueError("limit must be non-negative")
    include_type_set = {str(item).strip() for item in include_types or () if str(item).strip()}
    skip_item_ids = set(skip_item_ids or set())
    plans: list[GovernanceTicketPlan] = []
    for item in report.items:
        if len(plans) >= limit:
            break
        if item.item_id in skip_item_ids:
            continue
        if item.status != "open":
            continue
        if _severity_rank(item.severity) > min_rank:
            continue
        if include_type_set and item.item_type not in include_type_set:
            continue
        title = _ticket_title(item)
        description = render_governance_ticket_description(item, report=report)
        plans.append(
            GovernanceTicketPlan(
                ticket_id=str(uuid4()),
                item_id=item.item_id,
                item_type=item.item_type,
                severity=item.severity,
                sub_kb_id=item.sub_kb_id,
                title=title,
                target=target,
                assignee=item.suggested_owner,
                status="planned",
                description=description,
                operations=_ticket_operations_for_target(item, target=target, title=title, description=description),
                created_at=_now(),
            )
        )
    return tuple(plans)


def write_governance_ticket_outbox(
    plans: tuple[GovernanceTicketPlan, ...] | list[GovernanceTicketPlan],
    path: str | Path,
) -> int:
    outbox_path = Path(path)
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    with outbox_path.open("a", encoding="utf-8") as file:
        for plan in plans:
            file.write(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return len(plans)


def process_governance_ticket_outbox(
    path: str | Path,
    *,
    execute: bool = False,
    write_enabled: bool = False,
    client: GovernanceTicketClient | None = None,
    limit: int = 50,
    report_path: str | Path | None = None,
) -> GovernanceTicketOutboxReport:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    outbox_path = Path(path)
    plans, invalid_lines = _read_ticket_outbox(outbox_path, limit=limit)
    results = tuple(
        _process_ticket_plan(plan, execute=execute, write_enabled=write_enabled, client=client)
        for plan in plans
    )
    executed_operations = sum(
        1 for result in results for operation in result.operations if operation.status == "executed"
    )
    blocked_operations = sum(
        1 for result in results for operation in result.operations if operation.status.startswith("blocked")
    )
    report = GovernanceTicketOutboxReport(
        outbox_path=str(outbox_path),
        execute=execute,
        write_enabled=write_enabled,
        total=len(plans) + invalid_lines,
        processed=len(plans),
        invalid_lines=invalid_lines,
        executed_operations=executed_operations,
        blocked_operations=blocked_operations,
        status=_ticket_report_status(results, invalid_lines=invalid_lines),
        results=results,
        created_at=_now(),
    )
    if report_path:
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def render_governance_ticket_description(item: GovernanceItem, *, report: GovernanceReport) -> str:
    evidence = json.dumps(item.evidence, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [
        f"# {item.title}",
        "",
        f"- item_id: {item.item_id}",
        f"- item_type: {item.item_type}",
        f"- severity: {item.severity}",
        f"- sub_kb_id: {item.sub_kb_id or '-'}",
        f"- suggested_owner: {item.suggested_owner or '-'}",
        f"- source_ref: {item.source_ref or '-'}",
        f"- generated_at: {report.generated_at}",
        "",
        "## Summary",
        "",
        item.summary,
        "",
        "## Evidence",
        "",
        "```json",
        evidence,
        "```",
    ]
    return "\n".join(lines).strip() + "\n"


def _string_mapping(value: Any, field_name: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = str(key).strip()
        if normalized_key:
            result[normalized_key] = str(item or "").strip()
    return result


def _optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _non_negative_int_mapping(value: Any, field_name: str) -> dict[str, int]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    result: dict[str, int] = {}
    for key, item in value.items():
        normalized_key = str(key).strip()
        if normalized_key:
            result[normalized_key] = _required_non_negative_int(item, f"{field_name}.{normalized_key}")
    return result


def _required_non_negative_int(value: Any, field_name: str) -> int:
    parsed = _optional_non_negative_int(value, field_name)
    if parsed is None:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _policy_stale_after_days(
    policy: GovernancePolicy,
    *,
    sub_kb_id: str,
    priority: str,
    default: int,
) -> int:
    if sub_kb_id in policy.stale_after_days_by_sub_kb:
        return policy.stale_after_days_by_sub_kb[sub_kb_id]
    priority_key = priority.strip()
    if priority_key in policy.stale_after_days_by_priority:
        return policy.stale_after_days_by_priority[priority_key]
    return default


def _policy_owner_group(owner_group: str, policy: GovernancePolicy) -> str:
    normalized = owner_group.strip()
    return policy.owner_group_assignees.get(normalized, normalized)


def _policy_source_owner(owner: str, owner_group: str, policy: GovernancePolicy) -> str:
    normalized_owner = owner.strip()
    if normalized_owner:
        return policy.owner_replacements.get(normalized_owner, normalized_owner)
    return _policy_owner_group(owner_group, policy)


def _policy_inactive_owner(owner: str, owner_group: str, policy: GovernancePolicy) -> str:
    normalized_owner = owner.strip()
    return (
        policy.owner_replacements.get(normalized_owner)
        or _policy_owner_group(owner_group, policy)
        or normalized_owner
    )


def _policy_item_owner(
    item_type: str,
    *,
    sub_kb_id: str,
    fallback: str,
    policy: GovernancePolicy,
) -> str:
    return (
        policy.item_type_assignees.get(f"{sub_kb_id}:{item_type}")
        or policy.item_type_assignees.get(item_type)
        or fallback
    )


def _source_governance_items(
    raw: RawDocument,
    *,
    sub_kb_id: str,
    owner_group: str,
    priority: str,
    stale_after_days: int,
    now: datetime,
    policy: GovernancePolicy,
) -> tuple[tuple[GovernanceItem, ...], bool]:
    items: list[GovernanceItem] = []
    metadata = raw.metadata
    source_ref = raw.url or f"{metadata.get('system', 'source')}://{raw.docid}"
    owner = str(metadata.get("owner", "") or "").strip()
    owner_display = str(metadata.get("owner_displayname", "") or "").strip()
    owner_group_assignee = _policy_owner_group(owner_group, policy)
    suggested_owner = _policy_source_owner(owner, owner_group, policy)
    freshness = _freshness_date(metadata)
    is_stale = False
    if freshness is not None:
        age_days = max(0, (now - freshness).days)
        if age_days > stale_after_days:
            is_stale = True
            items.append(
                GovernanceItem(
                    item_id=_item_id("stale_source", raw.docid, str(age_days)),
                    item_type="stale_source",
                    severity=_source_priority_to_severity(priority),
                    sub_kb_id=sub_kb_id,
                    title=raw.title,
                    summary=f"source document is {age_days} days old",
                    suggested_owner=suggested_owner,
                    source_ref=source_ref,
                    evidence={
                        "docid": raw.docid,
                        "freshness_at": _format_time(freshness),
                        "age_days": age_days,
                        "threshold_days": stale_after_days,
                        "priority": priority,
                    },
                )
            )

    if not owner:
        items.append(
            GovernanceItem(
                item_id=_item_id("owner_missing", raw.docid),
                item_type="owner_missing",
                severity="P1",
                sub_kb_id=sub_kb_id,
                title=raw.title,
                summary="source document has no owner metadata",
                suggested_owner=owner_group_assignee,
                source_ref=source_ref,
                evidence={"docid": raw.docid, "owner_group": owner_group},
            )
        )
    elif _owner_inactive(owner, owner_display):
        items.append(
            GovernanceItem(
                item_id=_item_id("owner_inactive", raw.docid, owner),
                item_type="owner_inactive",
                severity="P1",
                sub_kb_id=sub_kb_id,
                title=raw.title,
                summary="source document owner appears inactive",
                suggested_owner=_policy_inactive_owner(owner, owner_group, policy),
                source_ref=source_ref,
                evidence={"docid": raw.docid, "owner": owner, "owner_displayname": owner_display},
            )
        )
    return tuple(items), is_stale


def _missing_registry_source_items(
    registry_sources: Iterable[dict[str, str]],
    *,
    loaded_docids: set[str],
    owner_groups: dict[str, str],
    policy: GovernancePolicy,
) -> tuple[GovernanceItem, ...]:
    items: list[GovernanceItem] = []
    for source in registry_sources:
        docid = source["docid"]
        if docid in loaded_docids:
            continue
        sub_kb_id = source["sub_kb_id"]
        owner = _policy_item_owner(
            "source_missing",
            sub_kb_id=sub_kb_id,
            fallback=_policy_owner_group(owner_groups.get(sub_kb_id, ""), policy),
            policy=policy,
        )
        items.append(
            GovernanceItem(
                item_id=_item_id("source_missing", docid, sub_kb_id),
                item_type="source_missing",
                severity=_source_priority_to_severity(source.get("priority", "")),
                sub_kb_id=sub_kb_id,
                title=source.get("title", docid),
                summary="registry source is not loaded by the current source bundle",
                suggested_owner=owner,
                source_ref=f"{source.get('system', 'source')}://{docid}",
                evidence={
                    "docid": docid,
                    "mode": source.get("mode", ""),
                    "priority": source.get("priority", ""),
                },
            )
        )
    return tuple(items)


def _feedback_items(path: str | Path, *, policy: GovernancePolicy) -> tuple[GovernanceItem, ...]:
    items: list[GovernanceItem] = []
    for record in load_feedback_records(path):
        if record.rating >= 0 and not record.corrected_answer:
            continue
        title = f"Feedback badcase {record.answer_id}"
        owner = _policy_item_owner("feedback_badcase", sub_kb_id="", fallback="curator", policy=policy)
        items.append(
            GovernanceItem(
                item_id=_item_id("feedback_badcase", record.feedback_id, record.answer_id, record.trace_id),
                item_type="feedback_badcase",
                severity="P1" if record.rating < 0 else "P2",
                sub_kb_id="",
                title=title,
                summary=record.reason or "answer received negative or corrected feedback",
                suggested_owner=owner,
                source_ref=f"trace://{record.trace_id}",
                evidence={
                    "feedback_id": record.feedback_id,
                    "answer_id": record.answer_id,
                    "trace_id": record.trace_id,
                    "rating": record.rating,
                    "reason": record.reason,
                    "has_corrected_answer": bool(record.corrected_answer),
                    "created_at": record.created_at,
                },
            )
        )
    return tuple(items)


def _candidate_items(
    path: str | Path,
    *,
    pending_docs_dir: str | Path,
    candidate_sla_days: int,
    now: datetime,
    owner_groups: dict[str, str],
    policy: GovernancePolicy,
) -> tuple[GovernanceItem, ...]:
    store_path = Path(path)
    if not store_path.exists():
        return ()
    store = JsonCandidateStore(store_path, pending_docs_dir=pending_docs_dir or store_path.parent)
    items: list[GovernanceItem] = []
    for candidate in store.list(limit=10000):
        if candidate.status == REJECTED_STATUS:
            continue
        owner = _policy_owner_group(owner_groups.get(candidate.sub_kb_id, ""), policy)
        source_ref = candidate.source_ref or f"candidate://{candidate.candidate_id}"
        if candidate.conflict_candidate_id:
            items.append(
                GovernanceItem(
                    item_id=_item_id("candidate_conflict", candidate.candidate_id, candidate.conflict_candidate_id),
                    item_type="candidate_conflict",
                    severity="P1",
                    sub_kb_id=candidate.sub_kb_id,
                    title=candidate.title,
                    summary="candidate conflicts with an existing candidate title",
                    suggested_owner=_policy_item_owner(
                        "candidate_conflict",
                        sub_kb_id=candidate.sub_kb_id,
                        fallback=owner,
                        policy=policy,
                    ),
                    source_ref=source_ref,
                    evidence={
                        "candidate_id": candidate.candidate_id,
                        "conflict_candidate_id": candidate.conflict_candidate_id,
                        "status": candidate.status,
                    },
                )
            )
        if candidate.status == NEEDS_REVISION_STATUS:
            items.append(
                GovernanceItem(
                    item_id=_item_id("candidate_needs_revision", candidate.candidate_id),
                    item_type="candidate_needs_revision",
                    severity="P2",
                    sub_kb_id=candidate.sub_kb_id,
                    title=candidate.title,
                    summary="candidate is waiting for revision",
                    suggested_owner=_policy_item_owner(
                        "candidate_needs_revision",
                        sub_kb_id=candidate.sub_kb_id,
                        fallback=owner,
                        policy=policy,
                    ),
                    source_ref=source_ref,
                    evidence={"candidate_id": candidate.candidate_id, "updated_at": candidate.updated_at},
                )
            )
        if candidate.status == PENDING_STATUS:
            created_at = _parse_time(candidate.created_at)
            if created_at is not None:
                age_days = max(0, (now - created_at).days)
                if age_days > candidate_sla_days:
                    items.append(
                        GovernanceItem(
                            item_id=_item_id("candidate_pending_overdue", candidate.candidate_id, str(age_days)),
                            item_type="candidate_pending_overdue",
                            severity="P2",
                            sub_kb_id=candidate.sub_kb_id,
                            title=candidate.title,
                            summary=f"candidate has been pending review for {age_days} days",
                            suggested_owner=_policy_item_owner(
                                "candidate_pending_overdue",
                                sub_kb_id=candidate.sub_kb_id,
                                fallback=owner,
                                policy=policy,
                            ),
                            source_ref=source_ref,
                            evidence={
                                "candidate_id": candidate.candidate_id,
                                "age_days": age_days,
                                "sla_days": candidate_sla_days,
                                "created_at": candidate.created_at,
                            },
                        )
                    )
    return tuple(items)


def _sub_kb_summaries(
    bundle: SourceBundle,
    *,
    owner_groups: dict[str, str],
    stale_docids: set[str],
    items: Sequence[GovernanceItem],
) -> tuple[SubKbGovernanceSummary, ...]:
    docs_by_sub_kb = Counter(bundle.sub_kbs.values())
    stale_by_sub_kb = Counter(bundle.sub_kbs[docid] for docid in stale_docids if docid in bundle.sub_kbs)
    item_counts = Counter(item.sub_kb_id for item in items)
    p1_counts = Counter(item.sub_kb_id for item in items if item.severity == "P1")
    sub_kb_ids = sorted(set(owner_groups) | set(docs_by_sub_kb) | set(item_counts))
    return tuple(
        SubKbGovernanceSummary(
            sub_kb_id=sub_kb_id,
            owner_group=owner_groups.get(sub_kb_id, ""),
            source_documents=docs_by_sub_kb[sub_kb_id],
            stale_documents=stale_by_sub_kb[sub_kb_id],
            open_items=item_counts[sub_kb_id],
            p1_items=p1_counts[sub_kb_id],
        )
        for sub_kb_id in sub_kb_ids
    )


def _owner_summaries(bundle: SourceBundle, *, stale_docids: set[str]) -> tuple[OwnerSummary, ...]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "display_name": "",
            "status": "active",
            "sub_kbs": set(),
            "source_documents": 0,
            "stale_documents": 0,
        }
    )
    for raw in bundle.documents:
        owner = str(raw.metadata.get("owner", "") or "").strip() or "unassigned"
        display_name = str(raw.metadata.get("owner_displayname", "") or "").strip()
        item = grouped[owner]
        item["display_name"] = item["display_name"] or display_name
        if _owner_inactive(owner, display_name):
            item["status"] = "inactive"
        item["sub_kbs"].add(bundle.sub_kbs[raw.docid])
        item["source_documents"] += 1
        if raw.docid in stale_docids:
            item["stale_documents"] += 1
    return tuple(
        OwnerSummary(
            owner=owner,
            display_name=str(item["display_name"]),
            status=str(item["status"]),
            sub_kbs=tuple(sorted(item["sub_kbs"])),
            source_documents=int(item["source_documents"]),
            stale_documents=int(item["stale_documents"]),
        )
        for owner, item in sorted(grouped.items())
    )


def _ticket_operations_for_target(
    item: GovernanceItem,
    *,
    target: str,
    title: str,
    description: str,
) -> tuple[GovernanceTicketOperation, ...]:
    params = {
        "title": title,
        "description": description,
        "priority": item.severity,
        "assignee": item.suggested_owner,
        "labels": ["codekb", item.item_type, item.sub_kb_id or "global"],
        "item_id": item.item_id,
        "item_type": item.item_type,
        "source_ref": item.source_ref,
    }
    if target == "manual":
        return (
            GovernanceTicketOperation(
                tool="manual_ticket",
                params=params,
                risk="no_external_write",
            ),
        )
    if target == "issue_tracker":
        return (
            GovernanceTicketOperation(
                tool="issue_tracker_create_ticket",
                params=params,
                risk="creates_external_ticket_when_write_enabled",
            ),
        )
    return (
        GovernanceTicketOperation(
            tool="git_create_issue",
            params=params,
            risk="creates_external_issue_when_write_enabled",
        ),
    )


def _stable_json_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _md_cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|").strip()
    return text or "-"


def _inline_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _read_ticket_outbox(path: Path, *, limit: int) -> tuple[tuple[GovernanceTicketPlan, ...], int]:
    if not path.exists():
        return (), 0
    plans: list[GovernanceTicketPlan] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(plans) >= limit:
            break
        if not line.strip():
            continue
        try:
            plans.append(_ticket_plan_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_lines += 1
    return tuple(plans), invalid_lines


def _ticket_plan_from_dict(payload: dict[str, Any]) -> GovernanceTicketPlan:
    return GovernanceTicketPlan(
        ticket_id=str(payload.get("ticket_id", "")).strip(),
        item_id=str(payload.get("item_id", "")).strip(),
        item_type=str(payload.get("item_type", "")).strip(),
        severity=str(payload.get("severity", "")).strip(),
        sub_kb_id=str(payload.get("sub_kb_id", "")).strip(),
        title=str(payload.get("title", "")).strip(),
        target=str(payload.get("target", "")).strip(),
        assignee=str(payload.get("assignee", "")).strip(),
        status=str(payload.get("status", "planned")).strip(),
        description=str(payload.get("description", "")),
        operations=tuple(_ticket_operation_from_dict(item) for item in payload.get("operations", [])),
        created_at=str(payload.get("created_at", "")).strip(),
    )


def _ticket_operation_from_dict(payload: dict[str, Any]) -> GovernanceTicketOperation:
    return GovernanceTicketOperation(
        tool=str(payload.get("tool", "")).strip(),
        params=dict(payload.get("params", {})),
        risk=str(payload.get("risk", "")).strip(),
    )


def _process_ticket_plan(
    plan: GovernanceTicketPlan,
    *,
    execute: bool,
    write_enabled: bool,
    client: GovernanceTicketClient | None,
) -> GovernanceTicketPlanResult:
    operation_results = tuple(
        _process_ticket_operation(
            operation,
            execute=execute,
            write_enabled=write_enabled,
            client=client,
        )
        for operation in plan.operations
    )
    statuses = {item.status for item in operation_results}
    if any(status.startswith("blocked") for status in statuses):
        status = "blocked"
    elif "executed" in statuses:
        status = "executed"
    elif "manual_required" in statuses:
        status = "manual_required"
    else:
        status = "validated"
    return GovernanceTicketPlanResult(
        ticket_id=plan.ticket_id,
        item_id=plan.item_id,
        target=plan.target,
        status=status,
        operations=operation_results,
    )


def _process_ticket_operation(
    operation: GovernanceTicketOperation,
    *,
    execute: bool,
    write_enabled: bool,
    client: GovernanceTicketClient | None,
) -> GovernanceTicketOperationResult:
    if operation.tool == "manual_ticket":
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="manual_required",
            detail="manual_ticket requires human action",
            params=operation.params,
            response={},
        )
    if operation.tool not in TICKET_WRITE_TOOLS:
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="blocked_unknown_tool",
            detail=f"unsupported governance ticket tool: {operation.tool}",
            params=operation.params,
            response={},
        )
    if not execute:
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="validated",
            detail="dry_run",
            params=operation.params,
            response={},
        )
    if not write_enabled:
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="blocked_write_disabled",
            detail="set CODEKB_ENABLE_GOVERNANCE_WRITE=1 to execute ticket writes",
            params=operation.params,
            response={},
        )
    if client is None:
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="blocked_missing_client",
            detail="no governance ticket client configured",
            params=operation.params,
            response={},
        )
    try:
        response = _execute_ticket_operation(client, operation)
    except Exception as exc:  # pragma: no cover - defensive boundary for real ticket clients.
        return GovernanceTicketOperationResult(
            tool=operation.tool,
            status="blocked_execute_error",
            detail=f"{exc.__class__.__name__}: {exc}",
            params=operation.params,
            response={},
        )
    return GovernanceTicketOperationResult(
        tool=operation.tool,
        status="executed",
        detail="ok",
        params=operation.params,
        response=response,
    )


def _execute_ticket_operation(
    client: GovernanceTicketClient,
    operation: GovernanceTicketOperation,
) -> dict[str, Any]:
    params = operation.params
    labels = tuple(str(item) for item in params.get("labels", ()))
    if operation.tool == "issue_tracker_create_ticket":
        return client.create_issue_tracker_ticket(
            title=str(params["title"]),
            description=str(params["description"]),
            priority=str(params["priority"]),
            assignee=str(params.get("assignee", "")),
            labels=labels,
        )
    if operation.tool == "git_create_issue":
        return client.create_git_issue(
            title=str(params["title"]),
            description=str(params["description"]),
            priority=str(params["priority"]),
            assignee=str(params.get("assignee", "")),
            labels=labels,
        )
    raise ValueError(f"unsupported governance ticket tool: {operation.tool}")


def _ticket_report_status(
    results: tuple[GovernanceTicketPlanResult, ...],
    *,
    invalid_lines: int,
) -> str:
    if invalid_lines and not results:
        return "invalid"
    statuses = {result.status for result in results}
    if any(status.startswith("blocked") for status in statuses):
        return "blocked"
    if "executed" in statuses and len(statuses) == 1:
        return "executed"
    if "executed" in statuses:
        return "partial"
    if invalid_lines:
        return "partial"
    if not results:
        return "empty"
    if "manual_required" in statuses and len(statuses) == 1:
        return "manual_required"
    if "manual_required" in statuses:
        return "partial"
    return "validated"


def _source_paths(source_paths: Sequence[str | Path], pending_docs_dir: str | Path | None) -> tuple[str | Path, ...]:
    paths = list(source_paths)
    if pending_docs_dir:
        pending = Path(pending_docs_dir)
        if pending.exists():
            paths.append(pending)
    if not paths:
        raise ValueError("at least one source path is required")
    return tuple(paths)


def _registry_sources(registry) -> dict[str, dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for sub_kb in registry.sub_kbs:
        for source in sub_kb.source_docs:
            sources[source.docid] = {
                "sub_kb_id": sub_kb.id,
                "system": source.system,
                "docid": source.docid,
                "title": source.title,
                "mode": source.mode,
                "priority": source.priority,
            }
    return sources


def _freshness_date(metadata: dict[str, Any]) -> datetime | None:
    values = [
        metadata.get("page_updated_at"),
        metadata.get("last_modified"),
        metadata.get("modified_at"),
        metadata.get("updated_at"),
        metadata.get("approved_at"),
    ]
    parsed = [_parse_time(str(value)) for value in values if value]
    parsed = [value for value in parsed if value is not None]
    return max(parsed) if parsed else None


def _parse_now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError(f"invalid now timestamp: {value}")
    return parsed


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")
    if " " in text and "T" not in text:
        candidates.append(text.replace(" ", "T"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _format_time(value: datetime) -> str:
    value = value if value.tzinfo else value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now() -> str:
    return _format_time(datetime.now(UTC))


def _normalize_ticket_target(target: str) -> str:
    target = str(target or "").strip().lower()
    if target not in TICKET_TARGETS:
        raise ValueError("target must be manual, issue_tracker, or git")
    return target


def _ticket_title(item: GovernanceItem) -> str:
    sub_kb = item.sub_kb_id or "global"
    return f"[{item.severity}][{item.item_type}][{sub_kb}] {item.title}"


def _owner_inactive(owner: str, display_name: str) -> bool:
    marker = "\u5df2\u79bb\u804c"
    normalized = f"{owner} {display_name}".lower()
    return marker in normalized or "inactive" in normalized or "disabled" in normalized


def _source_priority_to_severity(priority: str) -> str:
    priority = str(priority or "").upper()
    if priority == "P0":
        return "P1"
    if priority in {"P1", "P2", "P3"}:
        return priority
    return "P2"


def _severity_rank(severity: str) -> int:
    severity_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    return severity_order.get(str(severity or "").upper(), 9)


def _item_id(*parts: str) -> str:
    digest = sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _item_sort_key(item: GovernanceItem) -> tuple[int, str, str, str]:
    return (
        _severity_rank(item.severity),
        item.item_type,
        item.sub_kb_id,
        item.title,
    )
