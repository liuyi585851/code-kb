from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

from .redaction import redact_sensitive_text, redact_sensitive_url
from .user_auth import (
    DEFAULT_IM_OAUTH_SCOPE,
    JsonUserTokenStore,
    IMOAuthClient,
    make_im_oauth_state,
    verify_im_oauth_state,
)


DEFAULT_IM_OAUTH_NEXT_URL = "/auth/im/mcp/setup"


def run_im_oauth_smoke(
    *,
    env: Mapping[str, str],
    token_store_path: str = "/data/codekb/state/user-tokens.json",
    api_base_url: str = "http://127.0.0.1:8080",
    redirect_uri: str = "",
    next_url: str = DEFAULT_IM_OAUTH_NEXT_URL,
    check_credentials: bool = False,
    client: IMOAuthClient | None = None,
) -> dict[str, Any]:
    required = (
        "CODEKB_IM_CORP_ID",
        "CODEKB_IM_AGENT_ID",
        "CODEKB_IM_APP_SECRET",
        "CODEKB_IM_OAUTH_STATE_SECRET",
    )
    missing = [name for name in required if not str(env.get(name, "") or "").strip()]
    client = client or _client_from_env(env)
    resolved_redirect = str(redirect_uri or env.get("CODEKB_IM_OAUTH_REDIRECT_URI", "") or "").strip()
    if not resolved_redirect:
        resolved_redirect = f"{api_base_url.rstrip('/')}/auth/im/oauth/callback"

    state_report = _state_report(env.get("CODEKB_IM_OAUTH_STATE_SECRET", ""), next_url=next_url)
    authorize_report = _authorize_report(
        client,
        redirect_uri=resolved_redirect,
        state=state_report.get("state", ""),
        scope=str(env.get("CODEKB_IM_OAUTH_SCOPE", "") or DEFAULT_IM_OAUTH_SCOPE),
    )
    credential_report = _credential_report(client, check_credentials=check_credentials)
    token_store_report = _token_store_report(token_store_path)
    ok = (
        not missing
        and state_report["status"] == "verified"
        and authorize_report["status"] == "generated"
        and credential_report["status"] in {"skipped", "verified"}
    )
    status = "verified" if ok else _failure_status(missing, state_report, authorize_report, credential_report)
    return {
        "status": status,
        "ok": ok,
        "configured": not missing,
        "missing_env": missing,
        "api_base_url": api_base_url.rstrip("/"),
        "setup_url": f"{api_base_url.rstrip('/')}/auth/im/mcp/setup",
        "redirect_uri_hash": _hash(resolved_redirect),
        "redirect_uri_host": urllib.parse.urlparse(resolved_redirect).netloc,
        "state": {key: value for key, value in state_report.items() if key != "state"},
        "authorize_url": authorize_report,
        "credentials": credential_report,
        "token_store": token_store_report,
    }


def _client_from_env(env: Mapping[str, str]) -> IMOAuthClient:
    return IMOAuthClient(
        corp_id=env.get("CODEKB_IM_CORP_ID", ""),
        app_secret=env.get("CODEKB_IM_APP_SECRET", ""),
        agent_id=env.get("CODEKB_IM_AGENT_ID", ""),
        api_base=env.get("CODEKB_IM_API_BASE", "https://im-api.example.com/cgi-bin"),
        authorize_base=env.get("CODEKB_IM_OAUTH_AUTHORIZE_BASE", "https://im-oauth.example.com/connect/oauth2/authorize"),
    )


def _state_report(secret: str, *, next_url: str) -> dict[str, Any]:
    if not str(secret or "").strip():
        return {"status": "missing_secret", "signed": False, "verified": False, "next": ""}
    try:
        state = make_im_oauth_state(secret, next_url=next_url, now=1000)
        payload = verify_im_oauth_state(state, secret, now=1001)
    except Exception as exc:
        return {"status": "failed", "signed": False, "verified": False, "next": "", "error": _safe_error(exc)}
    return {
        "status": "verified",
        "signed": True,
        "verified": True,
        "next": payload.get("next", ""),
        "state": state,
    }


def _authorize_report(
    client: IMOAuthClient,
    *,
    redirect_uri: str,
    state: str,
    scope: str,
) -> dict[str, Any]:
    try:
        authorize_url = client.authorize_url(redirect_uri=redirect_uri, state=state, scope=scope)
    except Exception as exc:
        return {
            "status": "failed",
            "host": "",
            "has_state": False,
            "corp_id_hash": _hash(client.corp_id),
            "agent_id": client.agent_id,
            "scope": scope,
            "error": _safe_error(exc),
        }
    parsed = urllib.parse.urlparse(authorize_url)
    query = urllib.parse.parse_qs(parsed.query)
    return {
        "status": "generated",
        "host": parsed.netloc,
        "fragment": parsed.fragment,
        "has_state": bool(query.get("state", [""])[0]),
        "corp_id_hash": _hash(query.get("appid", [""])[0]),
        "agent_id": query.get("agentid", [""])[0],
        "scope": query.get("scope", [""])[0],
        "redirect_uri_hash": _hash(query.get("redirect_uri", [""])[0]),
    }


def _credential_report(client: IMOAuthClient, *, check_credentials: bool) -> dict[str, Any]:
    configured = client.configured()
    report = {
        "status": "skipped",
        "check_requested": check_credentials,
        "configured": configured,
        "access_token_acquired": False,
        "api_base": client.api_base,
        "corp_id_hash": _hash(client.corp_id),
        "agent_id": client.agent_id,
    }
    if not check_credentials:
        return report
    if not configured:
        return {**report, "status": "blocked_missing_credentials"}
    try:
        token = client._get_access_token()
    except Exception as exc:
        return {**report, "status": "failed", "error": _safe_error(exc)}
    return {**report, "status": "verified", "access_token_acquired": bool(token)}


def _token_store_report(path: str) -> dict[str, Any]:
    if not str(path or "").strip():
        return {"path": "", "exists": False, "total": 0, "active": 0, "revoked": 0, "expired": 0, "status": "missing"}
    try:
        summary = JsonUserTokenStore(path).summary()
    except Exception as exc:
        return {
            "path": str(path),
            "exists": False,
            "total": 0,
            "active": 0,
            "revoked": 0,
            "expired": 0,
            "status": "failed",
            "error": _safe_error(exc),
        }
    return {
        "path": summary["path"],
        "exists": Path(path).exists(),
        "total": summary["total"],
        "active": summary["active"],
        "revoked": summary["revoked"],
        "expired": summary["expired"],
        "status": "ok",
    }


def _failure_status(
    missing: list[str],
    state_report: dict[str, Any],
    authorize_report: dict[str, Any],
    credential_report: dict[str, Any],
) -> str:
    if missing:
        return "blocked_missing_env"
    for report in (state_report, authorize_report, credential_report):
        if report["status"] not in {"verified", "generated", "skipped"}:
            return str(report["status"])
    return "failed"


def _hash(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_error(exc: Exception) -> str:
    return redact_sensitive_text(redact_sensitive_url(str(exc)))
