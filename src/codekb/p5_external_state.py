from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .user_auth import JsonUserTokenStore
from .user_confirmation_delivery import validate_im_delivery_configuration
from .im_config import REQUIRED_IM_OAUTH_KEYS


DEFAULT_P5_ENV_FILE = "/data/codekb/state/p5-secrets.env"
DEFAULT_IM_TEMPLATE = "/data/codekb/state/p5-handoff/im-config.todo.env"
DEFAULT_USER_TOKEN_STORE = "/data/codekb/state/user-tokens.json"
DEFAULT_REAL_SAMPLES = "/data/codekb/state/diagnose-webhook-samples.real.yaml"


def build_p5_external_state(
    *,
    env_file: str = DEFAULT_P5_ENV_FILE,
    im_template: str = DEFAULT_IM_TEMPLATE,
    token_store: str = DEFAULT_USER_TOKEN_STORE,
    real_samples: str = DEFAULT_REAL_SAMPLES,
) -> dict[str, Any]:
    env_path = Path(env_file)
    template_path = _template_path(im_template)
    token_store_path = Path(token_store)
    real_samples_path = Path(real_samples)
    env = _read_env_file(env_path)
    template = _read_env_file(template_path)
    env_missing = [key for key in REQUIRED_IM_OAUTH_KEYS if not _present(env.get(key))]
    combined_missing = [key for key in REQUIRED_IM_OAUTH_KEYS if not _present(env.get(key)) and not _present(template.get(key))]
    self_binding_configured = _present(env.get("CODEKB_USER_BINDING_CODE"))
    token_summary = _token_store_summary(token_store_path)
    samples_active = _clean(env.get("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", "")) == str(real_samples_path)
    send_enabled = _clean(env.get("CODEKB_ENABLE_IM_SEND", "")) == "1"
    delivery_config = validate_im_delivery_configuration(
        agent_id=_clean(env.get("CODEKB_IM_AGENT_ID", "")),
        confirmation_url_base=_clean(env.get("CODEKB_IM_CONFIRM_URL_BASE", "")),
        require_confirmation_url=True,
    )
    checks = [
        _check(
            "im_template",
            self_binding_configured or not combined_missing,
            "Self-service binding is configured or IM template plus server env includes all required OAuth keys.",
            missing_keys=combined_missing,
            self_binding_configured=self_binding_configured,
        ),
        _check(
            "im_env",
            self_binding_configured or (env_path.exists() and not env_missing),
            "Self-service binding is configured or server env includes all required IM OAuth keys.",
            missing_keys=env_missing,
            self_binding_configured=self_binding_configured,
        ),
        _check(
            "mcp_auth",
            int(token_summary["active_tokens"]) > 0,
            "Token store has at least one active current-user token binding.",
            **token_summary,
        ),
        _check(
            "im_delivery",
            (not send_enabled) or bool(delivery_config["ok"]),
            "Web push inbox is available; real IM/TOF send is validated only when explicitly enabled.",
            delivery_config=delivery_config,
            send_enabled=send_enabled,
            web_push_inbox_url="/auth/im/confirmations/page",
        ),
        _check(
            "external_platform_samples",
            real_samples_path.exists() and samples_active,
            "Sanitized real webhook samples exist and are active.",
            real_samples_exists=real_samples_path.exists(),
            samples_active=samples_active,
        ),
    ]
    pending = [check["id"] for check in checks if check["status"] != "ok"]
    return {
        "status": "ready" if not pending else "pending_external_inputs",
        "ok": not pending,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "paths": {
            "env_file": str(env_path),
            "im_template": str(template_path),
            "token_store": str(token_store_path),
            "real_samples": str(real_samples_path),
        },
        "checks": checks,
        "pending_checks": pending,
        "secret_values_written": False,
    }


def render_p5_external_state_text(report: dict[str, Any]) -> str:
    lines = [
        f"diagnose_p5_external_state status={report['status']} ok={str(report['ok']).lower()} "
        f"pending={len(report['pending_checks'])}",
    ]
    for check in report["checks"]:
        lines.append(f"CHECK {check['id']} status={check['status']} message={check['message']}")
        missing = check.get("missing_keys") or []
        if missing:
            lines.append(f"MISSING {check['id']} {','.join(missing)}")
    return "\n".join(lines) + "\n"


def _template_path(path: str) -> Path:
    template = Path(path)
    if template.exists():
        return template
    fallback = Path("/data/codekb/state/im-config.todo.env")
    return fallback if fallback.exists() else template


def _check(check_id: str, ok: bool, message: str, **details: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "ok" if ok else "pending",
        "message": message,
        **details,
    }


def _token_store_summary(path: Path) -> dict[str, Any]:
    base = {
        "token_store_exists": path.exists(),
        "total_tokens": 0,
        "active_tokens": 0,
        "revoked_tokens": 0,
        "expired_tokens": 0,
        "token_store_status": "missing" if not path.exists() else "ok",
    }
    if not path.exists():
        return base
    try:
        summary = JsonUserTokenStore(path).summary()
    except Exception as exc:
        return {**base, "token_store_status": "error", "token_store_error": exc.__class__.__name__}
    return {
        **base,
        "token_store_status": "ok",
        "total_tokens": int(summary.get("total", 0) or 0),
        "active_tokens": int(summary.get("active", 0) or 0),
        "revoked_tokens": int(summary.get("revoked", 0) or 0),
        "expired_tokens": int(summary.get("expired", 0) or 0),
    }


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _clean(value)
    return values


def _present(value: str | None) -> bool:
    return bool(_clean(value))


def _clean(value: str | None) -> str:
    return str(value or "").strip().strip('"').strip("'")
