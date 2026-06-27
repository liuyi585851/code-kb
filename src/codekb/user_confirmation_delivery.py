from __future__ import annotations

import html
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from .user_auth import JsonUserTokenStore, UserTokenBinding
from .user_confirmation import UserConfirmationRequest


# access_token 失效/过期时 IM 返回的 errcode。
IM_TOKEN_EXPIRED_ERRCODE = 42001


class IMTransport(Protocol):
    """IM 客户端的 HTTP 接缝;可注入,方便写确定性测试。"""

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_json(self, url: str) -> dict[str, Any]: ...


class _UrllibIMTransport:
    """默认 transport:完全保持原先的 urllib 行为。"""

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _post_json(url, payload)

    def get_json(self, url: str) -> dict[str, Any]:
        return _get_json(url)


IM_ROUTE_KEYS = (
    "im_userid",
    "im_user_id",
    "userid",
    "user_id",
    "open_userid",
    "im_message_target",
    "im_robot_key",
    "im_robot_webhook",
    "contact_route",
    "route_value",
)


class UserConfirmationClient(Protocol):
    def send_confirmation(
        self,
        *,
        to_user: str,
        request: UserConfirmationRequest,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UserConfirmationDeliveryResult:
    confirmation_id: str
    channel: str
    reason: str
    status: str
    detail: str
    target_user_token_hash_prefix: str
    route_user_hash: str
    response: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "channel": self.channel,
            "reason": self.reason,
            "status": self.status,
            "detail": self.detail,
            "target_user_token_hash_prefix": self.target_user_token_hash_prefix,
            "route_user_hash": self.route_user_hash,
            "response": dict(self.response),
        }


@dataclass(frozen=True)
class UserConfirmationDeliveryReport:
    outbox_path: str
    token_store_path: str
    delivery_log_path: str
    execute: bool
    write_enabled: bool
    total: int
    processed: int
    invalid_lines: int
    executed_operations: int
    blocked_operations: int
    status: str
    results: tuple[UserConfirmationDeliveryResult, ...]
    created_at: str
    dead_lettered: int = 0
    dead_letter_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbox_path": self.outbox_path,
            "token_store_path": self.token_store_path,
            "delivery_log_path": self.delivery_log_path,
            "execute": self.execute,
            "write_enabled": self.write_enabled,
            "total": self.total,
            "processed": self.processed,
            "invalid_lines": self.invalid_lines,
            "executed_operations": self.executed_operations,
            "blocked_operations": self.blocked_operations,
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
            "created_at": self.created_at,
            "dead_lettered": self.dead_lettered,
            "dead_letter_path": self.dead_letter_path,
        }


