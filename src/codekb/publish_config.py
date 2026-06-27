from __future__ import annotations

import os
from typing import Any, Mapping, MutableMapping

from .publish import PUBLISH_MODES
from .publish_api import PUBLISH_CONFIG_KEYS
from .im_config import _hash_prefix, _present, _read_env_file, _write_env_updates


PUBLISH_ENV_KEYS = (
    "CODEKB_PUBLISH_MODE",
    "CODEKB_PUBLISH_INDEX_DOCID",
    "CODEKB_PUBLISH_TEMPLATE_DOCID",
    "CODEKB_PUBLISH_TARGET_PARENTID",
)


def configure_publish_env(
    *,
    env_file: str,
    values: Mapping[str, str] | None = None,
    env: MutableMapping[str, str] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    existing = _read_env_file(env_file) if str(env_file or "").strip() else {}
    provided = _normalized_publish_updates(values or {}, env)
    merged = {**existing, **provided}
    mode = str(merged.get("CODEKB_PUBLISH_MODE", "") or "manual").strip()
    missing = _missing_for_mode(mode, merged)
    invalid = _invalid_numeric_targets(mode, merged)
    planned_updates = {key: value for key, value in provided.items() if _present(value)}

    if mode not in PUBLISH_MODES:
        return _report(
            status="blocked_invalid_config",
            ok=False,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates=planned_updates,
            missing=["CODEKB_PUBLISH_MODE"],
            message="Publish mode must be manual, index_page, or template_copy.",
        )
    if invalid:
        return _report(
            status="blocked_invalid_config",
            ok=False,
            applied=False,
            env_file=env_file,
            existing=existing,
            merged=merged,
            planned_updates=planned_updates,
            missing=invalid,
            message="Wiki publish target ids must be positive numeric docids.",
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
            message="Publish target configuration is incomplete; env file was not changed.",
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
            message="Publish configuration is complete; rerun with apply=true to update the env file."
            if planned_updates
            else "Publish configuration is already complete.",
        )
    if not str(env_file or "").strip():
        raise ValueError("env_file is required when applying publish configuration")
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
            message="Publish configuration is already complete; env file was not changed.",
        )
    _write_env_updates(env_file, planned_updates)
    for key, value in planned_updates.items():
        env[key] = value
    return _report(
        status="applied",
        ok=True,
        applied=True,
        env_file=env_file,
        existing=existing,
        merged=merged,
        planned_updates=planned_updates,
        missing=[],
        message="Env file updated; current API process env was updated. Restart workers before executing real publish writes.",
    )


def _normalized_publish_updates(values: Mapping[str, str], env: Mapping[str, str]) -> dict[str, str]:
    aliases = {
        "mode": "CODEKB_PUBLISH_MODE",
        "index_docid": "CODEKB_PUBLISH_INDEX_DOCID",
        "template_docid": "CODEKB_PUBLISH_TEMPLATE_DOCID",
        "target_parentid": "CODEKB_PUBLISH_TARGET_PARENTID",
    }
    updates: dict[str, str] = {}
    for alias, key in aliases.items():
        value = values.get(alias, "") or values.get(key, "")
        normalized = _clean_value(value)
        if normalized:
            updates[key] = normalized
    for key in PUBLISH_CONFIG_KEYS.values():
        value = values.get(key, "")
        normalized = _clean_value(value)
        if normalized:
            updates[key] = normalized
    return updates


def _missing_for_mode(mode: str, merged: Mapping[str, str]) -> list[str]:
    if mode == "index_page" and not _present(merged.get("CODEKB_PUBLISH_INDEX_DOCID", "")):
        return ["CODEKB_PUBLISH_INDEX_DOCID"]
    if mode == "template_copy":
        missing = []
        if not _present(merged.get("CODEKB_PUBLISH_TEMPLATE_DOCID", "")):
            missing.append("CODEKB_PUBLISH_TEMPLATE_DOCID")
        if not _present(merged.get("CODEKB_PUBLISH_TARGET_PARENTID", "")):
            missing.append("CODEKB_PUBLISH_TARGET_PARENTID")
        return missing
    return []


def _invalid_numeric_targets(mode: str, merged: Mapping[str, str]) -> list[str]:
    if mode == "index_page" and _present(merged.get("CODEKB_PUBLISH_INDEX_DOCID", "")):
        if not _positive_int_string(merged.get("CODEKB_PUBLISH_INDEX_DOCID", "")):
            return ["CODEKB_PUBLISH_INDEX_DOCID"]
    if mode == "template_copy":
        invalid = []
        if _present(merged.get("CODEKB_PUBLISH_TEMPLATE_DOCID", "")) and not _positive_int_string(
            merged.get("CODEKB_PUBLISH_TEMPLATE_DOCID", "")
        ):
            invalid.append("CODEKB_PUBLISH_TEMPLATE_DOCID")
        if _present(merged.get("CODEKB_PUBLISH_TARGET_PARENTID", "")) and not _positive_int_string(
            merged.get("CODEKB_PUBLISH_TARGET_PARENTID", "")
        ):
            invalid.append("CODEKB_PUBLISH_TARGET_PARENTID")
        return invalid
    return []


def _positive_int_string(value: object) -> bool:
    try:
        return int(str(value).strip()) > 0
    except (TypeError, ValueError):
        return False


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
    message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "ok": ok,
        "applied": applied,
        "env_file": str(env_file or ""),
        "missing_env": missing,
        "planned_update_keys": sorted(planned_updates),
        "restart_required": False,
        "configured": ok and not missing,
        "keys": {
            key: {
                "configured": _present(merged.get(key, "")),
                "existing": _present(existing.get(key, "")),
                "will_update": key in planned_updates,
                "sensitive": False,
                "sha256_prefix": _hash_prefix(merged.get(key, "")),
            }
            for key in PUBLISH_ENV_KEYS
        },
        "message": message,
    }


def _clean_value(value: object) -> str:
    normalized = str(value or "").strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("environment values must not contain newlines")
    return normalized
