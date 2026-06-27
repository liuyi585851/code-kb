from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PATTERN = (
    r"password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|private[_-]?token|user[_-]?ticket|secret|client[_-]?secret|"
    r"app[_-]?secret|corp[_-]?secret|corpsecret|webhook[_-]?secret|secret[_-]?key|"
    r"api[_-]?key|access[_-]?key|x-[a-z0-9-]+-security-token|authorization|session[_-]?id|"
    r"sign|signature"
)
_SENSITIVE_KEY_RE = re.compile(rf"^(?:{_SENSITIVE_KEY_PATTERN})$", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_HEADER_RE = re.compile(r"(?im)^(\s*(?:authorization|cookie|set-cookie)\s*:\s*).*$")
_JSON_PAIR_RE = re.compile(
    rf"(?i)([\"'])((?:{_SENSITIVE_KEY_PATTERN}))\1(\s*:\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|true|false|null|-?\d+(?:\.\d+)?|[^\s,}\]]+)"
)
_ASSIGNMENT_RE = re.compile(rf"(?i)(\b(?:{_SENSITIVE_KEY_PATTERN})\b\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
_CLI_ARG_RE = re.compile(rf"(?i)(--(?:{_SENSITIVE_KEY_PATTERN})\b(?:=|\s+))(\"[^\"]*\"|'[^']*'|[^\s]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")


def redact_sensitive_text(value: str) -> str:
    if not value:
        return value
    redacted = _PRIVATE_KEY_RE.sub(REDACTED, value)
    redacted = _HEADER_RE.sub(lambda match: match.group(1) + REDACTED, redacted)
    redacted = _JSON_PAIR_RE.sub(_redact_json_pair, redacted)
    redacted = _ASSIGNMENT_RE.sub(_redact_assignment, redacted)
    redacted = _CLI_ARG_RE.sub(_redact_assignment, redacted)
    redacted = _BEARER_RE.sub("Bearer " + REDACTED, redacted)
    redacted = _AWS_ACCESS_KEY_RE.sub(REDACTED, redacted)
    return redacted


def redact_sensitive_url(value: str) -> str:
    if not value:
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return redact_sensitive_text(value)
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return redact_sensitive_text(value)

    query = urlencode(
        [
            (key, REDACTED if _is_sensitive_key(key) else item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return redact_sensitive_text(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)))


def _is_sensitive_key(value: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.match(value.strip()))


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_url(value)
    return value


def find_sensitive_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []
    _collect_sensitive_values(value, values=values, sensitive_parent=False)
    return tuple(dict.fromkeys(item for item in values if item and item != REDACTED))


def _collect_sensitive_values(value: Any, *, values: list[str], sensitive_parent: bool) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _collect_sensitive_values(item, values=values, sensitive_parent=sensitive_parent or _is_sensitive_key(str(key)))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_sensitive_values(item, values=values, sensitive_parent=sensitive_parent)
        return
    if value in (None, ""):
        return
    text = str(value)
    if sensitive_parent:
        values.extend(_leaf_sensitive_strings(value))
    values.extend(_sensitive_values_in_text(text))


def _leaf_sensitive_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_leaf_sensitive_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_leaf_sensitive_strings(item))
        return found
    if value in (None, ""):
        return []
    return [str(value)]


def _sensitive_values_in_text(value: str) -> list[str]:
    found: list[str] = []
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme and parsed.netloc and parsed.query:
        found.extend(item_value for key, item_value in parse_qsl(parsed.query, keep_blank_values=True) if _is_sensitive_key(key))
    found.extend(_strip_wrapping_quotes(match.group(4)) for match in _JSON_PAIR_RE.finditer(value))
    found.extend(_strip_wrapping_quotes(match.group(2)) for match in _ASSIGNMENT_RE.finditer(value))
    found.extend(_strip_wrapping_quotes(match.group(2)) for match in _CLI_ARG_RE.finditer(value))
    for match in _BEARER_RE.finditer(value):
        parts = match.group(0).split(None, 1)
        if len(parts) == 2:
            found.append(parts[1])
    return found


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group(2)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return match.group(1) + value[0] + REDACTED + value[-1]
    return match.group(1) + REDACTED


def _redact_json_pair(match: re.Match[str]) -> str:
    value = match.group(4)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        redacted_value = value[0] + REDACTED + value[-1]
    else:
        redacted_value = REDACTED
    return match.group(1) + match.group(2) + match.group(1) + match.group(3) + redacted_value
