from __future__ import annotations

import json
from typing import Any, Sequence

from .mcp_server import DiagnoseMcpRuntime, handle_mcp_request
from .user_auth import JsonUserTokenStore, public_token_metadata
from .user_confirmation import JsonlUserConfirmationResponseStore, public_confirmation_response
from .user_confirmation_delivery import UserConfirmationDeliveryResult, process_user_confirmation_outbox


DEFAULT_CURRENT_USER_SMOKE_QUERY = "DEVICE_SEQ 是什么？"
DEFAULT_CURRENT_USER_SMOKE_SUB_KBS = ("testing",)
DEFAULT_CURRENT_USER_SMOKE_REASON = "human_review_required"
DEFAULT_CURRENT_USER_SMOKE_MESSAGE = "P5 current-user smoke confirmation"
DEFAULT_CURRENT_USER_SMOKE_COMMENT = "P5 smoke confirmed"


def run_current_user_smoke(
    *,
    auth_token: str,
    token_store_path: str,
    confirmation_outbox_path: str,
    confirmation_responses_path: str,
    query: str = DEFAULT_CURRENT_USER_SMOKE_QUERY,
    sub_kbs: Sequence[str] = DEFAULT_CURRENT_USER_SMOKE_SUB_KBS,
    reason: str = DEFAULT_CURRENT_USER_SMOKE_REASON,
    message: str = DEFAULT_CURRENT_USER_SMOKE_MESSAGE,
    respond: bool = False,
    decision: str = "confirmed",
    comment: str = DEFAULT_CURRENT_USER_SMOKE_COMMENT,
    delivery_report_path: str = "",
    delivery_log_path: str = "",
    fixture_path: str = "data/fixtures/sample_corpus.jsonl",
    aliases_path: str = "data/entity_aliases.yaml",
    registry_path: str = "docs/kb-registry.draft.yaml",
    governance_policy_path: str = "docs/governance-policy.draft.yaml",
    feedback_log_path: str = "/data/codekb/logs/feedback.jsonl",
    candidate_store_path: str = "/data/codekb/state/candidates.json",
    pending_docs_dir: str = "/data/codekb/pending-docs",
    trace_log_path: str = "",
    retriever: str = "bm25-lite",
    index_db_path: str = "",
    api_base_url: str = "http://127.0.0.1:8080",
    top_k: int = 4,
    include_governance: bool = False,
) -> dict[str, Any]:
    token = str(auth_token or "").strip()
    if not token:
        raise ValueError("auth_token is required")
    binding = JsonUserTokenStore(token_store_path).validate(token)
    if binding is None:
        raise PermissionError("invalid auth token")

    normalized_sub_kbs = tuple(str(item).strip() for item in sub_kbs if str(item).strip())
    runtime = DiagnoseMcpRuntime(
        fixture_path=fixture_path,
        aliases_path=aliases_path,
        registry_path=registry_path,
        governance_policy_path=governance_policy_path,
        feedback_log_path=feedback_log_path,
        candidate_store_path=candidate_store_path,
        pending_docs_dir=pending_docs_dir,
        trace_log_path=trace_log_path,
        retriever=retriever,
        index_db_path=index_db_path,
        api_base_url=api_base_url,
        token_store_path=token_store_path,
        confirmation_outbox_path=confirmation_outbox_path,
    )

    diagnosis = _call_mcp_tool_json(
        runtime,
        name="codekb_diagnose",
        arguments={
            "auth_token": token,
            "query": str(query or "").strip() or DEFAULT_CURRENT_USER_SMOKE_QUERY,
            "sub_kbs": list(normalized_sub_kbs),
            "top_k": top_k,
            "include_governance": include_governance,
            "confirmation_policy": "always",
            "confirmation_reason": reason,
            "confirmation_message": message,
            "confirmation_payload": {"smoke": "current_user"},
        },
        request_id=1,
    )
    confirmation = _confirmation_from_diagnosis(diagnosis)
    confirmation_id = str(confirmation.get("confirmation_id", "") or "")
    delivery_report = process_user_confirmation_outbox(
        confirmation_outbox_path,
        token_store_path=token_store_path,
        execute=False,
        write_enabled=False,
        limit=1,
        confirmation_id=confirmation_id,
        report_path=delivery_report_path or None,
        delivery_log_path=delivery_log_path or None,
    )
    delivery_result = _delivery_result_for(confirmation_id, delivery_report.results)

    response_payload: dict[str, Any] | None = None
    if respond:
        response = JsonlUserConfirmationResponseStore(confirmation_responses_path).record(
            outbox_path=confirmation_outbox_path,
            user_token=token,
            confirmation_id=confirmation_id,
            decision=decision,
            comment=comment,
            metadata={"source": "current_user_smoke"},
        )
        response_payload = public_confirmation_response(response)

    ok = delivery_result is not None and delivery_result.status == "validated"
    if respond and response_payload is None:
        ok = False
    return {
        "status": _smoke_status(ok=ok, respond=respond, delivery_result=delivery_result),
        "ok": ok,
        "auth": {
            "token_valid": True,
            "token_id": binding.token_id,
            "user_id_hash": binding.user_id_hash,
            "token_hash_prefix": binding.token_hash[:12],
            "display_name": binding.display_name,
            "scopes": list(binding.scopes),
            "metadata": public_token_metadata(binding.metadata),
        },
        "diagnosis": _diagnosis_summary(diagnosis),
        "confirmation": _public_confirmation(confirmation),
        "delivery": {
            "report_status": delivery_report.status,
            "processed": delivery_report.processed,
            "blocked_operations": delivery_report.blocked_operations,
            "result": delivery_result.to_dict() if delivery_result else None,
        },
        "response": response_payload,
    }


