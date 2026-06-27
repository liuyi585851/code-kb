from __future__ import annotations

import json
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, TextIO

from .api import _run_diagnosis
from .diagnosis_confirmation import (
    confirmation_policy,
    maybe_append_diagnosis_confirmation,
    public_confirmation_request,
)
from .diagnosis_integrations import code_nav_mcp_tool_definitions, diagnose_mcp_tool_definitions
from .diagnosis_webhook import preview_diagnostic_webhook, validate_diagnostic_webhook
from .code_nav import file_outline, find_files, get_symbol, list_dir, read_file_range, search_code
from .usage import record_event
from .service import OfflineKbService
from .user_auth import JsonUserTokenStore
from .user_confirmation import JsonlUserConfirmationOutbox


@dataclass
class DiagnoseMcpRuntime:
    fixture_path: str = "data/fixtures/sample_corpus.jsonl"
    aliases_path: str = "data/entity_aliases.yaml"
    registry_path: str = "docs/kb-registry.draft.yaml"
    governance_policy_path: str = "docs/governance-policy.draft.yaml"
    feedback_log_path: str = "/data/codekb/logs/feedback.jsonl"
    candidate_store_path: str = "/data/codekb/state/candidates.json"
    pending_docs_dir: str = "/data/codekb/pending-docs"
    mapping_path: str = "docs/diagnose-webhook-mapping.draft.yaml"
    trace_log_path: str = ""
    retriever: str = "bm25-lite"
    index_db_path: str = ""
    api_base_url: str = "http://127.0.0.1:8080"
    mcp_token: str = ""
    token_store_path: str = ""
    allow_static_mcp_token: bool = False
    confirmation_outbox_path: str = "/data/codekb/outbox/user-confirmation.jsonl"
    service: OfflineKbService | None = None

    def kb_service(self) -> OfflineKbService:
        if self.service is None:
            self.service = OfflineKbService(
                fixture_path=self.fixture_path,
                aliases_path=self.aliases_path,
                trace_log_path=self.trace_log_path or None,
                retriever_mode=self.retriever,
                index_db_path=self.index_db_path or None,
            )
        return self.service


def handle_mcp_request(request: dict[str, Any], runtime: DiagnoseMcpRuntime) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = str(request.get("method", "")).strip()
    params = request.get("params") or {}
    if request_id is None:
        return None
    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "codekb-diagnose", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(
                request_id,
                {
                    "tools": list(diagnose_mcp_tool_definitions(api_base_url=runtime.api_base_url))
                    + list(code_nav_mcp_tool_definitions(api_base_url=runtime.api_base_url))
                },
            )
        if method == "tools/call":
            return _result(request_id, _call_tool(params, runtime))
        return _error(request_id, -32601, f"method not found: {method}")
    except PermissionError as exc:
        return _error(request_id, -32000, str(exc), data=_auth_error_data(runtime, str(exc)))
    except Exception as exc:
        return _error(request_id, -32000, str(exc))


