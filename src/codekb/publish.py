from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import uuid4

from .models import RawDocument
from .publish_redline import RedlineRule, scan_operation_redline
from .source import load_source_bundle


PUBLISH_MODES = {"manual", "index_page", "template_copy"}
WRITE_TOOLS = {"saveDocumentParts", "copyDocument", "saveDocument"}


class WikiPublishClient(Protocol):
    def save_document_parts(self, *, id: int, title: str, after: str = "", before: str = "") -> dict[str, Any]: ...

    def copy_document(
        self,
        *,
        docid: int,
        new_parentid: int,
        is_single: int = 1,
        language: str = "zh_CN",
    ) -> dict[str, Any]: ...

    def save_document(
        self,
        *,
        docid: int,
        title: str,
        body: str,
        is_html: bool = False,
        raw: bool = False,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublishOperation:
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
class PublishPlan:
    publish_id: str
    candidate_id: str
    sub_kb_id: str
    title: str
    source_path: str
    mode: str
    status: str
    rendered_body: str
    operations: tuple[PublishOperation, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_id": self.publish_id,
            "candidate_id": self.candidate_id,
            "sub_kb_id": self.sub_kb_id,
            "title": self.title,
            "source_path": self.source_path,
            "mode": self.mode,
            "status": self.status,
            "rendered_body": self.rendered_body,
            "operations": [operation.to_dict() for operation in self.operations],
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PublishOperationResult:
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
class PublishPlanResult:
    publish_id: str
    candidate_id: str
    mode: str
    status: str
    operations: tuple[PublishOperationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_id": self.publish_id,
            "candidate_id": self.candidate_id,
            "mode": self.mode,
            "status": self.status,
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass(frozen=True)
class PublishOutboxReport:
    outbox_path: str
    execute: bool
    write_enabled: bool
    total: int
    processed: int
    invalid_lines: int
    executed_operations: int
    blocked_operations: int
    skipped_operations: int
    status: str
    results: tuple[PublishPlanResult, ...]
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
            "skipped_operations": self.skipped_operations,
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
            "created_at": self.created_at,
        }


def build_publish_plans(
    pending_docs_dir: str | Path,
    *,
    mode: str = "manual",
    target_parentid: str = "",
    template_docid: str = "",
    index_docid: str = "",
    limit: int = 50,
) -> tuple[PublishPlan, ...]:
    mode = _normalize_mode(mode)
    if limit < 0:
        raise ValueError("limit must be non-negative")
    _validate_mode_requirements(mode, target_parentid=target_parentid, template_docid=template_docid, index_docid=index_docid)

    bundle = load_source_bundle(pending_docs_dir)
    plans: list[PublishPlan] = []
    for raw in sorted(bundle.documents, key=lambda item: (item.metadata.get("approved_at", ""), item.docid), reverse=True):
        if len(plans) >= limit:
            break
        sub_kb_id = bundle.sub_kbs[raw.docid]
        body = render_wiki_body(raw, sub_kb_id=sub_kb_id)
        plans.append(
            PublishPlan(
                publish_id=str(uuid4()),
                candidate_id=str(raw.metadata.get("candidate_id") or raw.docid),
                sub_kb_id=sub_kb_id,
                title=raw.title,
                source_path=str(raw.metadata.get("pending_doc_path", "")),
                mode=mode,
                status="planned",
                rendered_body=body,
                operations=_operations_for_mode(
                    raw,
                    body=body,
                    mode=mode,
                    target_parentid=target_parentid,
                    template_docid=template_docid,
                    index_docid=index_docid,
                ),
                created_at=_now(),
            )
        )
    return tuple(plans)


def write_publish_outbox(plans: tuple[PublishPlan, ...] | list[PublishPlan], path: str | Path) -> int:
    outbox_path = Path(path)
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    with outbox_path.open("a", encoding="utf-8") as file:
        for plan in plans:
            file.write(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return len(plans)


def process_publish_outbox(
    path: str | Path,
    *,
    execute: bool = False,
    write_enabled: bool = False,
    client: WikiPublishClient | None = None,
    limit: int = 50,
    report_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    confirm_real_publish: bool = False,
    redline_rules: Sequence[RedlineRule] | None = None,
) -> PublishOutboxReport:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    outbox_path = Path(path)
    plans, invalid_lines = _read_outbox(outbox_path, limit=limit)
    executed_publish_ids = _read_executed_publish_ids(ledger_path) if execute and ledger_path else set()
    results = tuple(
        _process_plan(
            plan,
            execute=execute,
            write_enabled=write_enabled,
            client=client,
            skip_already_executed=plan.publish_id in executed_publish_ids,
            confirm_real_publish=confirm_real_publish,
            redline_rules=redline_rules,
        )
        for plan in plans
    )
    if execute and ledger_path:
        executed_results = tuple(result for result in results if result.status == "executed")
        if executed_results:
            _append_publish_ledger_records(ledger_path, executed_results)
    executed_operations = sum(
        1 for result in results for operation in result.operations if operation.status == "executed"
    )
    blocked_operations = sum(
        1 for result in results for operation in result.operations if operation.status.startswith("blocked")
    )
    skipped_operations = sum(
        1 for result in results for operation in result.operations if operation.status.startswith("skipped")
    )
    report = PublishOutboxReport(
        outbox_path=str(outbox_path),
        execute=execute,
        write_enabled=write_enabled,
        total=len(plans) + invalid_lines,
        processed=len(plans),
        invalid_lines=invalid_lines,
        executed_operations=executed_operations,
        blocked_operations=blocked_operations,
        skipped_operations=skipped_operations,
        status=_report_status(results, invalid_lines=invalid_lines),
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


def render_wiki_body(raw: RawDocument, *, sub_kb_id: str) -> str:
    metadata = raw.metadata
    body = _body_without_duplicate_title(raw.body, raw.title)
    lines = [
        f"# {raw.title}",
        "",
        "> 本文由 Code-KB 人工审核候选生成。发布前仍需确认目标目录、owner 与业务上下文。",
        "",
        body,
        "",
        "## KB Metadata",
        "",
        f"- candidate_id: {metadata.get('candidate_id', raw.docid)}",
        f"- sub_kb_id: {sub_kb_id}",
        f"- source_type: {metadata.get('source_type', '')}",
        f"- source_ref: {metadata.get('source_ref', '')}",
        f"- approved_at: {metadata.get('approved_at', '')}",
        f"- dedupe_key: {metadata.get('dedupe_key', '')}",
    ]
    conflict = str(metadata.get("conflict_candidate_id", "") or "").strip()
    if conflict:
        lines.append(f"- conflict_candidate_id: {conflict}")
    return "\n".join(lines).strip() + "\n"


def _operations_for_mode(
    raw: RawDocument,
    *,
    body: str,
    mode: str,
    target_parentid: str,
    template_docid: str,
    index_docid: str,
) -> tuple[PublishOperation, ...]:
    candidate_id = str(raw.metadata.get("candidate_id") or raw.docid)
    if mode == "manual":
        return (
            PublishOperation(
                tool="manual_publish",
                params={
                    "title": raw.title,
                    "body": body,
                    "candidate_id": candidate_id,
                },
                risk="no_wiki_write",
            ),
        )
    if mode == "index_page":
        return (
            PublishOperation(
                tool="saveDocumentParts",
                params={
                    "id": _wiki_id(index_docid, field="index_docid"),
                    "title": raw.title,
                    "after": _index_entry(raw),
                    "candidate_id": candidate_id,
                },
                risk="writes_kb_owned_index_page_only",
            ),
        )
    return (
        PublishOperation(
            tool="copyDocument",
            params={
                "docid": _wiki_id(template_docid, field="template_docid"),
                "new_parentid": _wiki_id(target_parentid, field="target_parentid"),
                "is_single": 1,
                "language": "zh_CN",
                "candidate_id": candidate_id,
            },
            risk="requires_kb_owned_template_and_parent",
        ),
        PublishOperation(
            tool="saveDocument",
            params={
                "docid": "<copied_docid>",
                "title": raw.title,
                "body": body,
                "candidate_id": candidate_id,
            },
            risk="writes_copied_kb_owned_document_only",
        ),
    )


def _index_entry(raw: RawDocument) -> str:
    candidate_id = str(raw.metadata.get("candidate_id") or raw.docid)
    source_ref = str(raw.metadata.get("source_ref", "") or "")
    return (
        f"\n- [{raw.title}](pending://{raw.metadata.get('sub_kb_id', '')}/{candidate_id})"
        f" - candidate_id={candidate_id}; source_ref={source_ref}\n"
    )


def _body_without_duplicate_title(body: str, title: str) -> str:
    lines = body.strip().splitlines()
    if lines and lines[0].strip() == f"# {title}".strip():
        return "\n".join(lines[1:]).strip()
    return body.strip()


def _read_outbox(path: Path, *, limit: int) -> tuple[tuple[PublishPlan, ...], int]:
    if not path.exists():
        return (), 0
    plans: list[PublishPlan] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(plans) >= limit:
            break
        if not line.strip():
            continue
        try:
            plans.append(_plan_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_lines += 1
    return tuple(plans), invalid_lines


def _plan_from_dict(payload: dict[str, Any]) -> PublishPlan:
    return PublishPlan(
        publish_id=str(payload.get("publish_id", "")).strip(),
        candidate_id=str(payload.get("candidate_id", "")).strip(),
        sub_kb_id=str(payload.get("sub_kb_id", "")).strip(),
        title=str(payload.get("title", "")).strip(),
        source_path=str(payload.get("source_path", "")).strip(),
        mode=str(payload.get("mode", "")).strip(),
        status=str(payload.get("status", "planned")).strip(),
        rendered_body=str(payload.get("rendered_body", "")),
        operations=tuple(_operation_from_dict(item) for item in payload.get("operations", [])),
        created_at=str(payload.get("created_at", "")).strip(),
    )


def _operation_from_dict(payload: dict[str, Any]) -> PublishOperation:
    return PublishOperation(
        tool=str(payload.get("tool", "")).strip(),
        params=dict(payload.get("params", {})),
        risk=str(payload.get("risk", "")).strip(),
    )


def _process_plan(
    plan: PublishPlan,
    *,
    execute: bool,
    write_enabled: bool,
    client: WikiPublishClient | None,
    skip_already_executed: bool = False,
    confirm_real_publish: bool = False,
    redline_rules: Sequence[RedlineRule] | None = None,
) -> PublishPlanResult:
    if skip_already_executed:
        return PublishPlanResult(
            publish_id=plan.publish_id,
            candidate_id=plan.candidate_id,
            mode=plan.mode,
            status="skipped",
            operations=tuple(
                PublishOperationResult(
                    tool=operation.tool,
                    status="skipped_already_executed",
                    detail="publish_id already exists in publish ledger",
                    params=operation.params,
                    response={},
                )
                for operation in plan.operations
            ),
        )
    copied_docid = ""
    operation_results: list[PublishOperationResult] = []
    for operation in plan.operations:
        result, copied_docid = _process_operation(
            operation,
            execute=execute,
            write_enabled=write_enabled,
            client=client,
            copied_docid=copied_docid,
            confirm_real_publish=confirm_real_publish,
            rendered_body=plan.rendered_body,
            redline_rules=redline_rules,
        )
        operation_results.append(result)
    statuses = {item.status for item in operation_results}
    if "blocked_confirmation_required" in statuses:
        status = "blocked_confirmation_required"
    elif any(status.startswith("blocked") for status in statuses):
        status = "blocked"
    elif "executed" in statuses:
        status = "executed"
    elif "manual_required" in statuses:
        status = "manual_required"
    else:
        status = "validated"
    return PublishPlanResult(
        publish_id=plan.publish_id,
        candidate_id=plan.candidate_id,
        mode=plan.mode,
        status=status,
        operations=tuple(operation_results),
    )


def _process_operation(
    operation: PublishOperation,
    *,
    execute: bool,
    write_enabled: bool,
    client: WikiPublishClient | None,
    copied_docid: str,
    confirm_real_publish: bool = False,
    rendered_body: str = "",
    redline_rules: Sequence[RedlineRule] | None = None,
) -> tuple[PublishOperationResult, str]:
    if operation.tool == "manual_publish":
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="manual_required",
                detail="manual_publish requires human action",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    if operation.tool not in WRITE_TOOLS:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_unknown_tool",
                detail=f"unsupported publish tool: {operation.tool}",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    if not execute:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="validated",
                detail="dry_run",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    if not confirm_real_publish:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_confirmation_required",
                detail="execute=true requires confirm_real_publish=true before Wiki writes",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    if not write_enabled:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_write_disabled",
                detail="set CODEKB_ENABLE_WIKI_WRITE=1 to execute Wiki writes",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    if client is None:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_missing_client",
                detail="no Wiki publish client configured",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    redline = scan_operation_redline(rendered_body, operation.params, rules=redline_rules)
    if redline.matched:
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_redline",
                detail="publish redline matched: " + ", ".join(redline.matched_rules),
                params=operation.params,
                response={"redline": redline.to_dict()},
            ),
            copied_docid,
        )
    try:
        response, next_copied_docid = _execute_operation(client, operation, copied_docid=copied_docid)
    except Exception as exc:  # pragma: no cover - 对接真实 Wiki 客户端时的防御性兜底
        return (
            PublishOperationResult(
                tool=operation.tool,
                status="blocked_execute_error",
                detail=f"{exc.__class__.__name__}: {exc}",
                params=operation.params,
                response={},
            ),
            copied_docid,
        )
    return (
        PublishOperationResult(
            tool=operation.tool,
            status="executed",
            detail="ok",
            params=operation.params,
            response=response,
        ),
        next_copied_docid or copied_docid,
    )


def _execute_operation(
    client: WikiPublishClient,
    operation: PublishOperation,
    *,
    copied_docid: str,
) -> tuple[dict[str, Any], str]:
    params = operation.params
    if operation.tool == "saveDocumentParts":
        return (
            client.save_document_parts(
                id=_wiki_id(params["id"], field="id"),
                title=str(params["title"]),
                after=str(params.get("after", "")),
                before=str(params.get("before", "")),
            ),
            copied_docid,
        )
    if operation.tool == "copyDocument":
        response = client.copy_document(
            docid=_wiki_id(params["docid"], field="docid"),
            new_parentid=_wiki_id(params["new_parentid"], field="new_parentid"),
            is_single=int(params.get("is_single", 1) or 1),
            language=str(params.get("language", "zh_CN") or "zh_CN"),
        )
        return response, str(response.get("docid") or response.get("contentid") or "")
    docid = str(params["docid"])
    if docid == "<copied_docid>":
        docid = copied_docid
    if not docid:
        raise ValueError("saveDocument requires docid")
    return (
        client.save_document(
            docid=_wiki_id(docid, field="docid"),
            title=str(params["title"]),
            body=str(params["body"]),
            is_html=bool(params.get("is_html", False)),
            raw=bool(params.get("raw", False)),
        ),
        copied_docid,
    )


def _report_status(results: tuple[PublishPlanResult, ...], *, invalid_lines: int) -> str:
    if invalid_lines:
        return "partial"
    if results and all(result.status == "skipped" for result in results):
        return "skipped"
    if any(result.status == "blocked_confirmation_required" for result in results):
        return "blocked_confirmation_required"
    if any(result.status == "blocked" for result in results):
        return "blocked"
    if any(result.status == "executed" for result in results):
        return "executed"
    if any(result.status == "manual_required" for result in results):
        return "manual_required"
    return "validated"


def _read_executed_publish_ids(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    ledger_path = Path(path)
    if not ledger_path.exists():
        return set()
    publish_ids: set[str] = set()
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("status", "")).strip() == "executed":
            publish_id = str(payload.get("publish_id", "")).strip()
            if publish_id:
                publish_ids.add(publish_id)
    return publish_ids


def _append_publish_ledger_records(path: str | Path, results) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as file:
        for result in results:
            file.write(
                json.dumps(
                    {
                        "publish_id": result.publish_id,
                        "candidate_id": result.candidate_id,
                        "mode": result.mode,
                        "status": result.status,
                        "operations": [operation.to_dict() for operation in result.operations],
                        "created_at": _now(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def _normalize_mode(mode: str) -> str:
    mode = str(mode or "").strip()
    if mode not in PUBLISH_MODES:
        raise ValueError("mode must be manual, index_page, or template_copy")
    return mode


def _validate_mode_requirements(
    mode: str,
    *,
    target_parentid: str,
    template_docid: str,
    index_docid: str,
) -> None:
    if mode == "index_page" and not str(index_docid).strip():
        raise ValueError("index_docid is required for index_page mode")
    if mode == "template_copy":
        if not str(target_parentid).strip():
            raise ValueError("target_parentid is required for template_copy mode")
        if not str(template_docid).strip():
            raise ValueError("template_docid is required for template_copy mode")


def _wiki_id(value: object, *, field: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a numeric Wiki docid") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive Wiki docid")
    return parsed


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
