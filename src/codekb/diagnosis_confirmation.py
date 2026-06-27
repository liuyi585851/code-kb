from __future__ import annotations

from typing import Any

from .diagnosis import DiagnosticResult
from .user_confirmation import JsonlUserConfirmationOutbox, UserConfirmationRequest


CONFIRMATION_POLICIES = {"never", "always", "needs_review"}


def confirmation_policy(arguments: dict[str, Any]) -> str:
    policy = str(arguments.get("confirmation_policy", "never") or "never").strip()
    if not policy:
        return "never"
    if policy not in CONFIRMATION_POLICIES:
        raise ValueError("confirmation_policy must be one of: " + ", ".join(sorted(CONFIRMATION_POLICIES)))
    return policy


def maybe_append_diagnosis_confirmation(
    result: DiagnosticResult,
    arguments: dict[str, Any],
    *,
    confirmation_policy: str,
    user_token: str,
    confirmation_outbox_path: str,
) -> UserConfirmationRequest | None:
    if not should_request_confirmation(result, confirmation_policy=confirmation_policy):
        return None
    reason = str(arguments.get("confirmation_reason", "") or "").strip() or "human_review_required"
    message = str(arguments.get("confirmation_message", "") or "").strip() or default_confirmation_message(
        result,
        reason=reason,
    )
    extra_payload = arguments.get("confirmation_payload") or {}
    if not isinstance(extra_payload, dict):
        raise ValueError("confirmation_payload must be a JSON object")
    payload = {
        "diagnosis_id": result.diagnosis_id,
        "answer_id": result.answer_id,
        "trace_id": result.trace_id,
        "query": result.query,
        "sub_kbs": list(result.sub_kbs),
        "refused": result.refused,
        "confidence": result.confidence,
        "finding_types": [finding.finding_type for finding in result.findings],
        **dict(extra_payload),
    }
    return JsonlUserConfirmationOutbox(confirmation_outbox_path).append(
        user_token=user_token,
        reason=reason,
        message=message,
        payload=payload,
    )


def should_request_confirmation(result: DiagnosticResult, *, confirmation_policy: str) -> bool:
    if confirmation_policy == "never":
        return False
    if confirmation_policy == "always":
        return True
    return result.refused or bool(result.findings) or bool(result.gap_candidate)


def default_confirmation_message(result: DiagnosticResult, *, reason: str) -> str:
    if reason == "problem_solved":
        return "请确认本次问题是否已解决"
    if reason == "interaction_complete":
        return "请确认本次 AI 交互是否完成"
    if reason == "gap_candidate_review":
        return "请确认是否提交本次 KB 缺口候选"
    if result.refused:
        return "本次诊断未能给出可引用结论，请确认是否需要人工处理"
    if result.findings:
        return "本次诊断存在低置信或治理风险，请确认是否需要人工处理"
    return "请确认本次诊断是否需要人工处理"


def public_confirmation_request(request: UserConfirmationRequest) -> dict[str, Any]:
    return {
        "confirmation_id": request.confirmation_id,
        "created_at": request.created_at,
        "channel": request.channel,
        "target_user_token_hash_prefix": request.target_user_token_hash[:12],
        "reason": request.reason,
        "message": request.message,
        "payload": dict(request.payload),
        "status": request.status,
    }
