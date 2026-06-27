from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .user_auth import JsonUserTokenStore, public_token_metadata
from .user_confirmation import JsonlUserConfirmationOutbox
from .user_confirmation_delivery import (
    IM_ROUTE_KEYS,
    UserConfirmationClient,
    IMAppMessageClient,
    process_user_confirmation_outbox,
    validate_im_delivery_configuration,
)


DEFAULT_IM_SMOKE_MESSAGE = "P5 IM delivery smoke confirmation"
DEFAULT_IM_SMOKE_REASON = "human_review_required"


def run_im_delivery_smoke(
    *,
    env: Mapping[str, str],
    auth_token: str = "",
    token_store_path: str = "/data/codekb/state/user-tokens.json",
    confirmation_outbox_path: str = "/data/codekb/outbox/user-confirmation.jsonl",
    delivery_report_path: str = "/data/codekb/logs/user-confirmation-report.json",
    delivery_log_path: str = "/data/codekb/state/user-confirmation-delivery.jsonl",
    reason: str = DEFAULT_IM_SMOKE_REASON,
    message: str = DEFAULT_IM_SMOKE_MESSAGE,
    check_credentials: bool = True,
    execute: bool = False,
    write_enabled: bool = False,
    client: IMAppMessageClient | UserConfirmationClient | None = None,
) -> dict[str, Any]:
    client = client or _client_from_env(env)
    delivery_config = _delivery_config(client, env=env, require_confirmation_url=execute and write_enabled)
    credentials = _check_credentials(client, env=env, check_credentials=check_credentials, delivery_config=delivery_config)
    credential_ok = credentials["status"] in {"skipped", "verified"}
    if execute and write_enabled and not delivery_config["ok"]:
        credential_ok = False
    token = str(auth_token or "").strip()
    if execute and not token:
        raise ValueError("auth_token is required when executing a IM smoke send")
    if not token:
        return {
            "status": "credentials_verified" if credential_ok else credentials["status"],
            "ok": credential_ok,
            "credentials": credentials,
            "delivery_config": delivery_config,
            "current_user": None,
            "confirmation": None,
            "delivery": None,
        }

    store = JsonUserTokenStore(token_store_path)
    binding = store.validate(token)
    if binding is None:
        raise PermissionError("invalid auth token")
    current_user = {
        "token_valid": True,
        "token_id": binding.token_id,
        "user_id_hash": binding.user_id_hash,
        "token_hash_prefix": binding.token_hash[:12],
        "display_name": binding.display_name,
        "scopes": list(binding.scopes),
        "metadata": public_token_metadata(binding.metadata),
        "route_user_hash": _hash(_route_user(binding.metadata)),
        "route_configured": bool(_route_user(binding.metadata)),
    }
    if execute and write_enabled and not delivery_config["ok"]:
        return {
            "status": "blocked_invalid_delivery_config",
            "ok": False,
            "credentials": credentials,
            "delivery_config": delivery_config,
            "current_user": current_user,
            "confirmation": None,
            "delivery": None,
        }
    request = JsonlUserConfirmationOutbox(confirmation_outbox_path).append(
        user_token=token,
        reason=reason,
        message=message,
        payload={"smoke": "im_delivery"},
    )
    delivery_report = process_user_confirmation_outbox(
        confirmation_outbox_path,
        token_store_path=token_store_path,
        execute=execute,
        write_enabled=write_enabled,
        client=client,
        limit=1,
        confirmation_id=request.confirmation_id,
        report_path=delivery_report_path,
        delivery_log_path=delivery_log_path,
    )
    result = delivery_report.results[0] if delivery_report.results else None
    delivery_ok = result is not None and result.status == ("executed" if execute else "validated")
    ok = credential_ok and delivery_ok
    return {
        "status": _status(credentials=credentials, delivery_result=result),
        "ok": ok,
        "credentials": credentials,
        "delivery_config": delivery_config,
        "current_user": current_user,
        "confirmation": {
            "confirmation_id": request.confirmation_id,
            "created_at": request.created_at,
            "channel": request.channel,
            "target_user_token_hash_prefix": request.target_user_token_hash[:12],
            "reason": request.reason,
            "message": request.message,
            "payload": dict(request.payload),
            "status": request.status,
        },
        "delivery": {
            "report_status": delivery_report.status,
            "processed": delivery_report.processed,
            "blocked_operations": delivery_report.blocked_operations,
            "executed_operations": delivery_report.executed_operations,
            "result": result.to_dict() if result else None,
        },
    }


def _client_from_env(env: Mapping[str, str]) -> IMAppMessageClient:
    return IMAppMessageClient(
        corp_id=env.get("CODEKB_IM_CORP_ID", ""),
        agent_id=env.get("CODEKB_IM_AGENT_ID", ""),
        app_secret=env.get("CODEKB_IM_APP_SECRET", ""),
        api_base=env.get("CODEKB_IM_API_BASE", "https://im-api.example.com/cgi-bin"),
        confirmation_url_base=env.get("CODEKB_IM_CONFIRM_URL_BASE", ""),
    )


def _check_credentials(
    client: Any,
    *,
    env: Mapping[str, str],
    check_credentials: bool,
    delivery_config: dict[str, Any],
) -> dict[str, Any]:
    missing = [
        name
        for name in ("CODEKB_IM_CORP_ID", "CODEKB_IM_AGENT_ID", "CODEKB_IM_APP_SECRET")
        if not str(env.get(name, "") or "").strip()
    ]
    summary = {
        "status": "skipped",
        "check_requested": check_credentials,
        "configured": not missing and bool(getattr(client, "configured", lambda: False)()),
        "missing_env": missing,
        "access_token_acquired": False,
        "api_base": str(getattr(client, "api_base", "") or env.get("CODEKB_IM_API_BASE", "")),
        "corp_id_hash": _hash(str(getattr(client, "corp_id", "") or env.get("CODEKB_IM_CORP_ID", ""))),
        "agent_id": str(getattr(client, "agent_id", "") or env.get("CODEKB_IM_AGENT_ID", "")),
        "confirm_url_configured": bool(
            str(getattr(client, "confirmation_url_base", "") or env.get("CODEKB_IM_CONFIRM_URL_BASE", "")).strip()
        ),
        "delivery_config_ok": bool(delivery_config.get("ok")),
        "delivery_config_errors": list(delivery_config.get("errors") or []),
    }
    if not check_credentials:
        return summary
    if missing or not summary["configured"]:
        return {**summary, "status": "blocked_missing_credentials"}
    try:
        token = str(client._get_access_token() or "")
    except Exception as exc:
        return {**summary, "status": "failed", "error": str(exc)}
    return {**summary, "status": "verified", "access_token_acquired": bool(token)}


def _delivery_config(
    client: Any,
    *,
    env: Mapping[str, str],
    require_confirmation_url: bool,
) -> dict[str, Any]:
    return validate_im_delivery_configuration(
        agent_id=str(getattr(client, "agent_id", "") or env.get("CODEKB_IM_AGENT_ID", "")),
        confirmation_url_base=str(
            getattr(client, "confirmation_url_base", "") or env.get("CODEKB_IM_CONFIRM_URL_BASE", "")
        ),
        require_confirmation_url=require_confirmation_url,
    )


def _status(*, credentials: dict[str, Any], delivery_result: Any | None) -> str:
    if delivery_result is None:
        return credentials["status"]
    if credentials["status"] not in {"skipped", "verified"}:
        return credentials["status"]
    return str(delivery_result.status)


def _route_user(metadata: dict[str, Any]) -> str:
    for key in IM_ROUTE_KEYS:
        value = str(metadata.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _hash(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
