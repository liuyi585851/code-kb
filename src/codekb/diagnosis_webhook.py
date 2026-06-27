from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from .diagnosis_context import build_diagnostic_query, parse_diagnostic_context
from .redaction import find_sensitive_values, redact_sensitive_data, redact_sensitive_text, redact_sensitive_url


SUPPORTED_WEBHOOK_SOURCE_CHOICES = ("code_review", "ci", "mr", "issue_tracker", "crash", "generic")
SUPPORTED_WEBHOOK_SOURCES = set(SUPPORTED_WEBHOOK_SOURCE_CHOICES)
DEFAULT_WEBHOOK_MAPPING_PATH = "docs/diagnose-webhook-mapping.draft.yaml"
DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH = "docs/diagnose-webhook-samples.draft.yaml"
VALIDATION_CONTEXT_FIELDS = (
    "repo",
    "branch",
    "commit",
    "mr_id",
    "build_id",
    "job_name",
    "error_code",
    "error_text",
    "log_excerpt",
    "tags",
    "links",
)

BUILTIN_WEBHOOK_MAPPING: dict[str, Any] = {
    "query_paths": ("query", "question", "diagnostic_query"),
    "sub_kbs_paths": ("sub_kbs", "sub_kb", "kb"),
    "submitted_by_hash_paths": ("submitted_by_hash", "user_id_hash", "operator_hash"),
    "option_paths": {
        "top_k": ("top_k",),
        "min_confidence": ("min_confidence",),
        "include_governance": ("include_governance",),
        "allow_duplicate": ("allow_duplicate",),
    },
    "context_paths": {
        "repo": ("repo", "repository.path", "repository.full_name", "repository.name", "project.path", "project.name"),
        "branch": (
            "branch",
            "branch_name",
            "source_branch",
            "ref",
            "git.branch",
            "merge_request.source_branch",
            "mr.source_branch",
        ),
        "commit": ("commit", "sha", "head_sha", "git.commit", "merge_request.head_sha", "mr.head_sha"),
        "mr_id": ("mr_id", "merge_request.id", "merge_request.iid", "mr.id", "mr.iid"),
        "build_id": ("build_id", "pipeline_id", "run_id", "pipeline.id", "build.id", "job.id"),
        "job_name": ("job_name", "job.name", "build.job_name", "build.name", "pipeline.job_name"),
        "error_code": ("error_code", "failure_type", "status", "error.code", "failure.code"),
        "error_text": ("error_text", "message", "summary", "error.message", "error.text", "failure.message"),
        "log_excerpt": ("log_excerpt", "log", "log_tail", "error.log", "build.log", "job.log"),
    },
    "link_paths": {
        "repo": ("repo_url", "repository.url", "project.url"),
        "mr": ("mr_url", "merge_request.url", "mr.url"),
        "build": ("build_url", "pipeline_url", "pipeline.url", "build.url"),
        "job": ("job_url", "job.url"),
    },
    "tag_paths": ("tags", "event", "event_type", "trigger"),
}


@dataclass(frozen=True)
class DiagnosticWebhookMapping:
    path: str
    exists: bool
    sources: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "sources": {source: _mapping_to_dict(mapping) for source, mapping in self.sources.items()},
        }


@dataclass(frozen=True)
class DiagnosticWebhookEvent:
    event_id: str
    created_at: str
    source: str
    action: str
    status: str
    diagnosis_id: str
    answer_id: str
    trace_id: str
    query: str
    sub_kbs: tuple[str, ...]
    context: dict[str, Any]
    refused: bool
    confidence: float
    citation_docids: tuple[str, ...]
    finding_types: tuple[str, ...]
    gap_submission: dict[str, Any]
    error: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "created_at": self.created_at,
            "source": self.source,
            "action": self.action,
            "status": self.status,
            "diagnosis_id": self.diagnosis_id,
            "answer_id": self.answer_id,
            "trace_id": self.trace_id,
            "query": self.query,
            "sub_kbs": list(self.sub_kbs),
            "context": dict(self.context),
            "refused": self.refused,
            "confidence": self.confidence,
            "citation_docids": list(self.citation_docids),
            "finding_types": list(self.finding_types),
            "gap_submission": dict(self.gap_submission),
            "error": dict(self.error),
        }


class JsonlDiagnosticWebhookStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        source: str,
        action: str,
        status: str,
        diagnosis,
        submission=None,
    ) -> DiagnosticWebhookEvent:
        event = DiagnosticWebhookEvent(
            event_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            source=source,
            action=action,
            status=status,
            diagnosis_id=diagnosis.diagnosis_id,
            answer_id=diagnosis.answer_id,
            trace_id=diagnosis.trace_id,
            query=diagnosis.query,
            sub_kbs=tuple(diagnosis.sub_kbs),
            context=diagnosis.context.to_dict(),
            refused=diagnosis.refused,
            confidence=diagnosis.confidence,
            citation_docids=tuple(citation.docid for citation in diagnosis.citations),
            finding_types=tuple(finding.finding_type for finding in diagnosis.findings),
            gap_submission=_gap_submission_summary(submission),
            error={},
        )
        self._write(event)
        return event

    def append_failure(
        self,
        *,
        source: str,
        action: str,
        status: str,
        error_type: str,
        error_message: str,
        normalized: dict[str, Any] | None = None,
    ) -> DiagnosticWebhookEvent:
        normalized = normalized or {}
        context = _redacted_context(normalized.get("context"))
        event = DiagnosticWebhookEvent(
            event_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            source=str(source or "").strip(),
            action=action,
            status=status,
            diagnosis_id="",
            answer_id="",
            trace_id="",
            query=redact_sensitive_text(str(normalized.get("query", "") or "").strip()),
            sub_kbs=_sub_kbs_tuple(normalized.get("sub_kbs")),
            context=context,
            refused=False,
            confidence=0.0,
            citation_docids=(),
            finding_types=(),
            gap_submission={},
            error={
                "type": redact_sensitive_text(error_type),
                "message": redact_sensitive_text(error_message),
            },
        )
        self._write(event)
        return event

    def _write(self, event: DiagnosticWebhookEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def summary(
        self,
        *,
        source: str | None = None,
        status: str | None = None,
        action: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        events, invalid_lines = load_diagnostic_webhook_events(self.path)
        filtered = _filter_events(events, source=source, status=status, action=action)
        recent = tuple(sorted(filtered, key=lambda item: item.created_at, reverse=True)[: max(0, limit)])
        return {
            "path": str(self.path),
            "total": len(filtered),
            "unfiltered_total": len(events),
            "invalid_lines": invalid_lines,
            "filters": {
                "source": source or "",
                "status": status or "",
                "action": action or "",
            },
            "by_source": dict(Counter(event.source for event in filtered)),
            "by_status": dict(Counter(event.status for event in filtered)),
            "by_action": dict(Counter(event.action for event in filtered)),
            "latest_created_at": max((event.created_at for event in filtered), default=""),
            "events": [event.to_dict() for event in recent],
        }


def load_diagnostic_webhook_events(path: str | Path) -> tuple[tuple[DiagnosticWebhookEvent, ...], int]:
    event_path = Path(path)
    if not event_path.exists():
        return (), 0
    events: list[DiagnosticWebhookEvent] = []
    invalid_lines = 0
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(_event_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid_lines += 1
    return tuple(events), invalid_lines


def load_diagnostic_webhook_mapping(path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH) -> DiagnosticWebhookMapping:
    if not str(path).strip():
        return DiagnosticWebhookMapping(path="", exists=False, sources={})
    mapping_path = Path(path)
    sources: dict[str, dict[str, Any]] = {}
    if mapping_path.exists():
        data = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("diagnose webhook mapping yaml must be a mapping")
        raw_sources = data.get("sources", {})
        if not isinstance(raw_sources, dict):
            raise ValueError("diagnose webhook mapping sources must be a mapping")
        for source, source_data in raw_sources.items():
            normalized_source = str(source).strip().lower()
            if normalized_source not in SUPPORTED_WEBHOOK_SOURCES and normalized_source != "default":
                raise ValueError(f"unsupported diagnose webhook mapping source: {source}")
            if not isinstance(source_data, dict):
                raise ValueError(f"diagnose webhook mapping source must be a mapping: {source}")
            sources[normalized_source] = _normalize_mapping_config(source_data)

    return DiagnosticWebhookMapping(path=str(mapping_path), exists=mapping_path.exists(), sources=sources)


def effective_diagnostic_webhook_mapping(
    source: str,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
) -> DiagnosticWebhookMapping:
    loaded = load_diagnostic_webhook_mapping(mapping_path)
    normalized_source = source.strip().lower()
    if normalized_source not in SUPPORTED_WEBHOOK_SOURCES:
        raise ValueError(_unsupported_source_message())

    merged_sources: dict[str, dict[str, Any]] = {}
    for item in ("default", normalized_source):
        merged_sources[item] = _merge_mapping_configs(
            BUILTIN_WEBHOOK_MAPPING,
            loaded.sources.get("default", {}),
            loaded.sources.get(item, {}) if item != "default" else {},
        )
    return DiagnosticWebhookMapping(path=loaded.path, exists=loaded.exists, sources=merged_sources)


def normalize_diagnostic_webhook(
    source: str,
    payload: dict[str, Any],
    *,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("webhook payload must be a JSON object")
    normalized_source = source.strip().lower()
    if normalized_source not in SUPPORTED_WEBHOOK_SOURCES:
        raise ValueError(_unsupported_source_message())

    mapping = effective_diagnostic_webhook_mapping(normalized_source, mapping_path).sources[normalized_source]
    context = _context_from_payload(normalized_source, payload, mapping)
    result: dict[str, Any] = {
        "query": _first_path(payload, mapping["query_paths"]),
        "context": context,
    }
    for field, paths in mapping["option_paths"].items():
        value = _first_raw_path(payload, paths)
        if value not in (None, ""):
            result[field] = value

    sub_kbs = _first_raw_path(payload, mapping["sub_kbs_paths"])
    if sub_kbs not in (None, "", []):
        result["sub_kbs"] = sub_kbs

    submitted_by_hash = _first_path(payload, mapping["submitted_by_hash_paths"])
    if submitted_by_hash:
        result["submitted_by_hash"] = submitted_by_hash
    return result


def preview_diagnostic_webhook(
    source: str,
    payload: dict[str, Any],
    *,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
) -> dict[str, Any]:
    normalized = normalize_diagnostic_webhook(source, payload, mapping_path=mapping_path)
    context = parse_diagnostic_context(normalized.get("context"))
    query = build_diagnostic_query(normalized.get("query"), context)
    diagnostic_payload: dict[str, Any] = {
        "query": query,
        "context": context.to_dict(),
    }
    for field in ("top_k", "min_confidence", "include_governance", "allow_duplicate", "submitted_by_hash"):
        if field in normalized:
            diagnostic_payload[field] = normalized[field]
    if normalized.get("sub_kbs") not in (None, "", []):
        diagnostic_payload["sub_kbs"] = list(_sub_kbs_tuple(normalized.get("sub_kbs")))

    return {
        "source": source.strip().lower(),
        "diagnostic_payload": diagnostic_payload,
        "query": query,
        "context": context.to_dict(),
        "sub_kbs": list(_sub_kbs_tuple(normalized.get("sub_kbs"))),
        "options": {
            "top_k": normalized.get("top_k", ""),
            "min_confidence": normalized.get("min_confidence", ""),
            "include_governance": normalized.get("include_governance", ""),
            "allow_duplicate": normalized.get("allow_duplicate", ""),
        },
    }


def validate_diagnostic_webhook(
    source: str,
    payload: dict[str, Any],
    *,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
) -> dict[str, Any]:
    normalized_source = source.strip().lower()
    mapping = effective_diagnostic_webhook_mapping(normalized_source, mapping_path)
    normalized = normalize_diagnostic_webhook(normalized_source, payload, mapping_path=mapping_path)
    errors: list[str] = []
    warnings: list[str] = []
    query = ""
    query_ready = False

    context_dict: dict[str, Any]
    try:
        context = parse_diagnostic_context(normalized.get("context"))
        context_dict = context.to_dict()
    except ValueError as exc:
        errors.append(str(exc))
        context = None
        context_dict = _redacted_context(normalized.get("context"))

    if context is not None:
        try:
            query = build_diagnostic_query(normalized.get("query"), context)
            query_ready = True
        except ValueError as exc:
            errors.append(str(exc))

    sub_kbs = list(_sub_kbs_tuple(normalized.get("sub_kbs")))
    options = {
        field: normalized.get(field, "")
        for field in ("top_k", "min_confidence", "include_governance", "allow_duplicate")
        if normalized.get(field, "") not in (None, "")
    }
    if not context_dict.get("repo"):
        warnings.append("context.repo is missing; source attribution will be weaker")
    if not sub_kbs:
        warnings.append("sub_kbs is missing; diagnosis will search all configured KBs")

    diagnostic_payload: dict[str, Any] = {}
    if query_ready:
        diagnostic_payload = {"query": query, "context": context_dict}
        if sub_kbs:
            diagnostic_payload["sub_kbs"] = sub_kbs
        for field, value in options.items():
            diagnostic_payload[field] = value
        if normalized.get("submitted_by_hash"):
            diagnostic_payload["submitted_by_hash"] = normalized["submitted_by_hash"]

    return {
        "source": normalized_source,
        "valid": not errors,
        "query_ready": query_ready,
        "errors": errors,
        "warnings": warnings,
        "mapping": {
            "path": mapping.path,
            "exists": mapping.exists,
        },
        "extracted_fields": {
            "explicit_query": bool(str(normalized.get("query", "") or "").strip()),
            "query": query_ready,
            "sub_kbs": bool(sub_kbs),
            "submitted_by_hash": bool(str(normalized.get("submitted_by_hash", "") or "").strip()),
            "options": {
                field: field in options
                for field in ("top_k", "min_confidence", "include_governance", "allow_duplicate")
            },
            "context": {field: bool(context_dict.get(field)) for field in VALIDATION_CONTEXT_FIELDS},
            "links": {key: bool(value) for key, value in dict(context_dict.get("links") or {}).items()},
        },
        "query": query,
        "context": context_dict,
        "sub_kbs": sub_kbs,
        "options": options,
        "diagnostic_payload": diagnostic_payload,
    }


def validate_diagnostic_webhook_sample_suite(
    samples_path: str | Path = DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    *,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
) -> dict[str, Any]:
    sample_path = Path(samples_path)
    if not sample_path.exists():
        raise ValueError(f"diagnose webhook sample suite not found: {sample_path}")
    data = yaml.safe_load(sample_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("diagnose webhook sample suite yaml must be a mapping")
    raw_samples = data.get("samples", [])
    if not isinstance(raw_samples, list):
        raise ValueError("diagnose webhook sample suite samples must be a list")

    results = tuple(_validate_webhook_sample(sample, mapping_path=mapping_path) for sample in raw_samples)
    passed = sum(1 for sample in results if sample["status"] == "passed")
    failed = len(results) - passed
    return {
        "status": "passed" if failed == 0 else "failed",
        "path": str(sample_path),
        "mapping_path": str(mapping_path),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "samples": list(results),
    }


def load_diagnostic_webhook_payload_file(path: str | Path) -> dict[str, Any]:
    payload_path = Path(path)
    data = yaml.safe_load(payload_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"diagnose webhook payload file must contain a JSON/YAML object: {payload_path}")
    return data


def build_diagnostic_webhook_sample(
    *,
    source: str,
    name: str,
    payload: dict[str, Any],
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
    expected_context: dict[str, Any] | None = None,
    expected_sub_kbs: Iterable[str] | None = None,
    forbidden_values: Iterable[str] | None = None,
) -> dict[str, Any]:
    normalized_source = source.strip().lower()
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("sample name is required")
    if normalized_source not in SUPPORTED_WEBHOOK_SOURCES:
        raise ValueError(_unsupported_source_message())
    if not isinstance(payload, dict):
        raise ValueError("webhook payload must be a JSON object")

    raw_sensitive_values = find_sensitive_values(payload)
    safe_payload = redact_sensitive_data(payload)
    report = validate_diagnostic_webhook(normalized_source, safe_payload, mapping_path=mapping_path)
    sample_expected_context = dict(expected_context or _default_expected_context(report))
    sample_expected_sub_kbs = list(expected_sub_kbs) if expected_sub_kbs is not None else list(report.get("sub_kbs") or [])
    sample: dict[str, Any] = {
        "name": normalized_name,
        "source": normalized_source,
        "expected_valid": bool(report.get("valid")),
        "expected_query_ready": bool(report.get("query_ready")),
        "payload": safe_payload,
        "metadata": {
            "generated_by": "diagnose-webhook-sample-import",
            "payload_sanitized": True,
            "raw_sensitive_values_detected": len(raw_sensitive_values),
        },
    }
    if sample_expected_context:
        sample["expected_context"] = sample_expected_context
    if sample_expected_sub_kbs:
        sample["expected_sub_kbs"] = [str(item) for item in sample_expected_sub_kbs]
    explicit_forbidden_values = _sample_string_list(list(forbidden_values or []))
    if explicit_forbidden_values:
        sample["forbidden_values"] = explicit_forbidden_values

    leaked_values = _sensitive_values_leaked(sample, raw_sensitive_values)
    if leaked_values:
        raise ValueError(f"generated sample leaked {len(leaked_values)} sensitive value(s)")

    return {
        "sample": sample,
        "validation": {
            "valid": bool(report.get("valid")),
            "query_ready": bool(report.get("query_ready")),
            "errors": list(report.get("errors") or []),
            "warnings": list(report.get("warnings") or []),
        },
        "raw_sensitive_values_detected": len(raw_sensitive_values),
        "raw_sensitive_values_leaked": False,
    }


def import_diagnostic_webhook_sample(
    *,
    source: str,
    name: str,
    payload: dict[str, Any],
    output_path: str | Path,
    mapping_path: str | Path = DEFAULT_WEBHOOK_MAPPING_PATH,
    append: bool = False,
    expected_context: dict[str, Any] | None = None,
    expected_sub_kbs: Iterable[str] | None = None,
    forbidden_values: Iterable[str] | None = None,
) -> dict[str, Any]:
    built = build_diagnostic_webhook_sample(
        source=source,
        name=name,
        payload=payload,
        mapping_path=mapping_path,
        expected_context=expected_context,
        expected_sub_kbs=expected_sub_kbs,
        forbidden_values=forbidden_values,
    )
    output = Path(output_path)
    suite = _load_sample_suite_for_write(output) if append else {}
    samples = list(suite.get("samples") or [])
    samples.append(built["sample"])
    suite = {
        "version": int(suite.get("version") or 1),
        "updated_at": datetime.now(UTC).date().isoformat(),
        "status": "real_samples_draft",
        "samples": samples,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(suite, allow_unicode=True, sort_keys=False), encoding="utf-8")
    summary = validate_diagnostic_webhook_sample_suite(output, mapping_path=mapping_path)
    return {
        "status": "imported" if summary["status"] == "passed" else "imported_with_validation_errors",
        "output": str(output),
        "append": append,
        "sample_name": built["sample"]["name"],
        "source": built["sample"]["source"],
        "raw_sensitive_values_detected": built["raw_sensitive_values_detected"],
        "raw_sensitive_values_leaked": False,
        "validation": summary,
    }


def _context_from_payload(source: str, payload: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    context = dict(payload.get("context") or {})
    context.setdefault("surface", source)
    for field, paths in mapping["context_paths"].items():
        _set_if_missing(context, field, _first_path(payload, paths))

    links = dict(context.get("links") or {})
    for name, paths in mapping["link_paths"].items():
        value = _first_path(payload, paths)
        if value and name not in links:
            links[name] = value
    if links:
        context["links"] = links

    tags = _tags_from_payload(context, payload, source, mapping["tag_paths"])
    if tags:
        context["tags"] = tags
    return context


def _default_expected_context(report: dict[str, Any]) -> dict[str, Any]:
    context = dict(report.get("context") or {})
    expected: dict[str, Any] = {}
    for field in ("surface", "repo", "branch", "commit", "mr_id", "build_id", "job_name", "error_code"):
        if context.get(field):
            expected[field] = context[field]
    return expected


def _sensitive_values_leaked(sample: dict[str, Any], raw_sensitive_values: Iterable[str]) -> list[str]:
    text = yaml.safe_dump(sample, allow_unicode=True, sort_keys=False)
    return [value for value in raw_sensitive_values if value and value in text]


def _load_sample_suite_for_write(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("diagnose webhook sample suite yaml must be a mapping")
    if "samples" in data and not isinstance(data["samples"], list):
        raise ValueError("diagnose webhook sample suite samples must be a list")
    return data


def _validate_webhook_sample(sample: Any, *, mapping_path: str | Path) -> dict[str, Any]:
    if not isinstance(sample, dict):
        return {
            "name": "",
            "source": "",
            "status": "failed",
            "errors": ["sample must be a JSON object"],
            "warnings": [],
            "valid": False,
            "query_ready": False,
            "context": {},
            "sub_kbs": [],
        }
    name = str(sample.get("name", "") or "").strip()
    source = str(sample.get("source", "") or "").strip().lower()
    payload = sample.get("payload", {})
    errors: list[str] = []
    if not name:
        errors.append("sample.name is required")
    if not source:
        errors.append("sample.source is required")
    if not isinstance(payload, dict):
        errors.append("sample.payload must be a JSON object")
    if errors:
        return _webhook_sample_result(name=name, source=source, errors=errors)

    try:
        report = validate_diagnostic_webhook(source, payload, mapping_path=mapping_path)
    except ValueError as exc:
        return _webhook_sample_result(name=name, source=source, errors=[str(exc)])

    expected_valid = _sample_bool(sample.get("expected_valid"), default=True)
    expected_query_ready = _sample_bool(sample.get("expected_query_ready"), default=expected_valid)
    if bool(report["valid"]) != expected_valid:
        errors.append(f"valid expected {str(expected_valid).lower()} got {str(report['valid']).lower()}")
    if bool(report["query_ready"]) != expected_query_ready:
        errors.append(
            f"query_ready expected {str(expected_query_ready).lower()} got {str(report['query_ready']).lower()}"
        )

    expected_context = sample.get("expected_context", {}) or {}
    if not isinstance(expected_context, dict):
        errors.append("sample.expected_context must be a mapping")
        expected_context = {}
    for field, expected_value in expected_context.items():
        actual_value = dict(report.get("context") or {}).get(str(field), "")
        if not _sample_value_matches(actual_value, expected_value):
            errors.append(f"context.{field} expected {expected_value!r} got {actual_value!r}")

    expected_sub_kbs = sample.get("expected_sub_kbs")
    if expected_sub_kbs is not None:
        expected = _sample_string_list(expected_sub_kbs)
        actual = [str(item) for item in list(report.get("sub_kbs") or [])]
        if actual != expected:
            errors.append(f"sub_kbs expected {expected!r} got {actual!r}")

    report_text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for forbidden_value in _sample_string_list(sample.get("forbidden_values", [])):
        if forbidden_value and forbidden_value in report_text:
            errors.append(f"forbidden value leaked: {forbidden_value}")

    warnings = list(report.get("warnings") or [])
    return {
        "name": name,
        "source": source,
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "warnings": warnings,
        "valid": bool(report.get("valid")),
        "query_ready": bool(report.get("query_ready")),
        "context": dict(report.get("context") or {}),
        "sub_kbs": list(report.get("sub_kbs") or []),
        "query": str(report.get("query", "") or ""),
        "extracted_fields": dict(report.get("extracted_fields") or {}),
    }


def _webhook_sample_result(*, name: str, source: str, errors: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "status": "failed",
        "errors": errors,
        "warnings": [],
        "valid": False,
        "query_ready": False,
        "context": {},
        "sub_kbs": [],
        "query": "",
        "extracted_fields": {},
    }


def _sample_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return bool(value)


def _sample_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _sample_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple)):
        return _sample_string_list(actual) == _sample_string_list(expected)
    return str(actual) == str(expected)


def _unsupported_source_message() -> str:
    return "webhook source must be one of: " + ", ".join(SUPPORTED_WEBHOOK_SOURCE_CHOICES)


def _normalize_mapping_config(data: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for field in ("query_paths", "sub_kbs_paths", "submitted_by_hash_paths", "tag_paths"):
        if field in data:
            config[field] = _normalize_path_list(data[field], field)
    for field in ("context_paths", "link_paths", "option_paths"):
        if field in data:
            config[field] = _normalize_path_mapping(data[field], field)
    return config


def _merge_mapping_configs(*configs: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "query_paths": (),
        "sub_kbs_paths": (),
        "submitted_by_hash_paths": (),
        "option_paths": {},
        "context_paths": {},
        "link_paths": {},
        "tag_paths": (),
    }
    for config in configs:
        config = _normalize_mapping_config(config)
        for field in ("query_paths", "sub_kbs_paths", "submitted_by_hash_paths", "tag_paths"):
            merged[field] = _dedupe_paths((*config.get(field, ()), *merged[field]))
        for field in ("option_paths", "context_paths", "link_paths"):
            for name, paths in config.get(field, {}).items():
                merged[field][name] = _dedupe_paths((*paths, *merged[field].get(name, ())))
    return merged


def _normalize_path_mapping(value: Any, name: str) -> dict[str, tuple[tuple[str, ...], ...]]:
    if not isinstance(value, dict):
        raise ValueError(f"diagnose webhook mapping {name} must be a mapping")
    return {str(key).strip(): _normalize_path_list(paths, f"{name}.{key}") for key, paths in value.items()}


def _normalize_path_list(value: Any, name: str) -> tuple[tuple[str, ...], ...]:
    if isinstance(value, str):
        raw_paths = [value]
    elif isinstance(value, list):
        raw_paths = value
    elif isinstance(value, tuple):
        raw_paths = list(value)
    else:
        raise ValueError(f"diagnose webhook mapping {name} must be a string or list")
    paths: list[tuple[str, ...]] = []
    for item in raw_paths:
        path = _normalize_path(item, name)
        if path:
            paths.append(path)
    return tuple(paths)


def _normalize_path(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        items = tuple(part.strip() for part in value.split(".") if part.strip())
    elif isinstance(value, list):
        items = tuple(str(part).strip() for part in value if str(part).strip())
    elif isinstance(value, tuple):
        items = tuple(str(part).strip() for part in value if str(part).strip())
    else:
        raise ValueError(f"diagnose webhook mapping path must be a string or list: {name}")
    if not items:
        raise ValueError(f"diagnose webhook mapping path cannot be empty: {name}")
    return items


def _dedupe_paths(paths: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    deduped: list[tuple[str, ...]] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return tuple(deduped)


def _mapping_to_dict(mapping: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in ("query_paths", "sub_kbs_paths", "submitted_by_hash_paths", "tag_paths"):
        if field in mapping:
            result[field] = [".".join(path) for path in mapping[field]]
    for field in ("option_paths", "context_paths", "link_paths"):
        if field in mapping:
            result[field] = {key: [".".join(path) for path in paths] for key, paths in mapping[field].items()}
    return result


def _set_if_missing(payload: dict[str, Any], key: str, value: str) -> None:
    if value and not payload.get(key):
        payload[key] = value


def _tags_from_payload(
    context: dict[str, Any],
    payload: dict[str, Any],
    source: str,
    tag_paths: tuple[tuple[str, ...], ...],
) -> list[str]:
    tags: list[str] = []
    existing_tags = context.get("tags", [])
    if isinstance(existing_tags, str):
        tags.extend(item.strip() for item in existing_tags.split(","))
    elif isinstance(existing_tags, list):
        tags.extend(str(item).strip() for item in existing_tags)
    tags.append(source)
    for path in tag_paths:
        _extend_tags(tags, _get_path(payload, path))
    return [tag for tag in dict.fromkeys(tags) if tag]


def _extend_tags(tags: list[str], value: Any) -> None:
    if isinstance(value, str):
        tags.extend(item.strip() for item in value.split(","))
    elif isinstance(value, list):
        tags.extend(str(item).strip() for item in value)
    elif value not in (None, "", []):
        tags.append(str(value).strip())


def _first(payload: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return ""


def _first_path(payload: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> str:
    for path in paths:
        value = _get_path(payload, path)
        if value not in (None, "", []):
            return str(value).strip()
    return ""


def _first_raw_path(payload: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        value = _get_path(payload, path)
        if value not in (None, "", []):
            return value
    return None


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _gap_submission_summary(submission) -> dict[str, Any]:
    if submission is None:
        return {}
    return {
        "candidate_id": submission.candidate.candidate_id,
        "duplicate": submission.duplicate,
        "existing_candidate_id": submission.existing_candidate_id,
        "status": submission.candidate.status,
    }


def _event_from_dict(payload: dict[str, Any]) -> DiagnosticWebhookEvent:
    return DiagnosticWebhookEvent(
        event_id=str(payload.get("event_id", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
        source=str(payload.get("source", "")).strip(),
        action=str(payload.get("action", "")).strip(),
        status=str(payload.get("status", "")).strip(),
        diagnosis_id=str(payload.get("diagnosis_id", "")).strip(),
        answer_id=str(payload.get("answer_id", "")).strip(),
        trace_id=str(payload.get("trace_id", "")).strip(),
        query=str(payload.get("query", "")).strip(),
        sub_kbs=tuple(str(item).strip() for item in payload.get("sub_kbs", []) if str(item).strip()),
        context=dict(payload.get("context") or {}),
        refused=bool(payload.get("refused")),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        citation_docids=tuple(str(item).strip() for item in payload.get("citation_docids", []) if str(item).strip()),
        finding_types=tuple(str(item).strip() for item in payload.get("finding_types", []) if str(item).strip()),
        gap_submission=dict(payload.get("gap_submission") or {}),
        error=dict(payload.get("error") or {}),
    )


def _filter_events(
    events: tuple[DiagnosticWebhookEvent, ...],
    *,
    source: str | None,
    status: str | None,
    action: str | None,
) -> tuple[DiagnosticWebhookEvent, ...]:
    source = (source or "").strip()
    status = (status or "").strip()
    action = (action or "").strip()
    return tuple(
        event
        for event in events
        if (not source or event.source == source)
        and (not status or event.status == status)
        and (not action or event.action == action)
    )


def _sub_kbs_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),)


def _redacted_context(value: Any) -> dict[str, Any]:
    try:
        return parse_diagnostic_context(value).to_dict()
    except ValueError:
        if not isinstance(value, dict):
            return {}
        redacted = _redact_payload_value(value)
        return redacted if isinstance(redacted, dict) else {}


def _redact_payload_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            redact_sensitive_text(str(key)): _redact_payload_value(item_value)
            for key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload_value(item) for item in value]
    if value in (None, ""):
        return ""
    return redact_sensitive_url(redact_sensitive_text(str(value)))