class IMAppMessageClient:
    def __init__(
        self,
        *,
        corp_id: str,
        app_secret: str,
        agent_id: str,
        api_base: str = "https://im-api.example.com/cgi-bin",
        confirmation_url_base: str = "",
        transport: IMTransport | None = None,
        clock: Callable[[], float] | None = None,
        token_expiry_margin_seconds: int = 60,
        default_token_ttl_seconds: int = 7200,
    ) -> None:
        self.corp_id = str(corp_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.agent_id = str(agent_id or "").strip()
        self.api_base = str(api_base or "https://im-api.example.com/cgi-bin").rstrip("/")
        self.confirmation_url_base = str(confirmation_url_base or "").strip()
        self._transport: IMTransport = transport or _UrllibIMTransport()
        self._clock: Callable[[], float] = clock or time.monotonic
        self._token_expiry_margin = max(0, int(token_expiry_margin_seconds))
        self._default_token_ttl = max(1, int(default_token_ttl_seconds))
        self._access_token = ""
        self._access_token_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "IMAppMessageClient":
        return cls(
            corp_id=os.getenv("CODEKB_IM_CORP_ID", ""),
            app_secret=os.getenv("CODEKB_IM_APP_SECRET", ""),
            agent_id=os.getenv("CODEKB_IM_AGENT_ID", ""),
            api_base=os.getenv("CODEKB_IM_API_BASE", "https://im-api.example.com/cgi-bin"),
            confirmation_url_base=os.getenv("CODEKB_IM_CONFIRM_URL_BASE", ""),
        )

    def configured(self) -> bool:
        return bool(self.corp_id and self.app_secret and self.agent_id)

    def send_confirmation(
        self,
        *,
        to_user: str,
        request: UserConfirmationRequest,
    ) -> dict[str, Any]:
        if not self.configured():
            raise RuntimeError("IM client is not configured")
        validation = validate_im_delivery_configuration(
            agent_id=self.agent_id,
            confirmation_url_base=self.confirmation_url_base,
            require_confirmation_url=True,
        )
        if not validation["ok"]:
            raise RuntimeError(f"IM delivery configuration invalid: {', '.join(validation['errors'])}")
        message = build_im_confirmation_message(
            to_user=to_user,
            agent_id=self.agent_id,
            request=request,
            confirmation_url_base=self.confirmation_url_base,
        )
        response = self._send_message(message)
        if int(response.get("errcode", 0) or 0) == IM_TOKEN_EXPIRED_ERRCODE:
            # access_token 过期/失效:丢掉缓存的 token,重新取一个,只重试一次,
            # 还不行就放弃。
            response = self._send_message(message, force_refresh=True)
        if int(response.get("errcode", 0) or 0) != 0:
            raise RuntimeError(f"IM message/send failed: {response.get('errmsg', response)}")
        return _safe_im_response(response)

    def _send_message(self, message: dict[str, Any], *, force_refresh: bool = False) -> dict[str, Any]:
        token = self._get_access_token(force_refresh=force_refresh)
        return self._transport.post_json(
            f"{self.api_base}/message/send?access_token={urllib.parse.quote(token)}",
            message,
        )

    def _get_access_token(self, *, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._access_token
            and self._clock() < self._access_token_expires_at
        ):
            return self._access_token
        # 强制刷新(或已过期)时,先清掉缓存再重新拉取。
        self._access_token = ""
        self._access_token_expires_at = 0.0
        query = urllib.parse.urlencode({"corpid": self.corp_id, "corpsecret": self.app_secret})
        response = self._transport.get_json(f"{self.api_base}/gettoken?{query}")
        if int(response.get("errcode", 0) or 0) != 0:
            raise RuntimeError(f"IM gettoken failed: {response.get('errmsg', response)}")
        token = str(response.get("access_token", "") or "").strip()
        if not token:
            raise RuntimeError("IM gettoken response missing access_token")
        expires_in = int(response.get("expires_in", self._default_token_ttl) or self._default_token_ttl)
        self._access_token = token
        self._access_token_expires_at = self._clock() + max(0, expires_in - self._token_expiry_margin)
        return token


def process_user_confirmation_outbox(
    path: str | Path,
    *,
    token_store_path: str | Path,
    execute: bool = False,
    write_enabled: bool = False,
    client: UserConfirmationClient | None = None,
    limit: int = 50,
    confirmation_id: str = "",
    report_path: str | Path | None = None,
    delivery_log_path: str | Path | None = None,
    max_retries: int = 0,
    backoff_base_seconds: float = 0.0,
    sleeper: Callable[[float], None] | None = None,
    dead_letter_path: str | Path | None = None,
) -> UserConfirmationDeliveryReport:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    sleep = sleeper if sleeper is not None else time.sleep
    outbox_path = Path(path)
    requests, invalid_lines = _read_confirmation_outbox(
        outbox_path,
        limit=limit,
        confirmation_id=confirmation_id,
    )
    delivered_ids = _load_delivered_confirmation_ids(delivery_log_path)
    token_store = JsonUserTokenStore(token_store_path)
    active_bindings = {
        binding.token_hash: binding
        for binding in token_store.list()
        if token_store.validate_hash(binding.token_hash)
    }
    if execute and write_enabled and client is None:
        client = IMAppMessageClient.from_env()
    results: list[UserConfirmationDeliveryResult] = []
    dead_lettered = 0
    for request in requests:
        result, attempts, last_error = _process_confirmation_request(
            request,
            active_bindings=active_bindings,
            execute=execute,
            write_enabled=write_enabled,
            client=client,
            delivered_ids=delivered_ids,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            sleep=sleep,
        )
        results.append(result)
        if result.status == "executed":
            delivered_ids.add(request.confirmation_id)
            _append_delivery_receipt(delivery_log_path, result)
        elif result.status == "failed" and dead_letter_path:
            _append_dead_letter(dead_letter_path, request, last_error, attempts)
            dead_lettered += 1
    result_items = tuple(results)
    executed_operations = sum(1 for result in results if result.status == "executed")
    blocked_operations = sum(1 for result in results if result.status.startswith("blocked"))
    report = UserConfirmationDeliveryReport(
        outbox_path=str(outbox_path),
        token_store_path=str(token_store_path),
        delivery_log_path=str(delivery_log_path or ""),
        execute=execute,
        write_enabled=write_enabled,
        total=len(requests) + invalid_lines,
        processed=len(requests),
        invalid_lines=invalid_lines,
        executed_operations=executed_operations,
        blocked_operations=blocked_operations,
        status=_report_status(result_items, invalid_lines=invalid_lines),
        results=result_items,
        created_at=_now(),
        dead_lettered=dead_lettered,
        dead_letter_path=str(dead_letter_path or ""),
    )
    if report_path:
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def validate_im_delivery_configuration(
    *,
    agent_id: str,
    confirmation_url_base: str = "",
    require_confirmation_url: bool = False,
) -> dict[str, Any]:
    normalized_agent_id = str(agent_id or "").strip()
    normalized_url = str(confirmation_url_base or "").strip()
    errors: list[str] = []
    if not normalized_agent_id:
        errors.append("CODEKB_IM_AGENT_ID is required")
    elif not normalized_agent_id.isdecimal():
        errors.append("CODEKB_IM_AGENT_ID must be numeric")
    if require_confirmation_url and not normalized_url:
        errors.append("CODEKB_IM_CONFIRM_URL_BASE is required for real IM sends")
    if normalized_url:
        parsed = urllib.parse.urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("CODEKB_IM_CONFIRM_URL_BASE must be an absolute http(s) URL")
        if parsed.fragment:
            errors.append("CODEKB_IM_CONFIRM_URL_BASE must not contain a URL fragment")
    return {
        "ok": not errors,
        "errors": errors,
        "agent_id_configured": bool(normalized_agent_id),
        "agent_id_numeric": bool(normalized_agent_id and normalized_agent_id.isdecimal()),
        "confirmation_url_configured": bool(normalized_url),
        "confirmation_url_scheme": urllib.parse.urlparse(normalized_url).scheme if normalized_url else "",
    }


def build_im_confirmation_message(
    *,
    to_user: str,
    agent_id: str,
    request: UserConfirmationRequest,
    confirmation_url_base: str = "",
) -> dict[str, Any]:
    validation = validate_im_delivery_configuration(
        agent_id=agent_id,
        confirmation_url_base=confirmation_url_base,
        require_confirmation_url=False,
    )
    if not validation["ok"]:
        raise ValueError(f"invalid IM confirmation message configuration: {', '.join(validation['errors'])}")
    title = _reason_title(request.reason)
    url = _confirmation_url(confirmation_url_base, request.confirmation_id)
    if url:
        return {
            "touser": to_user,
            "msgtype": "textcard",
            "agentid": int(agent_id),
            "textcard": {
                "title": title,
                "description": _textcard_description(request),
                "url": url,
                "btntxt": "确认",
            },
        }
    return {
        "touser": to_user,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": f"{title}\n{request.message}\nconfirmation_id={request.confirmation_id}"},
    }


def _process_confirmation_request(
    request: UserConfirmationRequest,
    *,
    active_bindings: dict[str, UserTokenBinding],
    execute: bool,
    write_enabled: bool,
    client: UserConfirmationClient | None,
    delivered_ids: set[str],
    max_retries: int = 0,
    backoff_base_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[UserConfirmationDeliveryResult, int, str]:
    if request.confirmation_id in delivered_ids:
        return _result(request, "skipped_already_delivered", "confirmation was already delivered"), 0, ""
    if request.status and request.status != "pending_confirmation":
        return _result(request, "skipped_non_pending", f"status={request.status}"), 0, ""
    if request.channel != "im":
        return _result(request, "blocked_unsupported_channel", f"unsupported channel: {request.channel}"), 0, ""
    binding = active_bindings.get(request.target_user_token_hash)
    if binding is None:
        return _result(request, "blocked_missing_user_binding", "active token binding not found"), 0, ""
    route_user = _route_user_id(binding)
    route_hash = _hash(route_user)
    if not route_user:
        return _result(request, "blocked_missing_route", "token binding metadata missing current-user route"), 0, ""
    if not execute:
        return _result(request, "validated", "dry_run", route_user_hash=route_hash), 0, ""
    if not write_enabled:
        return (
            _result(
                request,
                "blocked_write_disabled",
                "set CODEKB_ENABLE_IM_SEND=1 to execute IM sends",
                route_user_hash=route_hash,
            ),
            0,
            "",
        )
    if client is None:
        return (
            _result(
                request,
                "blocked_client_unconfigured",
                "configure CODEKB_IM_CORP_ID/AGENT_ID/APP_SECRET",
                route_user_hash=route_hash,
            ),
            0,
            "",
        )
    # 发送时带有限次重试 + 指数退避。max_retries == 0(默认值)时只发一次,
    # 与原先行为完全一致。
    last_error = ""
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts += 1
        try:
            response = client.send_confirmation(to_user=route_user, request=request)
        except Exception as exc:  # noqa: BLE001 - 统一暴露成 "failed" 结果
            last_error = str(exc)
            if attempt < max_retries:
                sleep(backoff_base_seconds * (2 ** attempt))
                continue
            break
        else:
            return (
                _result(request, "executed", "sent", route_user_hash=route_hash, response=response),
                attempts,
                "",
            )
    detail = last_error if attempts <= 1 else f"{last_error} (attempts={attempts})"
    return _result(request, "failed", detail, route_user_hash=route_hash), attempts, last_error


def _read_confirmation_outbox(
    path: Path,
    *,
    limit: int,
    confirmation_id: str = "",
) -> tuple[tuple[UserConfirmationRequest, ...], int]:
    if not path.exists():
        return (), 0
    normalized_confirmation_id = str(confirmation_id or "").strip()
    requests: list[UserConfirmationRequest] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(requests) >= limit:
            break
        if not line.strip():
            continue
        try:
            request = _request_from_dict(json.loads(line))
        except (TypeError, ValueError, json.JSONDecodeError):
            if not normalized_confirmation_id:
                invalid_lines += 1
            continue
        if normalized_confirmation_id and request.confirmation_id != normalized_confirmation_id:
            continue
        requests.append(request)
    return tuple(requests), invalid_lines


def _request_from_dict(payload: dict[str, Any]) -> UserConfirmationRequest:
    return UserConfirmationRequest(
        confirmation_id=str(payload.get("confirmation_id", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
        channel=str(payload.get("channel", "im") or "im").strip(),
        target_user_token_hash=str(payload.get("target_user_token_hash", "")).strip(),
        reason=str(payload.get("reason", "")).strip(),
        message=str(payload.get("message", "")).strip(),
        payload=dict(payload.get("payload") or {}),
        status=str(payload.get("status", "pending_confirmation") or "pending_confirmation").strip(),
    )


def _result(
    request: UserConfirmationRequest,
    status: str,
    detail: str,
    *,
    route_user_hash: str = "",
    response: dict[str, Any] | None = None,
) -> UserConfirmationDeliveryResult:
    return UserConfirmationDeliveryResult(
        confirmation_id=request.confirmation_id,
        channel=request.channel,
        reason=request.reason,
        status=status,
        detail=detail,
        target_user_token_hash_prefix=request.target_user_token_hash[:12],
        route_user_hash=route_user_hash,
        response=dict(response or {}),
    )


def _route_user_id(binding: UserTokenBinding) -> str:
    for key in IM_ROUTE_KEYS:
        value = str(binding.metadata.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _report_status(results: tuple[UserConfirmationDeliveryResult, ...], *, invalid_lines: int) -> str:
    statuses = {result.status for result in results}
    if invalid_lines:
        return "invalid"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status.startswith("blocked") for status in statuses):
        return "blocked"
    if "executed" in statuses:
        return "executed"
    if statuses and all(status.startswith("skipped") for status in statuses):
        return "skipped"
    return "validated"


def _load_delivered_confirmation_ids(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    delivery_path = Path(path)
    if not delivery_path.exists():
        return set()
    delivered: set[str] = set()
    for line in delivery_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        confirmation_id = str(payload.get("confirmation_id", "") or "").strip()
        if confirmation_id and str(payload.get("status", "") or "") == "executed":
            delivered.add(confirmation_id)
    return delivered


def _append_delivery_receipt(path: str | Path | None, result: UserConfirmationDeliveryResult) -> None:
    if not path:
        return
    delivery_path = Path(path)
    delivery_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "confirmation_id": result.confirmation_id,
        "created_at": _now(),
        "channel": result.channel,
        "reason": result.reason,
        "status": result.status,
        "target_user_token_hash_prefix": result.target_user_token_hash_prefix,
        "route_user_hash": result.route_user_hash,
        "response": result.response,
    }
    with delivery_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def _append_dead_letter(
    path: str | Path | None,
    request: UserConfirmationRequest,
    last_error: str,
    attempts: int,
) -> None:
    if not path:
        return
    dead_letter_path = Path(path)
    dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "confirmation_id": request.confirmation_id,
        "created_at": _now(),
        "channel": request.channel,
        "reason": request.reason,
        "target_user_token_hash_prefix": request.target_user_token_hash[:12],
        "last_error": last_error,
        "attempts": attempts,
    }
    with dead_letter_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _reason_title(reason: str) -> str:
    titles = {
        "interaction_complete": "Code-KB 交互确认",
        "problem_solved": "Code-KB 问题解决确认",
        "human_review_required": "Code-KB 人审确认",
        "gap_candidate_review": "Code-KB 缺口候选确认",
    }
    return titles.get(reason, "Code-KB 确认")


def _textcard_description(request: UserConfirmationRequest) -> str:
    message = html.escape(request.message or "请确认本次诊断结果。")
    reason = html.escape(request.reason)
    created_at = html.escape(request.created_at)
    return (
        f"<div class=\"gray\">{created_at}</div>"
        f"<div class=\"normal\">{message}</div>"
        f"<div class=\"highlight\">reason={reason}</div>"
    )


def _confirmation_url(base: str, confirmation_id: str) -> str:
    normalized = str(base or "").strip()
    if not normalized:
        return ""
    separator = "&" if "?" in normalized else "?"
    return f"{normalized}{separator}confirmation_id={urllib.parse.quote(confirmation_id)}"


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_im_response(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "errcode": response.get("errcode", 0),
        "errmsg": response.get("errmsg", ""),
        "msgid": response.get("msgid", ""),
    }


def _hash(value: str) -> str:
    import hashlib

    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