def run_stdio(runtime: DiagnoseMcpRuntime, *, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> int:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        else:
            if not isinstance(request, dict):
                response = _error(None, -32600, "request must be a JSON object")
            else:
                response = handle_mcp_request(request, runtime)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
            output_stream.flush()
    return 0


def _call_tool(params: dict[str, Any], runtime: DiagnoseMcpRuntime) -> dict[str, Any]:
    name = str(params.get("name", "")).strip()
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("tools/call arguments must be an object")
    if name == "codekb_diagnose":
        user_token = _verify_tool_auth(arguments, runtime)
        policy = confirmation_policy(arguments)
        if policy != "never":
            user_token = _verify_current_user_auth(arguments, runtime)
        payload = _diagnose_payload(arguments)
        result = _run_diagnosis(
            payload,
            service=runtime.kb_service(),
            fixture_path=runtime.fixture_path,
            registry_path=runtime.registry_path,
            governance_policy_path=runtime.governance_policy_path,
            feedback_log_path=runtime.feedback_log_path,
            candidate_store_path=runtime.candidate_store_path,
            pending_docs_dir=runtime.pending_docs_dir,
        )
        response_payload = result.to_dict()
        confirmation = maybe_append_diagnosis_confirmation(
            result,
            arguments,
            confirmation_policy=policy,
            user_token=user_token,
            confirmation_outbox_path=runtime.confirmation_outbox_path,
        )
        if confirmation is not None:
            response_payload["confirmation"] = public_confirmation_request(confirmation)
        return _tool_json_content(response_payload)
    if name == "codekb_diagnose_webhook_validate":
        _verify_tool_auth(arguments, runtime)
        source, payload = _webhook_arguments(arguments)
        report = validate_diagnostic_webhook(source, payload, mapping_path=runtime.mapping_path)
        return _tool_json_content(report, is_error=not report["valid"])
    if name == "codekb_diagnose_webhook_normalize":
        _verify_tool_auth(arguments, runtime)
        source, payload = _webhook_arguments(arguments)
        preview = preview_diagnostic_webhook(source, payload, mapping_path=runtime.mapping_path)
        return _tool_json_content(preview)
    if name == "codekb_request_user_confirmation":
        user_token = _verify_current_user_auth(arguments, runtime)
        request = JsonlUserConfirmationOutbox(runtime.confirmation_outbox_path).append(
            user_token=user_token,
            reason=str(arguments.get("reason", "")).strip(),
            message=str(arguments.get("message", "")).strip(),
            payload=dict(arguments.get("payload") or {}),
        )
        return _tool_json_content(public_confirmation_request(request))
    if name in {"codekb_search_code", "codekb_get_symbol", "codekb_read_file_range", "codekb_file_outline", "codekb_find_files", "codekb_list_dir"}:
        _verify_tool_auth(arguments, runtime)
        service = runtime.kb_service()
        record_event(
            name.replace("codekb_", ""),
            source="mcp",
            query=str(arguments.get("query") or arguments.get("name") or arguments.get("pattern") or arguments.get("prefix") or ""),
        )
        if name == "codekb_search_code":
            return _tool_json_content(
                search_code(
                    service.retriever,
                    str(arguments.get("query", "")).strip(),
                    sub_kbs=arguments.get("sub_kbs"),
                    top_k=int(arguments.get("top_k") or 6),
                )
            )
        if name == "codekb_get_symbol":
            return _tool_json_content(
                get_symbol(service.retriever, str(arguments.get("name", "")).strip(), top_k=int(arguments.get("top_k") or 8))
            )
        if name == "codekb_read_file_range":
            return _tool_json_content(
                read_file_range(
                    service.store,
                    str(arguments.get("path", "")).strip(),
                    int(arguments.get("start_line") or 1),
                    int(arguments.get("end_line") or 1),
                )
            )
        if name == "codekb_find_files":
            return _tool_json_content(
                find_files(
                    service.store,
                    str(arguments.get("pattern", "")).strip(),
                    sub_kbs=arguments.get("sub_kbs"),
                    limit=int(arguments.get("limit") or 50),
                )
            )
        if name == "codekb_list_dir":
            return _tool_json_content(
                list_dir(service.store, str(arguments.get("prefix", "") or "").strip(), sub_kbs=arguments.get("sub_kbs"))
            )
        return _tool_json_content(file_outline(service.store, str(arguments.get("path", "")).strip()))
    raise ValueError(f"unknown tool: {name}")


def _diagnose_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in ("query", "context", "sub_kbs", "top_k", "min_confidence", "include_governance"):
        if field in arguments:
            payload[field] = arguments[field]
    return payload


def _verify_tool_auth(arguments: dict[str, Any], runtime: DiagnoseMcpRuntime) -> str:
    provided_token = str(arguments.get("auth_token", "") or "").strip()
    if not provided_token:
        raise PermissionError("MCP auth_token is required")
    if runtime.token_store_path:
        if JsonUserTokenStore(runtime.token_store_path).validate(provided_token):
            return provided_token
        raise PermissionError("invalid MCP auth token")
    expected_token = str(runtime.mcp_token or "").strip()
    if not runtime.allow_static_mcp_token:
        if expected_token:
            raise PermissionError("current-user token store is required for MCP auth")
        raise PermissionError("MCP auth backend is not configured")
    if expected_token and provided_token == expected_token:
        return provided_token
    if not expected_token:
        raise PermissionError("MCP auth backend is not configured")
    raise PermissionError("invalid MCP auth token")


def _verify_current_user_auth(arguments: dict[str, Any], runtime: DiagnoseMcpRuntime) -> str:
    provided_token = str(arguments.get("auth_token", "") or "").strip()
    if not provided_token:
        raise PermissionError("MCP auth_token is required")
    if not runtime.token_store_path:
        raise PermissionError("current-user token store is required for confirmation")
    if JsonUserTokenStore(runtime.token_store_path).validate(provided_token):
        return provided_token
    raise PermissionError("invalid MCP auth token")


def _webhook_arguments(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    source = str(arguments.get("source", "")).strip()
    payload = arguments.get("payload")
    if not source:
        raise ValueError("source is required")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return source, payload


def _tool_json_content(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            }
        ],
        "isError": is_error,
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _auth_error_data(runtime: DiagnoseMcpRuntime, message: str) -> dict[str, Any]:
    base_url = runtime.api_base_url.rstrip("/")
    setup_path = "/auth/im/mcp/setup"
    setup_url = f"{base_url}{setup_path}"
    login_query = urllib.parse.urlencode({"next": setup_path})
    reason = _auth_error_reason(message)
    return {
        "reason": reason,
        "authorization_required": True,
        "auth_token_argument": "auth_token",
        "setup_url": setup_url,
        "im_oauth_login_url": f"{base_url}/auth/im/oauth/login?{login_query}",
        "self_binding_page_url": f"{base_url}/auth/im/self-bindings/page",
        "token_binding_page_url": f"{base_url}/auth/im/token-bindings/page",
        "token_store_configured": bool(runtime.token_store_path),
        "static_token_configured": bool(str(runtime.mcp_token or "").strip()),
        "static_token_allowed": bool(runtime.allow_static_mcp_token),
        "remediation": _auth_error_remediation(reason),
    }


def _auth_error_reason(message: str) -> str:
    normalized = str(message or "").lower()
    if "auth_token is required" in normalized:
        return "missing_auth_token"
    if "backend is not configured" in normalized:
        return "auth_backend_not_configured"
    if "current-user token store" in normalized:
        return "current_user_token_store_required"
    return "invalid_auth_token"


def _auth_error_remediation(reason: str) -> str:
    if reason == "auth_backend_not_configured":
        return "Start diagnose-mcp-server with --token-store and have the current user complete self-service binding or IM OAuth before MCP use."
    if reason == "current_user_token_store_required":
        return "Use a bound current-user token from --token-store; shared static MCP tokens are local smoke-only and cannot be used for production MCP auth."
    return "Open setup_url to complete self-service binding or IM OAuth, then pass the issued token as auth_token."
