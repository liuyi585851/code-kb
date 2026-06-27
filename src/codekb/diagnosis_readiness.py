from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from .diagnosis_integrations import (
    DEFAULT_API_BASE_URL,
    diagnose_integration_artifacts,
    diagnose_mcp_tool_definitions,
    mr_candidate_card_template,
)
from .diagnosis_webhook import (
    DEFAULT_WEBHOOK_MAPPING_PATH,
    DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    SUPPORTED_WEBHOOK_SOURCE_CHOICES,
    effective_diagnostic_webhook_mapping,
    validate_diagnostic_webhook_sample_suite,
)
from .user_auth import JsonUserTokenStore


def build_p5_readiness_report(
    *,
    fixture_path: str = "data/fixtures/sample_corpus.jsonl",
    aliases_path: str = "data/entity_aliases.yaml",
    registry_path: str = "docs/kb-registry.draft.yaml",
    governance_policy_path: str = "docs/governance-policy.draft.yaml",
    index_db_path: str = "",
    diagnose_webhook_mapping_path: str = DEFAULT_WEBHOOK_MAPPING_PATH,
    diagnose_webhook_samples_path: str = DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    diagnose_webhook_log_path: str = "/data/codekb/logs/diagnose-webhook.jsonl",
    user_token_store_path: str = "/data/codekb/state/user-tokens.json",
    user_confirmation_outbox_path: str = "/data/codekb/outbox/user-confirmation.jsonl",
    user_confirmation_responses_path: str = "/data/codekb/state/user-confirmation-responses.jsonl",
    api_base_url: str = DEFAULT_API_BASE_URL,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    checks = [
        _diagnose_core_check(
            fixture_path=fixture_path,
            aliases_path=aliases_path,
            registry_path=registry_path,
            governance_policy_path=governance_policy_path,
            index_db_path=index_db_path,
        ),
        _webhook_mapping_check(diagnose_webhook_mapping_path),
        _webhook_sample_suite_check(diagnose_webhook_samples_path, diagnose_webhook_mapping_path),
        _integration_artifacts_check(api_base_url),
        _mcp_auth_check(user_token_store_path, api_base_url=api_base_url, env=env),
        _current_user_confirmation_check(
            user_confirmation_outbox_path=user_confirmation_outbox_path,
            user_confirmation_responses_path=user_confirmation_responses_path,
        ),
        _im_oauth_check(env),
        _im_delivery_check(env),
        _webhook_security_check(env),
        _auth_admin_security_check(env),
        _external_sample_source_check(diagnose_webhook_samples_path, diagnose_webhook_mapping_path, env=env),
    ]
    status_counts = Counter(check["status"] for check in checks)
    overall_status = _overall_status(checks)
    return {
        "status": overall_status,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "api_base_url": api_base_url.rstrip("/"),
        "summary": {
            "checks": len(checks),
            "ok": status_counts.get("ok", 0),
            "warn": status_counts.get("warn", 0),
            "deferred": status_counts.get("deferred", 0),
            "blocked": status_counts.get("blocked", 0),
        },
        "paths": {
            "fixture": fixture_path,
            "aliases": aliases_path,
            "registry": registry_path,
            "governance_policy": governance_policy_path,
            "index_db": index_db_path,
            "diagnose_webhook_mapping": diagnose_webhook_mapping_path,
            "diagnose_webhook_samples": diagnose_webhook_samples_path,
            "diagnose_webhook_log": diagnose_webhook_log_path,
            "user_token_store": user_token_store_path,
            "user_confirmation_outbox": user_confirmation_outbox_path,
            "user_confirmation_responses": user_confirmation_responses_path,
        },
        "checks": checks,
        "required_actions": [
            {
                "id": check["id"],
                "status": check["status"],
                "message": check["message"],
                "remediation": check["remediation"],
            }
            for check in checks
            if check["status"] != "ok"
        ],
    }


def _diagnose_core_check(
    *,
    fixture_path: str,
    aliases_path: str,
    registry_path: str,
    governance_policy_path: str,
    index_db_path: str,
) -> dict[str, Any]:
    files = {
        "fixture": _path_state(fixture_path),
        "aliases": _path_state(aliases_path),
        "registry": _path_state(registry_path),
        "governance_policy": _path_state(governance_policy_path),
    }
    missing = [name for name, state in files.items() if not state["exists"]]
    details = {
        "files": files,
        "index_db": _path_state(index_db_path) if index_db_path else {"path": "", "exists": False, "configured": False},
    }
    if missing:
        return _check(
            "diagnose_core_files",
            "blocked",
            "Required diagnosis files are missing: " + ", ".join(missing),
            "Fix CODEKB_FIXTURES/ALIASES/REGISTRY/GOVERNANCE_POLICY or deploy the missing files.",
            details,
        )
    return _check(
        "diagnose_core_files",
        "ok",
        "Diagnosis fixture, aliases, registry and governance policy are available.",
        "",
        details,
    )


def _webhook_mapping_check(mapping_path: str) -> dict[str, Any]:
    try:
        sources = {
            source: effective_diagnostic_webhook_mapping(source, mapping_path).to_dict()
            for source in SUPPORTED_WEBHOOK_SOURCE_CHOICES
        }
    except Exception as exc:
        return _check(
            "webhook_mapping",
            "blocked",
            f"Webhook mapping cannot be loaded: {exc}",
            "Fix the YAML configured by CODEKB_DIAGNOSE_WEBHOOK_MAPPING.",
            {"path": mapping_path},
        )
    exists = Path(mapping_path).exists() if mapping_path else False
    status = "ok" if exists else "warn"
    message = "Webhook mapping is configured and effective paths can be resolved."
    remediation = ""
    if not exists:
        message = "Webhook mapping file is missing; builtin paths are being used."
        remediation = "Provide a checked-in mapping YAML or set CODEKB_DIAGNOSE_WEBHOOK_MAPPING."
    return _check(
        "webhook_mapping",
        status,
        message,
        remediation,
        {
            "path": mapping_path,
            "exists": exists,
            "sources": sorted(sources),
        },
    )


def _webhook_sample_suite_check(samples_path: str, mapping_path: str) -> dict[str, Any]:
    try:
        summary = validate_diagnostic_webhook_sample_suite(samples_path, mapping_path=mapping_path)
    except Exception as exc:
        return _check(
            "webhook_sample_suite",
            "blocked",
            f"Webhook sample suite cannot run: {exc}",
            "Fix CODEKB_DIAGNOSE_WEBHOOK_SAMPLES or the active webhook mapping.",
            {"path": samples_path, "mapping_path": mapping_path},
        )
    details = {
        "path": summary.get("path", samples_path),
        "mapping_path": mapping_path,
        "status": summary.get("status", ""),
        "total": summary.get("total", 0),
        "passed": summary.get("passed", 0),
        "failed": summary.get("failed", 0),
    }
    if summary.get("status") != "passed":
        return _check(
            "webhook_sample_suite",
            "blocked",
            "Webhook sample suite has failing samples.",
            "Run diagnose-webhook-sample-suite and fix failed sample mappings or redaction rules.",
            details,
        )
    return _check(
        "webhook_sample_suite",
        "ok",
        "Webhook sample suite passes.",
        "",
        details,
    )


def _integration_artifacts_check(api_base_url: str) -> dict[str, Any]:
    tools = diagnose_mcp_tool_definitions(api_base_url=api_base_url)
    card = mr_candidate_card_template(api_base_url=api_base_url)
    artifacts = diagnose_integration_artifacts(api_base_url=api_base_url)
    auth_required = all("auth_token" in tool.get("inputSchema", {}).get("required", []) for tool in tools)
    has_confirmation_tool = any(tool.get("name") == "codekb_request_user_confirmation" for tool in tools)
    has_sample_action = any(action.get("id") == "sample_suite" for action in card.get("actions", []))
    has_current_user_auth = "current_user_auth.md" in set(artifacts.get("files") or [])
    has_external_handoff = "external_handoff.md" in set(artifacts.get("files") or [])
    artifact_payload = artifacts.get("artifacts") or {}
    artifact_text = "\n".join(str(value) for value in artifact_payload.values())
    artifact_text_lower = artifact_text.lower()
    has_current_user_token_guidance = (
        "current user's `auth_token`" in artifact_text
        or "current user bound to `auth_token`" in artifact_text
        or "当前用户 `auth_token`" in artifact_text
    )
    has_current_user_confirmation_target = (
        "routed only to the current user bound to `auth_token`" in artifact_text
        or "Confirmation target is always the current authenticated user bound to `auth_token`" in artifact_text
        or "确认目标始终是 `auth_token` 绑定的当前用户" in artifact_text
    )
    has_no_interface_lookup_guidance = (
        "do not infer an interface person" in artifact_text_lower
        or "do not route p5 confirmations by owner/interface-person lookup" in artifact_text_lower
        or "interface-person fields are not used for p5 routing" in artifact_text_lower
        or "不通过复杂接口人识别" in artifact_text
    )
    has_webhook_audit_exclusion = "Webhook audit events intentionally exclude `auth_token`" in artifact_text
    has_interaction_complete_confirmation = (
        "interaction complete" in artifact_text_lower or "interaction_complete" in artifact_text
    )
    has_problem_solved_confirmation = "problem solved" in artifact_text_lower or "problem_solved" in artifact_text
    has_webhook_confirmation_policy = (
        "POST /diagnose/webhook/{source}" in artifact_text
        and "X-CodeKB-Token" in artifact_text
        and "confirmation_policy=needs_review|always" in artifact_text
    )
    has_mcp_setup_url = "/auth/im/mcp/setup" in artifact_text
    has_token_binding_fallback_url = "/auth/im/token-bindings/page" in artifact_text
    has_external_state_final_gate = (
        "GET /diagnose/external-state" in artifact_text and "diagnose-p5-external-state" in artifact_text
    )
    has_final_verify_gate = "diagnose-p5-final-verify" in artifact_text and "--output /data/codekb/logs/p5-final-verify-report.json" in artifact_text
    ready = (
        len(tools) == 4
        and len(card.get("actions", [])) >= 4
        and len(artifacts.get("files") or []) >= 6
        and auth_required
        and has_confirmation_tool
        and has_sample_action
        and has_current_user_auth
        and has_external_handoff
        and has_webhook_confirmation_policy
        and has_current_user_token_guidance
        and has_current_user_confirmation_target
        and has_no_interface_lookup_guidance
        and has_webhook_audit_exclusion
        and has_interaction_complete_confirmation
        and has_problem_solved_confirmation
        and has_mcp_setup_url
        and has_token_binding_fallback_url
        and has_external_state_final_gate
        and has_final_verify_gate
    )
    details = {
        "api_base_url": api_base_url.rstrip("/"),
        "mcp_tools": len(tools),
        "mr_card_actions": len(card.get("actions", [])),
        "artifact_files": len(artifacts.get("files") or []),
        "auth_token_required": auth_required,
        "has_confirmation_tool": has_confirmation_tool,
        "has_sample_suite_action": has_sample_action,
        "has_current_user_auth_guide": has_current_user_auth,
        "has_external_handoff_checklist": has_external_handoff,
        "has_webhook_confirmation_policy_guide": has_webhook_confirmation_policy,
        "has_current_user_token_guidance": has_current_user_token_guidance,
        "has_current_user_confirmation_target": has_current_user_confirmation_target,
        "has_no_interface_lookup_guidance": has_no_interface_lookup_guidance,
        "has_webhook_audit_exclusion": has_webhook_audit_exclusion,
        "has_interaction_complete_confirmation": has_interaction_complete_confirmation,
        "has_problem_solved_confirmation": has_problem_solved_confirmation,
        "has_mcp_setup_url": has_mcp_setup_url,
        "has_token_binding_fallback_url": has_token_binding_fallback_url,
        "has_external_state_final_gate": has_external_state_final_gate,
        "has_final_verify_gate": has_final_verify_gate,
    }
    if not ready:
        return _check(
            "integration_artifacts",
            "blocked",
            "P5 integration artifacts are incomplete.",
            "Regenerate diagnose integration artifacts and ensure MCP tools require auth_token.",
            details,
        )
    return _check("integration_artifacts", "ok", "P5 integration artifacts are internally consistent.", "", details)


def _mcp_auth_check(user_token_store_path: str, *, api_base_url: str, env: Mapping[str, str]) -> dict[str, Any]:
    tools = diagnose_mcp_tool_definitions(api_base_url=api_base_url)
    auth_required = all("auth_token" in tool.get("inputSchema", {}).get("required", []) for tool in tools)
    mcp_token_configured = _env_present(env, "CODEKB_MCP_TOKEN")
    details: dict[str, Any] = {
        "token_store_path": user_token_store_path,
        "token_store_exists": Path(user_token_store_path).exists() if user_token_store_path else False,
        "static_mcp_token_configured": mcp_token_configured,
        "auth_token_required": auth_required,
    }
    if not auth_required:
        return _check(
            "mcp_auth",
            "blocked",
            "At least one MCP tool does not require auth_token.",
            "Fix diagnose_mcp_tool_definitions so every tool requires current-user auth_token.",
            details,
        )
    if not user_token_store_path:
        return _check(
            "mcp_auth",
            "blocked",
            "Current-user MCP token store is not configured.",
            "Set CODEKB_USER_TOKEN_STORE and start diagnose-mcp-server with --token-store; shared MCP tokens are local smoke-only.",
            details,
        )
    if user_token_store_path:
        try:
            summary = JsonUserTokenStore(user_token_store_path).summary()
        except Exception as exc:
            details["error"] = str(exc)
            return _check(
                "mcp_auth",
                "blocked",
                f"User token store cannot be read: {exc}",
                "Fix CODEKB_USER_TOKEN_STORE or repair the JSON token store.",
                details,
            )
        details.update(
            {
                "total_token_bindings": summary["total"],
                "active_token_bindings": summary["active"],
                "revoked_token_bindings": summary["revoked"],
                "expired_token_bindings": summary["expired"],
            }
        )
        if summary["active"] == 0:
            return _check(
                "mcp_auth",
                "warn",
                "MCP token store is configured but has no active current-user tokens.",
                "Have the user complete IM OAuth or web token binding before MCP use.",
                details,
            )
    if mcp_token_configured:
        return _check(
            "mcp_auth",
            "warn",
            "Shared MCP token is configured but production MCP uses current-user token store only.",
            "Unset CODEKB_MCP_TOKEN for production MCP processes; when --token-store is configured, bound current-user tokens are authoritative.",
            details,
        )
    return _check("mcp_auth", "ok", "MCP requires auth_token and has an auth backend.", "", details)


def _current_user_confirmation_check(
    *,
    user_confirmation_outbox_path: str,
    user_confirmation_responses_path: str,
) -> dict[str, Any]:
    details = {
        "outbox_path": user_confirmation_outbox_path,
        "outbox_exists": Path(user_confirmation_outbox_path).exists() if user_confirmation_outbox_path else False,
        "responses_path": user_confirmation_responses_path,
        "responses_exists": Path(user_confirmation_responses_path).exists() if user_confirmation_responses_path else False,
    }
    if not user_confirmation_outbox_path or not user_confirmation_responses_path:
        return _check(
            "current_user_confirmation",
            "blocked",
            "Current-user confirmation outbox or responses path is not configured.",
            "Set CODEKB_USER_CONFIRMATION_OUTBOX and CODEKB_USER_CONFIRMATION_RESPONSES.",
            details,
        )
    return _check(
        "current_user_confirmation",
        "ok",
        "Current-user confirmation paths are configured.",
        "",
        details,
    )


def _im_oauth_check(env: Mapping[str, str]) -> dict[str, Any]:
    self_binding_configured = _env_present(env, "CODEKB_USER_BINDING_CODE")
    required = (
        "CODEKB_IM_CORP_ID",
        "CODEKB_IM_AGENT_ID",
        "CODEKB_IM_APP_SECRET",
        "CODEKB_IM_OAUTH_STATE_SECRET",
    )
    missing = [name for name in required if not _env_present(env, name)]
    details = {
        "configured": not missing,
        "self_binding_configured": self_binding_configured,
        "missing_env": missing,
        "redirect_uri_configured": _env_present(env, "CODEKB_IM_OAUTH_REDIRECT_URI"),
    }
    if missing and self_binding_configured:
        return _check(
            "im_oauth",
            "ok",
            "Self-service current-user binding is configured; IM OAuth is optional for this stage.",
            "",
            details,
        )
    if missing:
        generated = [name for name in missing if name == "CODEKB_IM_OAUTH_STATE_SECRET"]
        external = [name for name in missing if name not in generated]
        remediation_parts = []
        if generated:
            remediation_parts.append("run diagnose-security-bootstrap to generate CODEKB_IM_OAUTH_STATE_SECRET")
        if external:
            remediation_parts.append("configure " + ", ".join(external) + " from the IM app console")
        return _check(
            "im_oauth",
            "deferred",
            "IM OAuth is not fully configured.",
            "; ".join(remediation_parts) + " before production OAuth login.",
            details,
        )
    return _check("im_oauth", "ok", "IM OAuth required environment variables are configured.", "", details)


def _im_delivery_check(env: Mapping[str, str]) -> dict[str, Any]:
    send_enabled = str(env.get("CODEKB_ENABLE_IM_SEND", "") or "").strip() == "1"
    required = ("CODEKB_IM_CORP_ID", "CODEKB_IM_AGENT_ID", "CODEKB_IM_APP_SECRET")
    missing = [name for name in required if not _env_present(env, name)]
    details = {
        "send_enabled": send_enabled,
        "missing_env": missing,
        "confirm_url_configured": _env_present(env, "CODEKB_IM_CONFIRM_URL_BASE"),
        "web_push_inbox_url": "/auth/im/confirmations/page",
        "web_push_inbox_available": True,
    }
    if not send_enabled:
        return _check(
            "im_delivery",
            "ok",
            "Web push inbox is the active confirmation delivery surface while IM/TOF send is pending.",
            "",
            details,
        )
    if missing:
        return _check(
            "im_delivery",
            "blocked",
            "IM send is enabled but app credentials are incomplete.",
            "Configure " + ", ".join(missing) + " or disable CODEKB_ENABLE_IM_SEND.",
            details,
        )
    return _check("im_delivery", "ok", "IM confirmation delivery is enabled and configured.", "", details)


def _webhook_security_check(env: Mapping[str, str]) -> dict[str, Any]:
    configured = _env_present(env, "CODEKB_DIAGNOSE_WEBHOOK_TOKEN")
    if not configured:
        return _check(
            "webhook_security",
            "warn",
            "Diagnose webhook shared token is not configured.",
            "Run diagnose-security-bootstrap and set CODEKB_DIAGNOSE_WEBHOOK_TOKEN before exposing webhook endpoints beyond trusted internal smoke tests.",
            {"shared_token_configured": False},
        )
    return _check("webhook_security", "ok", "Diagnose webhook shared token is configured.", "", {"shared_token_configured": True})


def _auth_admin_security_check(env: Mapping[str, str]) -> dict[str, Any]:
    configured = _env_present(env, "CODEKB_AUTH_ADMIN_TOKEN")
    details = {"admin_token_configured": configured}
    if not configured:
        return _check(
            "auth_admin_security",
            "warn",
            "Auth admin token is not configured.",
            "Set CODEKB_AUTH_ADMIN_TOKEN before using HTTP token management, confirmation response summaries, or real webhook sample import.",
            details,
        )
    return _check(
        "auth_admin_security",
        "ok",
        "Auth admin token is configured for controlled management endpoints.",
        "",
        details,
    )


def _external_sample_source_check(samples_path: str, mapping_path: str, *, env: Mapping[str, str]) -> dict[str, Any]:
    normalized = str(samples_path or "").replace("\\", "/")
    uses_default_draft = normalized == DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH or normalized.endswith(
        "/diagnose-webhook-samples.draft.yaml"
    )
    real_samples_path = _real_samples_path(samples_path, env)
    real_probe = _sample_suite_probe(real_samples_path, mapping_path)
    details = {
        "path": samples_path,
        "uses_default_draft_samples": uses_default_draft,
        "real_samples_path": real_samples_path,
        "real_samples_exists": real_probe["exists"],
        "real_samples_status": real_probe["status"],
        "real_samples_total": real_probe["total"],
        "real_samples_passed": real_probe["passed"],
        "real_samples_failed": real_probe["failed"],
        "real_samples_sources": real_probe["sources"],
        "real_samples_generated_by_import": real_probe["generated_by_import"],
        "real_samples_error": real_probe["error"],
    }
    if uses_default_draft:
        if real_probe["exists"] and real_probe["status"] == "passed":
            return _check(
                "external_platform_samples",
                "warn",
                "Webhook sample suite is still using synthetic draft samples; a sanitized real sample suite exists but is not active.",
                f"After confirming the samples are real platform data, set CODEKB_DIAGNOSE_WEBHOOK_SAMPLES={real_samples_path} and restart the API.",
                details,
            )
        if real_probe["exists"] and real_probe["status"] == "failed":
            return _check(
                "external_platform_samples",
                "warn",
                "Webhook sample suite is still using synthetic draft samples; the real sample suite exists but does not pass validation.",
                f"Run diagnose-webhook-sample-suite --samples {real_samples_path}, fix failed samples, then set CODEKB_DIAGNOSE_WEBHOOK_SAMPLES to that file.",
                details,
            )
        return _check(
            "external_platform_samples",
            "warn",
            "Webhook sample suite is still using synthetic draft samples.",
            "Use diagnose-webhook-sample-import to sanitize real platform payloads, set CODEKB_DIAGNOSE_WEBHOOK_SAMPLES to the generated suite, then rerun readiness.",
            details,
        )
    return _check("external_platform_samples", "ok", "Webhook sample suite is using a non-default sample source.", "", details)


def _real_samples_path(active_samples_path: str, env: Mapping[str, str]) -> str:
    configured = str(env.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", "") or "").strip()
    if configured:
        return configured
    normalized = str(active_samples_path or "").replace("\\", "/")
    if normalized and normalized != DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH and not normalized.endswith(
        "/diagnose-webhook-samples.draft.yaml"
    ):
        return active_samples_path
    return "/data/codekb/state/diagnose-webhook-samples.real.yaml"


def _sample_suite_probe(samples_path: str, mapping_path: str) -> dict[str, Any]:
    empty = {
        "exists": False,
        "status": "",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "sources": [],
        "generated_by_import": 0,
        "error": "",
    }
    if not samples_path:
        return empty
    path = Path(samples_path)
    if not path.exists():
        return empty
    try:
        summary = validate_diagnostic_webhook_sample_suite(path, mapping_path=mapping_path)
        generated_by_import = _generated_by_import_count(path)
    except Exception as exc:
        return {
            **empty,
            "exists": True,
            "status": "failed",
            "error": str(exc),
        }
    return {
        "exists": True,
        "status": str(summary.get("status", "")),
        "total": int(summary.get("total", 0) or 0),
        "passed": int(summary.get("passed", 0) or 0),
        "failed": int(summary.get("failed", 0) or 0),
        "sources": sorted({str(sample.get("source", "")) for sample in summary.get("samples", []) if sample.get("source")}),
        "generated_by_import": generated_by_import,
        "error": "",
    }


def _generated_by_import_count(samples_path: Path) -> int:
    data = yaml.safe_load(samples_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return 0
    samples = data.get("samples", [])
    if not isinstance(samples, list):
        return 0
    total = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        metadata = sample.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("generated_by") == "diagnose-webhook-sample-import":
            total += 1
    return total


def _path_state(path: str) -> dict[str, Any]:
    normalized = str(path or "")
    return {"path": normalized, "exists": bool(normalized and Path(normalized).exists())}


def _env_present(env: Mapping[str, str], name: str) -> bool:
    return bool(str(env.get(name, "") or "").strip())


def _check(
    check_id: str,
    status: str,
    message: str,
    remediation: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "remediation": remediation,
        "details": details,
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {check["status"] for check in checks}
    if "blocked" in statuses:
        return "blocked"
    if statuses.intersection({"warn", "deferred"}):
        return "ready_with_warnings"
    return "ready"
