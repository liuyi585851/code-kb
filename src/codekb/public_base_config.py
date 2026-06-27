from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PUBLIC_BASE_KEYS = (
    "CODEKB_API_BASE_URL",
    "CODEKB_IM_OAUTH_REDIRECT_URI",
    "CODEKB_IM_CONFIRM_URL_BASE",
)


def configure_public_base_env(
    *,
    env_file: str,
    api_base_url: str,
    apply: bool = False,
) -> dict[str, Any]:
    base_url = _normalize_base_url(api_base_url)
    urls = {
        "api_base_url": base_url,
        "oauth_redirect_uri": f"{base_url}/auth/im/oauth/callback",
        "confirm_url_base": f"{base_url}/auth/im/confirmations/page",
        "mcp_setup_url": f"{base_url}/auth/im/mcp/setup",
    }
    updates = {
        "CODEKB_API_BASE_URL": urls["api_base_url"],
        "CODEKB_IM_OAUTH_REDIRECT_URI": urls["oauth_redirect_uri"],
        "CODEKB_IM_CONFIRM_URL_BASE": urls["confirm_url_base"],
    }
    if not apply:
        return _report(
            status="ready_to_apply",
            ok=True,
            applied=False,
            env_file=env_file,
            updates=updates,
            urls=urls,
            message="Public API base URL is valid; rerun with --apply to update the env file.",
        )
    if not str(env_file or "").strip():
        raise ValueError("env_file is required when applying public base URL configuration")
    _write_env_updates(env_file, updates)
    return _report(
        status="applied",
        ok=True,
        applied=True,
        env_file=env_file,
        updates=updates,
        urls=urls,
        message="Env file updated; restart API/MCP/worker processes and rerun diagnose-readiness.",
    )


def _report(
    *,
    status: str,
    ok: bool,
    applied: bool,
    env_file: str,
    updates: dict[str, str],
    urls: dict[str, str],
    message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": ok,
        "applied": applied,
        "env_file": str(env_file or ""),
        "planned_update_keys": sorted(updates),
        "restart_required": applied,
        "urls": urls,
        "secret_values_written": False,
        "message": message,
    }


def _normalize_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("api_base_url is required")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_base_url must be an absolute http(s) URL")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("api_base_url must not include params, query, or fragment")
    return normalized


def _write_env_updates(path: str, updates: dict[str, str]) -> None:
    env_path = Path(path)
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced: set[str] = set()
    output_lines: list[str] = []
    for line in existing_lines:
        key = _env_line_key(line)
        if key in updates:
            output_lines.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            output_lines.append(line)
    missing = [key for key in updates if key not in replaced]
    if missing and output_lines and output_lines[-1].strip():
        output_lines.append("")
    for key in missing:
        output_lines.append(f"{key}={updates[key]}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def _env_line_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return ""
    key = stripped.split("=", 1)[0].strip()
    if not key or not (key[0].isalpha() or key[0] == "_"):
        return ""
    if not all(char.isalnum() or char == "_" for char in key):
        return ""
    return key
