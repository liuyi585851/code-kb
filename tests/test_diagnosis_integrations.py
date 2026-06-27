import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.diagnosis_integrations import (
    diagnose_integration_artifacts,
    diagnose_mcp_tool_definitions,
    export_diagnose_integration_pack,
    mr_candidate_card_template,
    render_current_user_auth_guide,
    render_external_handoff_checklist,
)
from codekb.mcp_server import DiagnoseMcpRuntime, handle_mcp_request
from codekb.user_auth import JsonUserTokenStore


ROOT = Path(__file__).resolve().parents[1]


class DiagnosisIntegrationTests(unittest.TestCase):
    def test_integration_pack_exports_expected_files(self):
        with TemporaryDirectory() as tmp:
            summary = export_diagnose_integration_pack(tmp, api_base_url="http://kb.example")
            output = Path(tmp)
            tools = json.loads((output / "mcp_tools.json").read_text(encoding="utf-8"))
            card = json.loads((output / "mr_candidate_card.json").read_text(encoding="utf-8"))
            skill = (output / "code_review_skill.md").read_text(encoding="utf-8")
            im_entry = (output / "im_entry.md").read_text(encoding="utf-8")
            current_user_auth = (output / "current_user_auth.md").read_text(encoding="utf-8")
            external_handoff = (output / "external_handoff.md").read_text(encoding="utf-8")

        self.assertEqual(summary["mcp_tools"], 4)
        self.assertIn("mcp_tools.json", summary["files"])
        self.assertIn("current_user_auth.md", summary["files"])
        self.assertIn("external_handoff.md", summary["files"])
        self.assertEqual(tools[0]["name"], "codekb_diagnose")
        self.assertEqual(card["card_type"], "codekb_diagnose_candidate")
        self.assertEqual(summary["mr_card_actions"], 4)
        self.assertIn("issue_tracker", tools[1]["inputSchema"]["properties"]["source"]["enum"])
        self.assertIn("crash", tools[1]["inputSchema"]["properties"]["source"]["enum"])
        self.assertIn("confirmation_policy", tools[0]["inputSchema"]["properties"])
        self.assertIn("http://kb.example/diagnose/webhook/{source}/validate", skill)
        self.assertIn("http://kb.example/diagnose/webhook/sample-suite", skill)
        self.assertIn("X-CodeKB-Token", skill)
        self.assertIn("POST /diagnose/webhook/{source}", skill)
        self.assertIn("current user's `auth_token`", skill)
        self.assertIn("every production MCP tool call", skill)
        self.assertIn("do not infer an interface person from repository, owner, or payload fields", skill)
        self.assertIn("Do not route P5 confirmations by owner/interface-person lookup", skill)
        self.assertIn("issue_tracker", skill)
        self.assertIn("crash", skill)
        self.assertIn("auth_token", skill)
        self.assertIn("/auth/im/mcp/setup", skill)
        self.assertIn("/auth/im/self-bindings/page", skill)
        self.assertIn("/auth/im/mcp/setup", im_entry)
        self.assertIn("/auth/im/self-bindings/page", im_entry)
        self.assertIn("codekb-confirmation-worker", im_entry)
        self.assertIn("auth_token", im_entry)
        self.assertIn("X-CodeKB-Token", im_entry)
        self.assertIn("confirmation_policy=needs_review|always", im_entry)
        self.assertIn("/auth/im/current-user/status", current_user_auth)
        self.assertIn("/auth/im/current-user/smoke", current_user_auth)
        self.assertIn("/auth/im/confirmations/request", current_user_auth)
        self.assertIn("/auth/im/self-bindings/page", current_user_auth)
        self.assertIn("/auth/im/token-bindings/page", current_user_auth)
        self.assertIn("POST http://kb.example/diagnose", current_user_auth)
        self.assertIn("POST http://kb.example/diagnose/webhook/{source}", current_user_auth)
        self.assertIn("X-CodeKB-Token", current_user_auth)
        self.assertIn("/auth/im/current-user/smoke", im_entry)
        self.assertIn("/auth/im/confirmations/request", im_entry)
        self.assertIn("CODEKB_USER_CONFIRMATION_DELIVERY_LOG", current_user_auth)
        self.assertIn("CODEKB_USER_BINDING_CODE", current_user_auth)
        self.assertIn("admin-only fallback", current_user_auth)
        self.assertIn("--allow-static-mcp-token", current_user_auth)
        self.assertIn("error.data.setup_url", current_user_auth)
        self.assertIn("error.data.self_binding_page_url", current_user_auth)
        self.assertIn("error.data.token_binding_page_url", current_user_auth)
        self.assertIn("CODEKB_IM_CORP_ID", external_handoff)
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", external_handoff)
        self.assertIn("/diagnose/webhook/{source}/sample-import", external_handoff)
        self.assertIn("/auth/im/current-user/smoke", external_handoff)
        self.assertIn("/diagnose/acceptance", external_handoff)
        self.assertIn("/diagnose/external-state", external_handoff)
        self.assertIn("/diagnose/external-inputs", external_handoff)
        self.assertIn("/diagnose/external-inputs/page", external_handoff)
        self.assertIn("/auth/im/self-bindings/page", external_handoff)
        self.assertIn("/auth/im/token-bindings/page", external_handoff)
        self.assertIn("diagnose-p5-external-state", external_handoff)
        self.assertIn("diagnose-p5-final-verify", external_handoff)
        self.assertIn("diagnose-external-inputs", external_handoff)
        self.assertIn("--allow-static-mcp-token", external_handoff)
        self.assertIn("diagnose-acceptance", external_handoff)
        self.assertIn("current user's `auth_token`", external_handoff)
        self.assertIn("Confirmation target is always the current authenticated user bound to `auth_token`", external_handoff)
        self.assertIn("interface-person fields are not used for P5 routing", external_handoff)
        self.assertIn("Webhook audit events intentionally exclude `auth_token`", external_handoff)
        self.assertIn("confirmation_policy=needs_review", current_user_auth)
        self.assertIn("confirmation_policy=needs_review", skill)

    def test_cli_diagnose_integration_export_outputs_summary_json(self):
        with TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-integration-export",
                        "--output-dir",
                        tmp,
                        "--api-base-url",
                        "http://kb.example",
                        "--json",
                    ]
                )
            summary = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(summary["api_base_url"], "http://kb.example")
        self.assertEqual(summary["mcp_tools"], 4)
        self.assertIn("current_user_auth.md", summary["files"])
        self.assertIn("external_handoff.md", summary["files"])

    def test_diagnose_integration_artifacts_returns_http_ready_payload(self):
        artifacts = diagnose_integration_artifacts(api_base_url="http://kb.example/")

        self.assertEqual(artifacts["status"], "ok")
        self.assertEqual(artifacts["api_base_url"], "http://kb.example")
        self.assertEqual(artifacts["mcp_tools"], 4)
        self.assertIn("current_user_auth.md", artifacts["files"])
        self.assertIn("external_handoff.md", artifacts["files"])
        self.assertIn("mcp_tools.json", artifacts["artifacts"])
        self.assertIn("/auth/im/mcp/setup", artifacts["artifacts"]["current_user_auth.md"])
        self.assertIn("/auth/im/current-user/smoke", artifacts["artifacts"]["current_user_auth.md"])
        self.assertIn("Final Verification", artifacts["artifacts"]["external_handoff.md"])

    def test_current_user_auth_guide_points_to_setup_and_worker(self):
        guide = render_current_user_auth_guide(api_base_url="http://kb.example/")

        self.assertIn("http://kb.example/auth/im/mcp/setup", guide)
        self.assertIn("POST http://kb.example/auth/im/current-user/status", guide)
        self.assertIn("POST http://kb.example/auth/im/current-user/smoke", guide)
        self.assertIn("POST http://kb.example/auth/im/confirmations/request", guide)
        self.assertIn("http://kb.example/auth/im/token-bindings/page", guide)
        self.assertIn("POST http://kb.example/diagnose", guide)
        self.assertIn("POST http://kb.example/diagnose/webhook/{source}", guide)
        self.assertIn("X-CodeKB-Token", guide)
        self.assertIn("deploy/codekb-confirmation-worker start", guide)
        self.assertIn("CODEKB_ENABLE_IM_SEND=1", guide)
        self.assertIn("admin-only fallback", guide)
        self.assertIn("--allow-static-mcp-token", guide)
        self.assertIn("error.data.setup_url", guide)
        self.assertIn("error.data.token_binding_page_url", guide)

    def test_external_handoff_checklist_lists_user_supplied_inputs(self):
        guide = render_external_handoff_checklist(api_base_url="http://kb.example/")

        self.assertIn("http://kb.example/auth/im/oauth/callback", guide)
        self.assertIn("CODEKB_IM_APP_SECRET", guide)
        self.assertIn("X-CodeKB-Admin-Token", guide)
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", guide)
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", guide)
        self.assertIn("diagnose-webhook-sample-activate", guide)
        self.assertIn("diagnose-im-oauth-smoke", guide)
        self.assertIn("diagnose-im-configure", guide)
        self.assertIn("http://kb.example/auth/im/current-user/smoke", guide)
        self.assertIn("--allow-static-mcp-token", guide)
        self.assertIn("http://kb.example/auth/im/confirmations/request", guide)
        self.assertIn("http://kb.example/auth/im/token-bindings/page", guide)
        self.assertIn("confirmation_policy=needs_review|always", guide)
        self.assertIn("Confirmation target is always the current authenticated user bound to `auth_token`", guide)
        self.assertIn("interface-person fields are not used for P5 routing", guide)
        self.assertIn("Webhook audit events intentionally exclude `auth_token`", guide)
        self.assertIn("diagnose-external-inputs", guide)
        self.assertIn("diagnose-im-smoke", guide)
        self.assertIn("diagnose-acceptance", guide)
        self.assertIn("GET /diagnose/readiness", guide)
        self.assertIn("GET /diagnose/external-state", guide)
        self.assertIn("diagnose-p5-external-state", guide)
        self.assertIn("diagnose-p5-final-verify", guide)

    def test_mcp_tool_definitions_include_http_targets(self):
        tools = diagnose_mcp_tool_definitions(api_base_url="http://kb.example/")
        card = mr_candidate_card_template(api_base_url="http://kb.example/")

        self.assertEqual(tools[1]["http"]["url"], "http://kb.example/diagnose/webhook/{source}/validate")
        self.assertEqual(card["actions"][1]["url"], "http://kb.example/diagnose/webhook/sample-suite")
        self.assertEqual(card["actions"][2]["url"], "http://kb.example/diagnose/webhook/{source}")

    def test_mcp_tool_definitions_require_auth_token(self):
        tools = diagnose_mcp_tool_definitions(api_base_url="http://kb.example/")

        for tool in tools:
            self.assertIn("auth_token", tool["inputSchema"]["required"])

    def test_mcp_tools_list(self):
        response = handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            DiagnoseMcpRuntime(api_base_url="http://kb.example"),
        )

        self.assertEqual(response["id"], 1)
        tool_names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("codekb_diagnose", tool_names)
        self.assertIn("codekb_diagnose_webhook_validate", tool_names)
        self.assertIn("codekb_request_user_confirmation", tool_names)

    def test_mcp_validate_tool_returns_json_content(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            issued = JsonUserTokenStore(token_store).issue(user_id_hash="u_hash", scopes=["diagnose"])
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": issued["token"],
                            "source": "code_review",
                            "payload": {
                                "repository": {"path": "ym/app"},
                                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=abc123"},
                                "sub_kbs": ["testing"],
                            },
                        },
                    },
                },
                DiagnoseMcpRuntime(
                    mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                    token_store_path=str(token_store),
                ),
            )
            payload = json.loads(response["result"]["content"][0]["text"])

        self.assertFalse(response["result"]["isError"])
        self.assertTrue(payload["valid"])
        self.assertTrue(payload["query_ready"])
        self.assertNotIn("abc123", response["result"]["content"][0]["text"])

    def test_mcp_tool_auth_rejects_wrong_token(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            JsonUserTokenStore(token_store).issue(user_id_hash="u_hash", scopes=["diagnose"])
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": "wrong",
                            "source": "ci",
                            "payload": {"repo": "ym/app"},
                        },
                    },
                },
                DiagnoseMcpRuntime(token_store_path=str(token_store), api_base_url="http://kb.example/"),
            )

        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("invalid MCP auth token", response["error"]["message"])
        self.assertEqual(response["error"]["data"]["reason"], "invalid_auth_token")
        self.assertEqual(response["error"]["data"]["setup_url"], "http://kb.example/auth/im/mcp/setup")
        self.assertEqual(
            response["error"]["data"]["self_binding_page_url"],
            "http://kb.example/auth/im/self-bindings/page",
        )
        self.assertEqual(
            response["error"]["data"]["token_binding_page_url"],
            "http://kb.example/auth/im/token-bindings/page",
        )
        self.assertEqual(response["error"]["data"]["auth_token_argument"], "auth_token")
        self.assertNotIn("wrong", json.dumps(response, ensure_ascii=False))

    def test_mcp_tool_auth_rejects_missing_token(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "source": "ci",
                            "payload": {"repo": "ym/app"},
                        },
                    },
                },
                DiagnoseMcpRuntime(token_store_path=str(token_store), api_base_url="http://kb.example/"),
            )

        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("MCP auth_token is required", response["error"]["message"])
        self.assertEqual(response["error"]["data"]["reason"], "missing_auth_token")
        self.assertEqual(response["error"]["data"]["setup_url"], "http://kb.example/auth/im/mcp/setup")
        self.assertEqual(
            response["error"]["data"]["im_oauth_login_url"],
            "http://kb.example/auth/im/oauth/login?next=%2Fauth%2Fim%2Fmcp%2Fsetup",
        )
        self.assertEqual(
            response["error"]["data"]["self_binding_page_url"],
            "http://kb.example/auth/im/self-bindings/page",
        )
        self.assertEqual(
            response["error"]["data"]["token_binding_page_url"],
            "http://kb.example/auth/im/token-bindings/page",
        )

    def test_mcp_tool_auth_rejects_missing_backend(self):
        response = handle_mcp_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "codekb_diagnose_webhook_validate",
                    "arguments": {
                        "auth_token": "current-user-token",
                        "source": "ci",
                        "payload": {"repo": "ym/app"},
                    },
                },
            },
            DiagnoseMcpRuntime(api_base_url="http://kb.example/"),
        )

        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("MCP auth backend is not configured", response["error"]["message"])
        self.assertEqual(response["error"]["data"]["reason"], "auth_backend_not_configured")
        self.assertFalse(response["error"]["data"]["token_store_configured"])
        self.assertFalse(response["error"]["data"]["static_token_configured"])
        self.assertIn("--token-store", response["error"]["data"]["remediation"])

    def test_mcp_static_token_requires_explicit_local_compat(self):
        request = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "codekb_diagnose_webhook_validate",
                "arguments": {
                    "auth_token": "expected",
                    "source": "code_review",
                    "payload": {
                        "repository": {"path": "ym/app"},
                        "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
                        "sub_kbs": ["testing"],
                    },
                },
            },
        }
        default_response = handle_mcp_request(
            request,
            DiagnoseMcpRuntime(
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                mcp_token="expected",
                api_base_url="http://kb.example/",
            ),
        )
        local_response = handle_mcp_request(
            request,
            DiagnoseMcpRuntime(
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                mcp_token="expected",
                allow_static_mcp_token=True,
                api_base_url="http://kb.example/",
            ),
        )

        self.assertEqual(default_response["error"]["code"], -32000)
        self.assertIn("current-user token store is required", default_response["error"]["message"])
        self.assertEqual(default_response["error"]["data"]["reason"], "current_user_token_store_required")
        self.assertFalse(default_response["error"]["data"]["static_token_allowed"])
        self.assertFalse(local_response["result"]["isError"])

    def test_mcp_token_store_overrides_static_token(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            runtime = DiagnoseMcpRuntime(
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                mcp_token="static-token",
                token_store_path=str(token_store),
            )
            static_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": "static-token",
                            "source": "code_review",
                            "payload": {
                                "repository": {"path": "ym/app"},
                                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
                                "sub_kbs": ["testing"],
                            },
                        },
                    },
                },
                runtime,
            )
            bound_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": issued["token"],
                            "source": "code_review",
                            "payload": {
                                "repository": {"path": "ym/app"},
                                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
                                "sub_kbs": ["testing"],
                            },
                        },
                    },
                },
                runtime,
            )

        self.assertEqual(static_response["error"]["code"], -32000)
        self.assertIn("invalid MCP auth token", static_response["error"]["message"])
        self.assertFalse(bound_response["result"]["isError"])

    def test_mcp_confirmation_tool_writes_current_user_outbox(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            outbox = Path(tmp) / "user-confirmation.jsonl"
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            legacy_route = JsonUserTokenStore(token_store).issue(
                user_id_hash="legacy_interface_hash",
                display_name="Legacy Interface",
                scopes=["diagnose"],
                metadata={"im_userid": "legacy-interface-user"},
            )
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_request_user_confirmation",
                        "arguments": {
                            "auth_token": issued["token"],
                            "reason": "human_review_required",
                            "message": "确认是否提交 KB 缺口候选",
                            "payload": {
                                "diagnosis_id": "diag-1",
                                "owner": "legacy-owner",
                                "interface_person": "legacy-interface-user",
                            },
                        },
                    },
                },
                DiagnoseMcpRuntime(token_store_path=str(token_store), confirmation_outbox_path=str(outbox)),
            )
            raw = outbox.read_text(encoding="utf-8")
            payload = json.loads(response["result"]["content"][0]["text"])
            stored = json.loads(raw)
            current_prefix = hashlib.sha256(issued["token"].encode("utf-8")).hexdigest()[:12]
            legacy_prefix = hashlib.sha256(legacy_route["token"].encode("utf-8")).hexdigest()[:12]

        self.assertFalse(response["result"]["isError"])
        self.assertEqual(payload["reason"], "human_review_required")
        self.assertEqual(payload["payload"]["diagnosis_id"], "diag-1")
        self.assertEqual(payload["payload"]["interface_person"], "legacy-interface-user")
        self.assertEqual(payload["target_user_token_hash_prefix"], current_prefix)
        self.assertNotEqual(payload["target_user_token_hash_prefix"], legacy_prefix)
        self.assertEqual(len(payload["target_user_token_hash_prefix"]), 12)
        self.assertNotIn("target_user_token_hash", payload)
        self.assertNotIn(issued["token"], raw)
        self.assertNotIn(legacy_route["token"], raw)
        self.assertEqual(len(stored["target_user_token_hash"]), 64)

    def test_mcp_confirmation_rejects_static_token_without_current_user_store(self):
        with TemporaryDirectory() as tmp:
            outbox = Path(tmp) / "user-confirmation.jsonl"
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_request_user_confirmation",
                        "arguments": {
                            "auth_token": "static-token",
                            "reason": "human_review_required",
                            "message": "确认是否提交 KB 缺口候选",
                        },
                    },
                },
                DiagnoseMcpRuntime(mcp_token="static-token", confirmation_outbox_path=str(outbox)),
            )

        self.assertFalse(outbox.exists())
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("current-user token store is required", response["error"]["message"])



    def test_mcp_code_nav_tools_require_auth(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            JsonUserTokenStore(token_store).issue(user_id_hash="u_hash", scopes=["diagnose"])
            runtime = DiagnoseMcpRuntime(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
                token_store_path=str(token_store),
            )
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "codekb_search_code", "arguments": {"query": "AuthSDK"}},
                },
                runtime,
            )
        self.assertIn("error", response)  # 缺少 auth_token,会像其他 MCP 工具一样被拒绝

    def test_code_nav_tools_listed(self):
        response = handle_mcp_request(
            {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
            DiagnoseMcpRuntime(),
        )
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertTrue({"codekb_search_code", "codekb_read_file_range", "codekb_file_outline"} <= names)

    def test_mcp_diagnose_auto_confirmation_requires_current_user_store(self):
        with TemporaryDirectory() as tmp:
            outbox = Path(tmp) / "user-confirmation.jsonl"
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose",
                        "arguments": {
                            "auth_token": "static-token",
                            "query": "DEVICE_SEQ 是什么？",
                            "confirmation_policy": "always",
                            "include_governance": False,
                        },
                    },
                },
                DiagnoseMcpRuntime(
                    fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                    mcp_token="static-token",
                    confirmation_outbox_path=str(outbox),
                ),
            )

        self.assertFalse(outbox.exists())
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("current-user token store is required", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
