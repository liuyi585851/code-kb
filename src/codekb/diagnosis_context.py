from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .redaction import redact_sensitive_text, redact_sensitive_url


_FIELD_LIMITS = {
    "surface": 120,
    "repo": 300,
    "branch": 200,
    "commit": 120,
    "mr_id": 120,
    "build_id": 120,
    "job_name": 300,
    "error_code": 200,
    "error_text": 2000,
    "log_excerpt": 6000,
}
_QUERY_LIMIT = 1200
_MAX_TAGS = 20
_MAX_LINKS = 20


@dataclass(frozen=True)
class DiagnosticContext:
    surface: str = ""
    repo: str = ""
    branch: str = ""
    commit: str = ""
    mr_id: str = ""
    build_id: str = ""
    job_name: str = ""
    error_code: str = ""
    error_text: str = ""
    log_excerpt: str = ""
    tags: tuple[str, ...] = ()
    links: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "repo": self.repo,
            "branch": self.branch,
            "commit": self.commit,
            "mr_id": self.mr_id,
            "build_id": self.build_id,
            "job_name": self.job_name,
            "error_code": self.error_code,
            "error_text": self.error_text,
            "log_excerpt": self.log_excerpt,
            "tags": list(self.tags),
            "links": dict(self.links or {}),
        }

    def non_empty_items(self) -> tuple[tuple[str, Any], ...]:
        payload = self.to_dict()
        return tuple((key, value) for key, value in payload.items() if value not in ("", [], {}))

    def is_empty(self) -> bool:
        return not self.non_empty_items()


def parse_diagnostic_context(value: object | None) -> DiagnosticContext:
    if value in (None, "", {}):
        return DiagnosticContext()
    if not isinstance(value, dict):
        raise ValueError("context must be a JSON object")

    fields = {field: _normalize_context_field(field, value.get(field), limit) for field, limit in _FIELD_LIMITS.items()}
    tags = _parse_tags(value.get("tags"))
    links = _parse_links(value.get("links"))
    return DiagnosticContext(**fields, tags=tags, links=links)


def build_diagnostic_query(query: object | None, context: DiagnosticContext | None = None) -> str:
    normalized_query = redact_sensitive_text(_normalize_text(query, _QUERY_LIMIT))
    if normalized_query:
        return normalized_query

    context = context or DiagnosticContext()
    if not any((context.error_code, context.error_text, context.log_excerpt)):
        raise ValueError("query is required when diagnostic context has no error text")

    parts: list[str] = []
    if context.error_code:
        parts.append(f"error_code={context.error_code}")
    if context.error_text:
        parts.append(context.error_text)
    if context.log_excerpt:
        parts.append("log_excerpt=" + _log_query_excerpt(context.log_excerpt))
    if context.job_name:
        parts.append(f"job={context.job_name}")
    if context.repo:
        parts.append(f"repo={context.repo}")
    if context.branch:
        parts.append(f"branch={context.branch}")
    if context.mr_id:
        parts.append(f"mr={context.mr_id}")
    if context.build_id:
        parts.append(f"build={context.build_id}")
    if context.tags:
        parts.append("tags=" + ",".join(context.tags[:5]))
    return redact_sensitive_text(_truncate(" ".join(parts), _QUERY_LIMIT))


def _normalize_text(value: object | None, limit: int) -> str:
    if value in (None, ""):
        return ""
    return _truncate(str(value).strip(), limit)


def _normalize_context_field(field: str, value: object | None, limit: int) -> str:
    text = _normalize_text(value, limit)
    if field in {"error_text", "log_excerpt"}:
        return redact_sensitive_text(text)
    return text


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()


def _parse_tags(value: object | None) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    raw_tags: list[object]
    if isinstance(value, str):
        raw_tags = value.split(",")
    elif isinstance(value, list):
        raw_tags = value
    else:
        raise ValueError("context.tags must be a list or comma-separated string")

    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_tags:
        tag = _normalize_text(item, 80)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= _MAX_TAGS:
            break
    return tuple(tags)


def _parse_links(value: object | None) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("context.links must be a JSON object")
    links: dict[str, str] = {}
    for key, link_value in value.items():
        link_key = _normalize_text(key, 120)
        link_text = redact_sensitive_url(_normalize_text(link_value, 1000))
        if not link_key or not link_text:
            continue
        links[link_key] = link_text
        if len(links) >= _MAX_LINKS:
            break
    return links


def _log_query_excerpt(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return " | ".join(lines[:8]) if lines else value.strip()
