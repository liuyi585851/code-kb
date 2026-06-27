from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping


IM_CONFIG_KEYS = (
    "CODEKB_IM_CORP_ID",
    "CODEKB_IM_AGENT_ID",
    "CODEKB_IM_APP_SECRET",
    "CODEKB_IM_OAUTH_STATE_SECRET",
    "CODEKB_IM_OAUTH_REDIRECT_URI",
    "CODEKB_IM_CONFIRM_URL_BASE",
    "CODEKB_ENABLE_IM_SEND",
)
REQUIRED_IM_OAUTH_KEYS = (
    "CODEKB_IM_CORP_ID",
    "CODEKB_IM_AGENT_ID",
    "CODEKB_IM_APP_SECRET",
    "CODEKB_IM_OAUTH_STATE_SECRET",
)
SENSITIVE_IM_CONFIG_KEYS = {
    "CODEKB_IM_APP_SECRET",
    "CODEKB_IM_OAUTH_STATE_SECRET",
}


def write_im_config_template(
    *,
    output_path: str,
    env_file: str = "",
    api_base_url: str = "",
    force: bool = False,
) -> dict[str, Any]:
    output = Path(output_path)
    if output.exists() and not force:
        raise ValueError(f"template output already exists: {output}")
    existing = _read_env_file(env_file) if str(env_file or "").strip() else {}
    base_url = str(api_base_url or "").rstrip("/")
    redirect_uri = f"{base_url}/auth/im/oauth/callback" if base_url else ""
    confirm_url_base = f"{base_url}/auth/im/confirmations/page" if base_url else ""
    lines = [
        "# Code-KB IM configuration template",
        "# Fill this file on the server only. Do not commit it or paste values into chat.",
        "# Existing env values are not copied here, so secrets are not duplicated.",
        "",
        "CODEKB_IM_CORP_ID=",
        "CODEKB_IM_AGENT_ID=",
        "CODEKB_IM_APP_SECRET=",
    ]
    if _present(existing.get("CODEKB_IM_OAUTH_STATE_SECRET", "")):
        lines.append("# CODEKB_IM_OAUTH_STATE_SECRET already exists in the server env file.")
    else:
        lines.append("CODEKB_IM_OAUTH_STATE_SECRET=")
    lines.extend(
        [
            f"CODEKB_IM_OAUTH_REDIRECT_URI={redirect_uri}",
            f"CODEKB_IM_CONFIRM_URL_BASE={confirm_url_base}",
            "",
            "# Enable only after OAuth, current-user routing, and dry-run delivery smoke pass:",
            "# CODEKB_ENABLE_IM_SEND=1",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    output.chmod(0o600)
    missing = [key for key in REQUIRED_IM_OAUTH_KEYS if not _present(existing.get(key, ""))]
    return {
        "status": "template_written",
        "ok": True,
        "output": str(output),
        "file_mode": "0600",
        "env_file": str(env_file or ""),
        "api_base_url": base_url,
        "existing_keys": sorted(key for key in IM_CONFIG_KEYS if _present(existing.get(key, ""))),
        "missing_env": missing,
        "template_keys": [
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "CODEKB_IM_OAUTH_REDIRECT_URI",
            "CODEKB_IM_CONFIRM_URL_BASE",
        ],
        "output_contains_secret_values": False,
        "message": "Template written; fill it on the server, export the values, then run diagnose-im-configure --apply.",
    }


def configure_im_env(
    *,
    env_file: str,
    env: Mapping[str, str] | None = None,
    apply: bool = False,
    confirm_real_send: bool = False,
    enable_send: bool = False,
    values: Mapping[str, str] | None = None,
    template_path: str = "",
) -> dict[str, Any]:
    env = os.environ if env is None else env
    template_values = _read_env_file(template_path) if str(template_path or "").strip() else {}
    provided = _normalized_updates({**template_values, **dict(values or {})}, env)
    if _present(template_values.get("CODEKB_ENABLE_IM_SEND", "")):
        enable_send = str(template_values.get("CODEKB_ENABLE_IM_SEND", "") or "").strip() == "1" or enable_send
    existing = _read_env_file(env_file) if str(env_file or "").strip() else {}
    merged = {**existing, **provided}
    if enable_send:
        provided["CODEKB_ENABLE_IM_SEND"] = "1"
        merged["CODEKB_ENABLE_IM_SEND"] = "1"

    missing = [key for key in REQUIRED_IM_OAUTH_KEYS if not _present(merged.get(key, ""))]
    planned_updates = {key: value for key, value in provided.items() if _present(value)}
    if enable_send and not confirm_real_send:
        return _report(
            status="confirmation_required",
            ok=False,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates=planned_updates,
            missing=missing,
            enable_send_requested=enable_send,
            source_template=template_path,
            message="Refusing to enable real IM sends until --confirm-real-send is provided.",
        )
    if missing:
        return _report(
            status="blocked_missing_inputs",
            ok=False,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates=planned_updates,
            missing=missing,
            enable_send_requested=enable_send,
            source_template=template_path,
            message="IM OAuth configuration is incomplete; env file was not changed.",
        )
    if not apply:
        status = "configured" if not planned_updates else "ready_to_apply"
        return _report(
            status=status,
            ok=True,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates=planned_updates,
            missing=[],
            enable_send_requested=enable_send,
            source_template=template_path,
            message="IM configuration is complete; rerun with --apply to update the env file." if planned_updates else "IM configuration is already complete.",
        )
    if not str(env_file or "").strip():
        raise ValueError("env_file is required when applying IM configuration")
    if not planned_updates:
        return _report(
            status="configured",
            ok=True,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates={},
            missing=[],
            enable_send_requested=enable_send,
            source_template=template_path,
            message="IM configuration is already complete; env file was not changed.",
        )
    _write_env_updates(env_file, planned_updates)
    return _report(
        status="applied",
        ok=True,
        applied=True,
        env_file=env_file,
        existing=existing,
        merged=merged,
        planned_updates=planned_updates,
        missing=[],
        enable_send_requested=enable_send,
        source_template=template_path,
        message="Env file updated; restart API/MCP/worker processes and rerun diagnose-readiness.",
    )


def _normalized_updates(values: Mapping[str, str], env: Mapping[str, str]) -> dict[str, str]:
    updates: dict[str, str] = {}
    aliases = {
        "corp_id": "CODEKB_IM_CORP_ID",
        "agent_id": "CODEKB_IM_AGENT_ID",
        "app_secret": "CODEKB_IM_APP_SECRET",
        "oauth_state_secret": "CODEKB_IM_OAUTH_STATE_SECRET",
        "redirect_uri": "CODEKB_IM_OAUTH_REDIRECT_URI",
        "confirm_url_base": "CODEKB_IM_CONFIRM_URL_BASE",
    }
    for source, target in aliases.items():
        value = values.get(source, "") or values.get(target, "") or env.get(target, "")
        normalized = _clean_value(value)
        if normalized:
            updates[target] = normalized
    return updates


def _report(
    *,
    status: str,
    ok: bool,
    applied: bool,
    env_file: str,
    existing: Mapping[str, str],
    merged: Mapping[str, str],
    planned_updates: Mapping[str, str],
    missing: list[str],
    enable_send_requested: bool,
    message: str,
    source_template: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": ok,
        "applied": applied,
        "env_file": str(env_file or ""),
        "missing_env": missing,
        "planned_update_keys": sorted(planned_updates),
        "restart_required": applied,
        "enable_send_requested": enable_send_requested,
        "im_send_enabled_after_apply": str(merged.get("CODEKB_ENABLE_IM_SEND", "") or "").strip() == "1",
        "configured": not missing,
            "keys": {
            key: {
                "configured": _present(merged.get(key, "")),
                "existing": _present(existing.get(key, "")),
                "will_update": key in planned_updates,
                "sensitive": key in SENSITIVE_IM_CONFIG_KEYS,
                "sha256_prefix": _hash_prefix(merged.get(key, "")),
            }
            for key in IM_CONFIG_KEYS
        },
        "source_template": str(source_template or ""),
        "message": message,
    }


def _read_env_file(path: str) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _valid_env_key(key):
            raise ValueError(f"invalid env file line {line_number}: invalid key")
        values[key] = _parse_env_value(value.strip())
    return values


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _write_env_updates(path: str, updates: Mapping[str, str]) -> None:
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
    if not _valid_env_key(key):
        return ""
    return key


def _valid_env_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in key)


def _clean_value(value: str) -> str:
    normalized = str(value or "").strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("environment values must not contain newlines")
    return normalized


def _present(value: str) -> bool:
    return bool(str(value or "").strip())


def _hash_prefix(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
