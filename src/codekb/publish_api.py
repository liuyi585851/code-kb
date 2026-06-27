from __future__ import annotations

from pathlib import Path
from typing import Any

from .publish import (
    WikiPublishClient,
    PUBLISH_MODES,
    build_publish_plans,
    process_publish_outbox,
    write_publish_outbox,
)
from .publish_diff import publish_diff
from .source import load_source_bundle


PUBLISH_CONFIG_KEYS = {
    "mode": "CODEKB_PUBLISH_MODE",
    "target_parentid": "CODEKB_PUBLISH_TARGET_PARENTID",
    "template_docid": "CODEKB_PUBLISH_TEMPLATE_DOCID",
    "index_docid": "CODEKB_PUBLISH_INDEX_DOCID",
}


def build_publish_readiness(
    pending_docs_dir: str | Path,
    outbox_path: str | Path,
    report_path: str | Path,
    *,
    ledger_path: str | Path = "",
    env: dict[str, str] | None = None,
    client_configured: bool = False,
) -> dict[str, Any]:
    env = env or {}
    resolved = resolve_publish_options({}, env=env)
    missing = _missing_target_config(resolved)
    invalid = _invalid_numeric_target_config(resolved)
    pending_docs = _pending_docs_summary(pending_docs_dir)
    write_enabled = str(env.get("CODEKB_ENABLE_WIKI_WRITE", "") or "").strip() == "1"
    real_write_ready = bool(write_enabled and client_configured and not missing and not invalid)
    if resolved["mode"] not in PUBLISH_MODES:
        status = "invalid_config"
        missing = ["CODEKB_PUBLISH_MODE"]
    elif invalid:
        status = "invalid_config"
        missing = invalid
    elif missing:
        status = "missing_target_config"
    elif not pending_docs["exists"]:
        status = "pending_docs_missing"
    else:
        status = "ready_for_outbox"
    return {
        "status": status,
        "mode": resolved["mode"],
        "resolved": resolved,
        "missing": missing,
        "pending_docs": pending_docs,
        "outbox_path": str(outbox_path),
        "report_path": str(report_path),
        "ledger_path": str(ledger_path or ""),
        "write_enabled": write_enabled,
        "client_configured": client_configured,
        "real_write_ready": real_write_ready,
        "required_env": _required_env_for_mode(resolved["mode"]),
    }


def plan_publish_outbox(
    pending_docs_dir: str | Path,
    outbox_path: str | Path,
    payload: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    include_diff: bool = False,
    read_client: Any | None = None,
) -> dict[str, Any]:
    resolved = resolve_publish_options(payload, env=env or {})
    limit = _parse_limit(payload.get("limit"), default=50)
    plans = build_publish_plans(
        pending_docs_dir,
        mode=resolved["mode"],
        target_parentid=resolved["target_parentid"],
        template_docid=resolved["template_docid"],
        index_docid=resolved["index_docid"],
        limit=limit,
    )
    written = write_publish_outbox(plans, outbox_path)
    response = {
        "status": "queued" if written else "empty",
        "dry_run": True,
        "mode": resolved["mode"],
        "written": written,
        "outbox_path": str(outbox_path),
        "plans": [plan.to_dict() for plan in plans],
    }
    if include_diff:
        response["diff_preview"] = [
            {
                "publish_id": plan.publish_id,
                "candidate_id": plan.candidate_id,
                "operations": [
                    publish_diff(operation, plan.rendered_body, read_client=read_client)
                    for operation in plan.operations
                ],
            }
            for plan in plans
        ]
    return response


def process_publish_outbox_report(
    outbox_path: str | Path,
    report_path: str | Path,
    payload: dict[str, Any],
    *,
    ledger_path: str | Path | None = None,
    write_enabled: bool = False,
    client: WikiPublishClient | None = None,
) -> dict[str, Any]:
    report = process_publish_outbox(
        outbox_path,
        execute=_parse_bool(payload.get("execute"), default=False),
        write_enabled=write_enabled,
        client=client,
        limit=_parse_limit(payload.get("limit"), default=50),
        report_path=report_path,
        ledger_path=ledger_path,
        confirm_real_publish=_parse_bool(payload.get("confirm_real_publish"), default=False),
    )
    response = report.to_dict()
    response["report_path"] = str(report_path)
    response["ledger_path"] = str(ledger_path or "")
    return response


def resolve_publish_options(payload: dict[str, Any], *, env: dict[str, str]) -> dict[str, str]:
    return {
        "mode": _configured_value(payload, env, "mode", default="manual"),
        "target_parentid": _configured_value(payload, env, "target_parentid"),
        "template_docid": _configured_value(payload, env, "template_docid"),
        "index_docid": _configured_value(payload, env, "index_docid"),
    }


def _configured_value(payload: dict[str, Any], env: dict[str, str], key: str, *, default: str = "") -> str:
    value = payload.get(key)
    if value not in (None, ""):
        return str(value).strip()
    return str(env.get(PUBLISH_CONFIG_KEYS[key], default) or "").strip()


def _missing_target_config(resolved: dict[str, str]) -> list[str]:
    mode = resolved["mode"]
    if mode == "index_page" and not resolved["index_docid"]:
        return ["CODEKB_PUBLISH_INDEX_DOCID"]
    if mode == "template_copy":
        missing = []
        if not resolved["template_docid"]:
            missing.append("CODEKB_PUBLISH_TEMPLATE_DOCID")
        if not resolved["target_parentid"]:
            missing.append("CODEKB_PUBLISH_TARGET_PARENTID")
        return missing
    return []


def _invalid_numeric_target_config(resolved: dict[str, str]) -> list[str]:
    mode = resolved["mode"]
    if mode == "index_page" and resolved["index_docid"] and not _positive_int_string(resolved["index_docid"]):
        return ["CODEKB_PUBLISH_INDEX_DOCID"]
    if mode == "template_copy":
        invalid = []
        if resolved["template_docid"] and not _positive_int_string(resolved["template_docid"]):
            invalid.append("CODEKB_PUBLISH_TEMPLATE_DOCID")
        if resolved["target_parentid"] and not _positive_int_string(resolved["target_parentid"]):
            invalid.append("CODEKB_PUBLISH_TARGET_PARENTID")
        return invalid
    return []


def _positive_int_string(value: str) -> bool:
    try:
        return int(str(value).strip()) > 0
    except (TypeError, ValueError):
        return False


def _required_env_for_mode(mode: str) -> list[str]:
    if mode == "index_page":
        return ["CODEKB_PUBLISH_INDEX_DOCID"]
    if mode == "template_copy":
        return ["CODEKB_PUBLISH_TEMPLATE_DOCID", "CODEKB_PUBLISH_TARGET_PARENTID"]
    return []


def _pending_docs_summary(path: str | Path) -> dict[str, Any]:
    pending_path = Path(path)
    if not pending_path.exists():
        return {"path": str(pending_path), "exists": False, "count": 0, "error": ""}
    try:
        bundle = load_source_bundle(pending_path)
        return {"path": str(pending_path), "exists": True, "count": len(bundle.documents), "error": ""}
    except Exception as exc:
        return {"path": str(pending_path), "exists": True, "count": 0, "error": f"{exc.__class__.__name__}: {exc}"}


def _parse_limit(value: object, *, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 0 or limit > 200:
        raise ValueError("limit must be between 0 and 200")
    return limit


def _parse_bool(value: object, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError("boolean value must be true or false")