def _call_mcp_tool_json(
    runtime: DiagnoseMcpRuntime,
    *,
    name: str,
    arguments: dict[str, Any],
    request_id: int,
) -> dict[str, Any]:
    response = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        runtime,
    )
    if response is None:
        raise RuntimeError(f"MCP tool {name} returned no response")
    if "error" in response:
        message = str(response["error"].get("message", "") or "")
        if "auth" in message.lower() or "token" in message.lower():
            raise PermissionError(message)
        raise RuntimeError(message or f"MCP tool {name} failed")
    result = response.get("result") or {}
    payload = _tool_payload(result)
    if result.get("isError"):
        raise RuntimeError(f"MCP tool {name} returned error payload: {payload}")
    return payload


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") or []
    if not content:
        return {}
    text = str((content[0] or {}).get("text", "") or "")
    if not text:
        return {}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("MCP tool payload must be a JSON object")
    return payload


def _diagnosis_summary(payload: dict[str, Any]) -> dict[str, Any]:
    citations = []
    for citation in list(payload.get("citations") or [])[:5]:
        if not isinstance(citation, dict):
            continue
        citations.append(
            {
                "docid": citation.get("docid", ""),
                "title": citation.get("title", ""),
                "anchor": citation.get("anchor", ""),
                "score": citation.get("score", 0),
            }
        )
    return {
        "diagnosis_id": payload.get("diagnosis_id", ""),
        "answer_id": payload.get("answer_id", ""),
        "trace_id": payload.get("trace_id", ""),
        "query": payload.get("query", ""),
        "sub_kbs": list(payload.get("sub_kbs") or []),
        "refused": bool(payload.get("refused", False)),
        "refusal_reason": payload.get("refusal_reason", ""),
        "confidence": payload.get("confidence", 0),
        "citations": citations,
        "finding_types": [
            item.get("finding_type", "")
            for item in payload.get("findings", [])
            if isinstance(item, dict)
        ],
        "confirmation_id": (payload.get("confirmation") or {}).get("confirmation_id", ""),
    }


def _public_confirmation(payload: dict[str, Any]) -> dict[str, Any]:
    token_hash = str(payload.get("target_user_token_hash", "") or "")
    token_hash_prefix = str(payload.get("target_user_token_hash_prefix", "") or "") or token_hash[:12]
    return {
        "confirmation_id": payload.get("confirmation_id", ""),
        "created_at": payload.get("created_at", ""),
        "channel": payload.get("channel", ""),
        "reason": payload.get("reason", ""),
        "message": payload.get("message", ""),
        "payload": dict(payload.get("payload") or {}),
        "status": payload.get("status", ""),
        "target_user_token_hash_prefix": token_hash_prefix,
    }


def _confirmation_from_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    confirmation = payload.get("confirmation")
    if not isinstance(confirmation, dict):
        raise RuntimeError("diagnosis did not create a current-user confirmation")
    return confirmation


def _delivery_result_for(
    confirmation_id: str,
    results: Sequence[UserConfirmationDeliveryResult],
) -> UserConfirmationDeliveryResult | None:
    for result in results:
        if result.confirmation_id == confirmation_id:
            return result
    return None


def _smoke_status(
    *,
    ok: bool,
    respond: bool,
    delivery_result: UserConfirmationDeliveryResult | None,
) -> str:
    if ok:
        return "responded" if respond else "validated"
    if delivery_result is None:
        return "delivery_not_processed"
    return delivery_result.status
