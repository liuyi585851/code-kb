from __future__ import annotations

import json
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from .redaction import redact_sensitive_text, redact_sensitive_url


CONFIRMATION_REASONS = {"interaction_complete", "problem_solved", "human_review_required", "gap_candidate_review"}
CONFIRMATION_DECISIONS = {"confirmed", "rejected", "needs_followup"}


@dataclass(frozen=True)
class UserConfirmationRequest:
    confirmation_id: str
    created_at: str
    channel: str
    target_user_token_hash: str
    reason: str
    message: str
    payload: dict[str, Any]
    status: str = "pending_confirmation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "created_at": self.created_at,
            "channel": self.channel,
            "target_user_token_hash": self.target_user_token_hash,
            "reason": self.reason,
            "message": self.message,
            "payload": dict(self.payload),
            "status": self.status,
        }


@dataclass(frozen=True)
class UserConfirmationResponse:
    response_id: str
    confirmation_id: str
    created_at: str
    decision: str
    responder_user_token_hash: str
    comment: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_id": self.response_id,
            "confirmation_id": self.confirmation_id,
            "created_at": self.created_at,
            "decision": self.decision,
            "responder_user_token_hash": self.responder_user_token_hash,
            "comment": self.comment,
            "metadata": dict(self.metadata),
        }


class JsonlUserConfirmationOutbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        user_token: str,
        reason: str,
        message: str,
        payload: dict[str, Any] | None = None,
        channel: str = "im",
    ) -> UserConfirmationRequest:
        token = str(user_token or "").strip()
        if not token:
            raise ValueError("user token is required for confirmation push")
        normalized_reason = str(reason or "").strip()
        if normalized_reason not in CONFIRMATION_REASONS:
            raise ValueError("confirmation reason must be one of: " + ", ".join(sorted(CONFIRMATION_REASONS)))
        request = UserConfirmationRequest(
            confirmation_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            channel=str(channel or "im").strip() or "im",
            target_user_token_hash=sha256(token.encode("utf-8")).hexdigest(),
            reason=normalized_reason,
            message=redact_sensitive_text(str(message or "").strip()),
            payload=_redact_payload(dict(payload or {})),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return request


class JsonlUserConfirmationResponseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        outbox_path: str | Path,
        user_token: str,
        confirmation_id: str,
        decision: str,
        comment: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> UserConfirmationResponse:
        token = str(user_token or "").strip()
        if not token:
            raise ValueError("user token is required for confirmation response")
        request = find_user_confirmation_request(outbox_path, confirmation_id)
        if request is None:
            raise ValueError("confirmation request not found")
        token_hash = sha256(token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(token_hash, request.target_user_token_hash):
            raise PermissionError("confirmation token does not match target user")
        normalized_decision = str(decision or "").strip()
        if normalized_decision not in CONFIRMATION_DECISIONS:
            raise ValueError("confirmation decision must be one of: " + ", ".join(sorted(CONFIRMATION_DECISIONS)))
        response = UserConfirmationResponse(
            response_id=str(uuid4()),
            confirmation_id=request.confirmation_id,
            created_at=_now(),
            decision=normalized_decision,
            responder_user_token_hash=token_hash,
            comment=redact_sensitive_text(str(comment or "").strip()),
            metadata=_redact_payload(dict(metadata or {})),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(response.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return response

    def list(self, *, limit: int = 50) -> tuple[UserConfirmationResponse, ...]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if not self.path.exists():
            return ()
        responses: list[UserConfirmationResponse] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if len(responses) >= limit:
                break
            if not line.strip():
                continue
            try:
                responses.append(_response_from_dict(json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(responses)

    def summary(self, *, limit: int = 50) -> dict[str, Any]:
        responses = self.list(limit=limit)
        latest = latest_confirmation_responses(responses)
        return {
            "path": str(self.path),
            "total": len(responses),
            "latest_total": len(latest),
            "decisions": {
                decision: sum(1 for response in responses if response.decision == decision)
                for decision in sorted(CONFIRMATION_DECISIONS)
            },
            "responses": [public_confirmation_response(response) for response in responses],
        }


def list_user_confirmations(
    outbox_path: str | Path,
    *,
    responses_path: str | Path,
    user_token: str,
    limit: int = 50,
    include_responded: bool = False,
) -> tuple[dict[str, Any], ...]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    token_hash = _token_hash(user_token)
    if not token_hash:
        raise ValueError("user token is required")
    responses = latest_confirmation_responses(JsonlUserConfirmationResponseStore(responses_path).list(limit=10000))
    matches: list[dict[str, Any]] = []
    for request in load_user_confirmation_requests(outbox_path, limit=10000):
        if not hmac.compare_digest(request.target_user_token_hash, token_hash):
            continue
        response = responses.get(request.confirmation_id)
        if response is not None and not include_responded:
            continue
        matches.append(public_confirmation_request(request, response=response))
        if len(matches) >= limit:
            break
    return tuple(matches)


def get_user_confirmation_detail(
    outbox_path: str | Path,
    *,
    responses_path: str | Path,
    user_token: str,
    confirmation_id: str,
) -> dict[str, Any]:
    token_hash = _token_hash(user_token)
    if not token_hash:
        raise ValueError("user token is required")
    request = find_user_confirmation_request(outbox_path, confirmation_id)
    if request is None:
        raise ValueError("confirmation request not found")
    if not hmac.compare_digest(request.target_user_token_hash, token_hash):
        raise PermissionError("confirmation token does not match target user")
    responses = latest_confirmation_responses(JsonlUserConfirmationResponseStore(responses_path).list(limit=10000))
    return public_confirmation_request(request, response=responses.get(request.confirmation_id))


def load_user_confirmation_requests(path: str | Path, *, limit: int = 50) -> tuple[UserConfirmationRequest, ...]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    outbox_path = Path(path)
    if not outbox_path.exists():
        return ()
    requests: list[UserConfirmationRequest] = []
    for line in outbox_path.read_text(encoding="utf-8").splitlines():
        if len(requests) >= limit:
            break
        if not line.strip():
            continue
        try:
            requests.append(_request_from_dict(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(requests)


def find_user_confirmation_request(path: str | Path, confirmation_id: str) -> UserConfirmationRequest | None:
    normalized_id = str(confirmation_id or "").strip()
    if not normalized_id:
        raise ValueError("confirmation_id is required")
    for request in load_user_confirmation_requests(path, limit=10000):
        if request.confirmation_id == normalized_id:
            return request
    return None


def public_confirmation_response(response: UserConfirmationResponse) -> dict[str, Any]:
    return {
        "response_id": response.response_id,
        "confirmation_id": response.confirmation_id,
        "created_at": response.created_at,
        "decision": response.decision,
        "responder_user_token_hash_prefix": response.responder_user_token_hash[:12],
        "comment": response.comment,
        "metadata": dict(response.metadata),
    }


def public_confirmation_request(
    request: UserConfirmationRequest,
    *,
    response: UserConfirmationResponse | None = None,
) -> dict[str, Any]:
    payload = {
        "confirmation_id": request.confirmation_id,
        "created_at": request.created_at,
        "channel": request.channel,
        "reason": request.reason,
        "message": redact_sensitive_text(request.message),
        "payload": _redact_payload(dict(request.payload)),
        "status": "responded" if response else request.status,
        "target_user_token_hash_prefix": request.target_user_token_hash[:12],
    }
    if response is not None:
        payload["response"] = public_confirmation_response(response)
    return payload


def latest_confirmation_responses(
    responses: tuple[UserConfirmationResponse, ...] | list[UserConfirmationResponse],
) -> dict[str, UserConfirmationResponse]:
    latest: dict[str, UserConfirmationResponse] = {}
    for response in responses:
        latest[response.confirmation_id] = response
    return latest


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


def _response_from_dict(payload: dict[str, Any]) -> UserConfirmationResponse:
    return UserConfirmationResponse(
        response_id=str(payload.get("response_id", "")).strip(),
        confirmation_id=str(payload.get("confirmation_id", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
        decision=str(payload.get("decision", "")).strip(),
        responder_user_token_hash=str(payload.get("responder_user_token_hash", "")).strip(),
        comment=str(payload.get("comment", "")).strip(),
        metadata=dict(payload.get("metadata") or {}),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _token_hash(token: str) -> str:
    token = str(token or "").strip()
    if not token:
        return ""
    return sha256(token.encode("utf-8")).hexdigest()


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_url(value)
    return value
