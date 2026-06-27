import contextlib
import io
import json
import os
import re
import sys
import unittest
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.api import create_app
from codekb.cli import main
from codekb.current_user_demo_page import render_current_user_demo_page
from codekb.mcp_server import DiagnoseMcpRuntime, handle_mcp_request
from codekb.webhook_demo_page import render_webhook_demo_page
from codekb.user_auth import (
    JsonUserTokenStore,
    IMOAuthClient,
    IMOAuthProfile,
    issue_im_oauth_token,
    make_im_oauth_state,
    safe_relative_url,
    verify_im_oauth_state,
)


class UserAuthTests(unittest.TestCase):
    def test_im_oauth_state_and_authorize_url(self):
        state = make_im_oauth_state(
            "state-secret",
            next_url="/auth/im/confirmations/page?confirmation_id=c1",
            now=1000,
        )
        payload = verify_im_oauth_state(state, "state-secret", now=1010)
        client = IMOAuthClient(corp_id="corp-id", app_secret="app-secret", agent_id="100001")
        authorize_url = client.authorize_url(
            redirect_uri="https://kb.example/auth/im/oauth/callback",
            state=state,
        )
        parsed = urllib.parse.urlparse(authorize_url)
        query = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(payload["next"], "/auth/im/confirmations/page?confirmation_id=c1")
        self.assertEqual(parsed.netloc, "im-oauth.example.com")
        self.assertEqual(query["appid"], ["corp-id"])
        self.assertEqual(query["redirect_uri"], ["https://kb.example/auth/im/oauth/callback"])
        self.assertEqual(query["agentid"], ["100001"])
        self.assertEqual(parsed.fragment, "im_redirect")
        self.assertEqual(safe_relative_url("https://evil.example/path"), "")
        with self.assertRaises(ValueError):
            verify_im_oauth_state(state + "x", "state-secret", now=1010)
        with self.assertRaises(ValueError):
            verify_im_oauth_state(state, "state-secret", now=3000)

    def test_im_oauth_exchange_code_issues_bound_token(self):
        calls: list[str] = []

        def fake_get_json(url: str):
            calls.append(url)
            if "/gettoken?" in url:
                return {"errcode": 0, "access_token": "access-token"}
            if "/auth/getuserinfo?" in url:
                return {
                    "errcode": 0,
                    "UserId": "ww-user",
                    "DeviceId": "device-secret",
                    "user_ticket": "ticket-secret",
                }
            raise AssertionError(url)

        client = IMOAuthClient(
            corp_id="corp-id",
            app_secret="app-secret",
            agent_id="100001",
            get_json=fake_get_json,
        )
        profile = client.exchange_code("oauth-code")
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            store = JsonUserTokenStore(store_path)
            issued = issue_im_oauth_token(store, profile, scopes=["diagnose"], ttl_days=7)
            raw_store = store_path.read_text(encoding="utf-8")
            summary = store.summary()

        self.assertEqual(len(calls), 2)
        self.assertEqual(profile.user_id, "ww-user")
        self.assertEqual(len(profile.user_id_hash), 64)
        self.assertTrue(issued["token"].startswith("lkb_"))
        self.assertNotIn(issued["token"], raw_store)
        self.assertIn("ww-user", raw_store)
        self.assertNotIn("device-secret", raw_store)
        self.assertNotIn("ticket-secret", raw_store)
        self.assertNotIn("ww-user", json.dumps(summary, ensure_ascii=False))

    def test_token_store_issue_validate_and_revoke(self):
        with TemporaryDirectory() as tmp:
            store = JsonUserTokenStore(Path(tmp) / "tokens.json")
            issued = store.issue(user_id_hash="u_hash", display_name="User", scopes=["diagnose"])
            raw = (Path(tmp) / "tokens.json").read_text(encoding="utf-8")
            binding = store.validate(issued["token"])
            revoked = store.revoke(issued["binding"]["token_id"])

        self.assertTrue(issued["token"].startswith("lkb_"))
        self.assertIsNotNone(binding)
        self.assertEqual(binding.user_id_hash, "u_hash")
        self.assertEqual(revoked.revoked_at != "", True)
        self.assertNotIn(issued["token"], raw)
        self.assertIn("token_hash", raw)

    def test_cli_token_bind_list_and_revoke(self):
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "auth-token-bind",
                        "--store",
                        str(store_path),
                        "--user-id-hash",
                        "u_hash",
                        "--display-name",
                        "User",
                        "--scope",
                        "diagnose",
                        "--metadata-json",
                        '{"im_userid":"ww-user","source":"test"}',
                        "--json",
                    ]
                )
            issued = json.loads(stdout.getvalue())
            list_stdout = io.StringIO()
            with contextlib.redirect_stdout(list_stdout):
                list_code = main(["auth-token-list", "--store", str(store_path), "--json"])
            summary = json.loads(list_stdout.getvalue())
            revoke_stdout = io.StringIO()
            with contextlib.redirect_stdout(revoke_stdout):
                revoke_code = main(
                    [
                        "auth-token-revoke",
                        "--store",
                        str(store_path),
                        "--token-id",
                        issued["binding"]["token_id"],
                        "--json",
                    ]
                )
            revoked = json.loads(revoke_stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(list_code, 0)
        self.assertEqual(revoke_code, 0)
        self.assertIn("token", issued)
        self.assertNotIn("token_hash", issued["binding"])
        self.assertNotIn("im_userid", issued["binding"]["metadata"])
        self.assertIn("im_userid_hash", issued["binding"]["metadata"])
        self.assertNotIn("ww-user", json.dumps(summary, ensure_ascii=False))
        self.assertEqual(summary["active"], 1)
        self.assertTrue(revoked["revoked_at"])

    def test_mcp_validates_bound_token_store(self):
        with TemporaryDirectory() as tmp:
            store = JsonUserTokenStore(Path(tmp) / "tokens.json")
            issued = store.issue(user_id_hash="u_hash", scopes=["diagnose"])
            response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": issued["token"],
                            "source": "ci",
                            "payload": {"repo": "ym/app"},
                        },
                    },
                },
                DiagnoseMcpRuntime(token_store_path=str(Path(tmp) / "tokens.json")),
            )
            store.revoke(issued["binding"]["token_id"])
            rejected = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "codekb_diagnose_webhook_validate",
                        "arguments": {
                            "auth_token": issued["token"],
                            "source": "ci",
                            "payload": {"repo": "ym/app"},
                        },
                    },
                },
                DiagnoseMcpRuntime(token_store_path=str(Path(tmp) / "tokens.json")),
            )

        self.assertEqual(response["result"]["isError"], True)
        self.assertIn("query is required", response["result"]["content"][0]["text"])
        self.assertEqual(rejected["error"]["code"], -32000)
        self.assertIn("invalid MCP auth token", rejected["error"]["message"])

    def test_api_token_bind_summary_and_revoke(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original_store = os.environ.get("CODEKB_USER_TOKEN_STORE")
        original_admin_token = os.environ.get("CODEKB_AUTH_ADMIN_TOKEN")
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post(
                    "/auth/im/token-bindings",
                    json={"user_id_hash": "u_hash", "scopes": ["diagnose"]},
                )
                issued_response = client.post(
                    "/auth/im/token-bindings",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={
                        "user_id_hash": "u_hash",
                        "display_name": "User",
                        "scopes": ["diagnose"],
                        "ttl_days": 30,
                        "metadata": {"source": "test", "im_userid": "ww-user"},
                    },
                )
                issued = issued_response.json()
                summary = client.get(
                    "/auth/im/token-bindings/summary",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                ).json()
                revoked_response = client.post(
                    f"/auth/im/token-bindings/{issued['binding']['token_id']}/revoke",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                )
                revoked = revoked_response.json()
                raw_store = store_path.read_text(encoding="utf-8")
            finally:
                if original_store is None:
                    os.environ.pop("CODEKB_USER_TOKEN_STORE", None)
                else:
                    os.environ["CODEKB_USER_TOKEN_STORE"] = original_store
                if original_admin_token is None:
                    os.environ.pop("CODEKB_AUTH_ADMIN_TOKEN", None)
                else:
                    os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = original_admin_token

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(issued_response.status_code, 200)
        self.assertEqual(revoked_response.status_code, 200)
        self.assertNotIn("admin-token", issued_response.text)
        self.assertNotIn("ww-user", issued_response.text)
        self.assertTrue(issued["token"].startswith("lkb_"))
        self.assertNotIn("token_hash", issued["binding"])
        self.assertIn("token_hash_prefix", issued["binding"])
        self.assertNotIn("im_userid", issued["binding"]["metadata"])
        self.assertIn("im_userid_hash", issued["binding"]["metadata"])
        self.assertNotIn("ww-user", json.dumps(summary, ensure_ascii=False))
        self.assertEqual(summary["active"], 1)
        self.assertEqual(revoked["status"], "revoked")
        self.assertTrue(revoked["binding"]["revoked_at"])
        self.assertNotIn(issued["token"], raw_store)
        self.assertIn("token_hash", raw_store)

    def test_api_token_binding_page_serves_admin_fallback_tool(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/auth/im/token-bindings/page")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB Token Binding", response.text)
        self.assertIn("admin-token", response.text)
        self.assertIn("im-userid", response.text)
        self.assertIn("/auth/im/token-bindings", response.text)
        self.assertIn("codekb_user_token", response.text)

    def test_api_user_self_binding_issues_current_user_token_without_leaking_route(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env("CODEKB_USER_TOKEN_STORE", "CODEKB_USER_BINDING_CODE")
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ.pop("CODEKB_USER_BINDING_CODE", None)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                unconfigured = client.post(
                    "/auth/im/self-bindings",
                    json={
                        "binding_code": "bind-code",
                        "route_type": "im_robot",
                        "route_value": "robot-route-secret",
                    },
                )
                os.environ["CODEKB_USER_BINDING_CODE"] = "bind-code"
                denied = client.post(
                    "/auth/im/self-bindings",
                    json={
                        "binding_code": "wrong",
                        "route_type": "im_robot",
                        "route_value": "robot-route-secret",
                    },
                )
                issued_response = client.post(
                    "/auth/im/self-bindings",
                    json={
                        "binding_code": "bind-code",
                        "display_name": "User",
                        "route_type": "im_robot",
                        "route_value": "robot-route-secret",
                        "scopes": ["diagnose"],
                        "ttl_days": 30,
                    },
                )
                issued = issued_response.json()
                status = client.post(
                    "/auth/im/current-user/status",
                    json={"auth_token": issued["token"]},
                )
                raw_store = store_path.read_text(encoding="utf-8")
            finally:
                _restore_env(original)

        combined_response = issued_response.text + status.text
        metadata = issued["binding"]["metadata"]
        self.assertEqual(unconfigured.status_code, 503)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(issued_response.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertTrue(issued["token"].startswith("lkb_"))
        self.assertEqual(metadata["source"], "self_service_binding")
        self.assertEqual(metadata["route_type"], "im_robot")
        self.assertIn("im_robot_key_hash", metadata)
        self.assertIn("route_value_hash", metadata)
        self.assertNotIn("im_robot_key", metadata)
        self.assertNotIn("route_value", metadata)
        self.assertNotIn("bind-code", combined_response + raw_store)
        self.assertNotIn("robot-route-secret", combined_response)
        self.assertNotIn(issued["token"], raw_store)
        self.assertIn("robot-route-secret", raw_store)

    def test_api_user_self_binding_page_serves_user_tool(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/auth/im/self-bindings/page")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB Self Binding", response.text)
        self.assertIn("binding-code", response.text)
        self.assertIn("route-value", response.text)
        self.assertIn("/auth/im/self-bindings", response.text)
        self.assertIn("codekb_user_token", response.text)

    def test_current_user_demo_page_renders_end_to_end_flow(self):
        html = render_current_user_demo_page()

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("Code-KB Current User Demo", html)
        self.assertIn("/auth/im/current-user/status", html)
        self.assertIn("/diagnose", html)
        self.assertIn("/auth/im/confirmations/request", html)
        self.assertIn("/auth/im/confirmations/pending", html)
        self.assertIn("/auth/im/self-bindings/page", html)
        self.assertIn("/auth/im/confirmations/page", html)
        self.assertIn("codekb_user_token", html)
        self.assertIn("Diagnose and push", html)
        self.assertNotIn("CODEKB_USER_BINDING_CODE", html)

    def test_api_current_user_demo_page_serves_tool_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/demo/current-user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB Current User Demo", response.text)
        self.assertIn("/diagnose", response.text)

    def test_webhook_demo_page_renders_platform_flow(self):
        html = render_webhook_demo_page()

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("Code-KB Webhook Demo", html)
        self.assertIn("/diagnose/webhook/", html)
        self.assertIn("X-CodeKB-Token", html)
        self.assertIn("code_review", html)
        self.assertIn("issue_tracker", html)
        self.assertIn("crash", html)
        self.assertIn("confirmation_policy", html)
        self.assertIn("codekb_user_token", html)
        self.assertIn("/auth/im/confirmations/page", html)

    def test_api_webhook_demo_page_serves_tool_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/demo/webhook")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB Webhook Demo", response.text)
        self.assertIn("/diagnose/webhook/", response.text)

    def test_api_token_management_requires_configured_admin_token(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original_store = os.environ.get("CODEKB_USER_TOKEN_STORE")
        original_admin_token = os.environ.get("CODEKB_AUTH_ADMIN_TOKEN")
        with TemporaryDirectory() as tmp:
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(Path(tmp) / "tokens.json")
            os.environ.pop("CODEKB_AUTH_ADMIN_TOKEN", None)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                issued = client.post(
                    "/auth/im/token-bindings",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"user_id_hash": "u_hash", "scopes": ["diagnose"]},
                )
                summary = client.get(
                    "/auth/im/token-bindings/summary",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                )
            finally:
                if original_store is None:
                    os.environ.pop("CODEKB_USER_TOKEN_STORE", None)
                else:
                    os.environ["CODEKB_USER_TOKEN_STORE"] = original_store
                if original_admin_token is None:
                    os.environ.pop("CODEKB_AUTH_ADMIN_TOKEN", None)
                else:
                    os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = original_admin_token

        self.assertEqual(issued.status_code, 401)
        self.assertEqual(summary.status_code, 401)
        self.assertIn("auth admin token is not configured", issued.text)
        self.assertIn("auth admin token is not configured", summary.text)

    def test_api_token_binding_derives_user_hash_from_im_userid(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original_store = os.environ.get("CODEKB_USER_TOKEN_STORE")
        original_admin_token = os.environ.get("CODEKB_AUTH_ADMIN_TOKEN")
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.post(
                    "/auth/im/token-bindings",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={
                        "display_name": "User",
                        "scopes": ["diagnose"],
                        "metadata": {"im_userid": "ww-user"},
                    },
                )
            finally:
                if original_store is None:
                    os.environ.pop("CODEKB_USER_TOKEN_STORE", None)
                else:
                    os.environ["CODEKB_USER_TOKEN_STORE"] = original_store
                if original_admin_token is None:
                    os.environ.pop("CODEKB_AUTH_ADMIN_TOKEN", None)
                else:
                    os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = original_admin_token

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["token"].startswith("lkb_"))
        self.assertEqual(len(payload["binding"]["user_id_hash"]), 64)
        self.assertNotIn("ww-user", response.text)
        self.assertIn("im_userid_hash", payload["binding"]["metadata"])

    def test_api_im_oauth_login_redirects_to_authorize_url(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env(
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "CODEKB_IM_OAUTH_REDIRECT_URI",
        )
        try:
            os.environ["CODEKB_IM_CORP_ID"] = "corp-id"
            os.environ["CODEKB_IM_AGENT_ID"] = "100001"
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret"
            os.environ["CODEKB_IM_OAUTH_STATE_SECRET"] = "state-secret"
            os.environ["CODEKB_IM_OAUTH_REDIRECT_URI"] = "https://kb.example/auth/im/oauth/callback"
            client = TestClient(
                create_app(
                    fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                )
            )
            response = client.get(
                "/auth/im/oauth/login?next=/auth/im/confirmations/page%3Fconfirmation_id%3Dc1",
                follow_redirects=False,
            )
        finally:
            _restore_env(original)

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        parsed = urllib.parse.urlparse(location)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "im-oauth.example.com")
        self.assertEqual(query["appid"], ["corp-id"])
        self.assertEqual(query["agentid"], ["100001"])
        self.assertEqual(query["redirect_uri"], ["https://kb.example/auth/im/oauth/callback"])
        self.assertIn("state", query)

    def test_api_im_oauth_login_uses_public_base_when_redirect_not_configured(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env(
            "CODEKB_API_BASE_URL",
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "CODEKB_IM_OAUTH_REDIRECT_URI",
        )
        try:
            os.environ["CODEKB_API_BASE_URL"] = "https://kb.example"
            os.environ["CODEKB_IM_CORP_ID"] = "corp-id"
            os.environ["CODEKB_IM_AGENT_ID"] = "100001"
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret"
            os.environ["CODEKB_IM_OAUTH_STATE_SECRET"] = "state-secret"
            os.environ.pop("CODEKB_IM_OAUTH_REDIRECT_URI", None)
            client = TestClient(
                create_app(
                    fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                )
            )
            response = client.get("/auth/im/oauth/login", follow_redirects=False)
        finally:
            _restore_env(original)

        self.assertEqual(response.status_code, 307)
        location = response.headers["location"]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        self.assertEqual(query["redirect_uri"], ["https://kb.example/auth/im/oauth/callback"])

    def test_api_im_oauth_callback_issues_current_user_token(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env(
            "CODEKB_USER_TOKEN_STORE",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "CODEKB_IM_OAUTH_TOKEN_TTL_DAYS",
        )
        state = make_im_oauth_state(
            "state-secret",
            next_url="/auth/im/confirmations/page?confirmation_id=c1",
        )
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ["CODEKB_IM_OAUTH_STATE_SECRET"] = "state-secret"
            os.environ["CODEKB_IM_OAUTH_TOKEN_TTL_DAYS"] = "7"
            fake_profile = IMOAuthProfile(
                user_id="ww-user",
                open_id="",
                device_id="device-secret",
                user_ticket="ticket-secret",
                raw={"UserId": "ww-user"},
            )

            class FakeOAuthClient:
                def exchange_code(self, code: str):
                    self.code = code
                    return fake_profile

            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                with patch("codekb.api._build_im_oauth_client", return_value=FakeOAuthClient()):
                    response = client.get(f"/auth/im/oauth/callback?code=oauth-code&state={state}")
                raw_store = store_path.read_text(encoding="utf-8")
                summary = JsonUserTokenStore(store_path).summary()
            finally:
                _restore_env(original)

        token_match = re.search(r"lkb_[A-Za-z0-9_-]+", response.text)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(token_match)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('localStorage.setItem("codekb_user_token"', response.text)
        self.assertIn("/auth/im/confirmations/page?confirmation_id=c1", response.text)
        self.assertNotIn(token_match.group(0), raw_store)
        self.assertIn("ww-user", raw_store)
        self.assertNotIn("device-secret", raw_store)
        self.assertNotIn("ticket-secret", raw_store)
        self.assertNotIn("ww-user", json.dumps(summary, ensure_ascii=False))

    def test_api_mcp_setup_page_serves_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]

        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )
        response = client.get("/auth/im/mcp/setup")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("/auth/im/oauth/login?next=", response.text)
        self.assertIn("/auth/im/current-user/status", response.text)
        self.assertIn("/auth/im/current-user/smoke", response.text)
        self.assertIn("/auth/im/mcp/setup/status", response.text)
        self.assertIn("/diagnose/external-inputs/page", response.text)
        self.assertIn("/diagnose/external-inputs.md", response.text)
        self.assertIn("/diagnose/final-verification/page", response.text)
        self.assertIn("/auth/im/self-bindings/page", response.text)
        self.assertIn("/auth/im/token-bindings/page", response.text)
        self.assertIn("/auth/im/configure/page", response.text)
        self.assertIn("/demo/current-user", response.text)
        self.assertIn("/demo/webhook", response.text)
        self.assertIn("/auth/im/confirmations/page", response.text)
        self.assertIn("confirmations", response.text)
        self.assertIn("oauth-callback", response.text)
        self.assertIn("codekb_user_token", response.text)

    def test_api_mcp_setup_status_reports_oauth_and_token_store_state(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env(
            "CODEKB_API_BASE_URL",
            "CODEKB_USER_TOKEN_STORE",
            "CODEKB_IM_CORP_ID",
            "CODEKB_IM_AGENT_ID",
            "CODEKB_IM_APP_SECRET",
            "CODEKB_IM_OAUTH_STATE_SECRET",
            "CODEKB_IM_OAUTH_REDIRECT_URI",
            "CODEKB_USER_BINDING_CODE",
        )
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            issued = JsonUserTokenStore(store_path).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            os.environ["CODEKB_API_BASE_URL"] = "http://kb.example"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ.pop("CODEKB_IM_CORP_ID", None)
            os.environ.pop("CODEKB_IM_AGENT_ID", None)
            os.environ.pop("CODEKB_IM_APP_SECRET", None)
            os.environ["CODEKB_IM_OAUTH_STATE_SECRET"] = "state-secret"
            os.environ["CODEKB_USER_BINDING_CODE"] = "bind-code"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/auth/im/mcp/setup/status")
            finally:
                _restore_env(original)

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["api_base_url"], "http://kb.example")
        self.assertEqual(payload["confirmations_url"], "http://kb.example/auth/im/confirmations/page")
        self.assertEqual(payload["web_push_inbox_url"], "http://kb.example/auth/im/confirmations/page")
        self.assertEqual(payload["current_user_demo_url"], "http://kb.example/demo/current-user")
        self.assertEqual(payload["webhook_demo_url"], "http://kb.example/demo/webhook")
        self.assertEqual(payload["current_user_smoke_url"], "http://kb.example/auth/im/current-user/smoke")
        self.assertEqual(payload["external_inputs_markdown_url"], "http://kb.example/diagnose/external-inputs.md")
        self.assertEqual(payload["external_inputs_page_url"], "http://kb.example/diagnose/external-inputs/page")
        self.assertEqual(payload["final_verification_url"], "http://kb.example/diagnose/final-verification")
        self.assertEqual(payload["final_verification_page_url"], "http://kb.example/diagnose/final-verification/page")
        self.assertEqual(payload["self_binding_page_url"], "http://kb.example/auth/im/self-bindings/page")
        self.assertEqual(payload["token_binding_page_url"], "http://kb.example/auth/im/token-bindings/page")
        self.assertEqual(payload["im_configure_url"], "http://kb.example/auth/im/configure")
        self.assertEqual(payload["im_configure_page_url"], "http://kb.example/auth/im/configure/page")
        self.assertTrue(payload["mcp_auth_strategy"]["current_user_auth_required"])
        self.assertEqual(payload["mcp_auth_strategy"]["confirmation_target"], "current_authenticated_user")
        self.assertFalse(payload["mcp_auth_strategy"]["interface_person_lookup_enabled"])
        self.assertEqual(payload["mcp_auth_strategy"]["token_binding"], "self_service_binding_or_im_oauth")
        self.assertTrue(payload["self_binding"]["configured"])
        self.assertIn("im_robot", payload["self_binding"]["route_types"])
        self.assertEqual(payload["oauth"]["callback_url"], "http://kb.example/auth/im/oauth/callback")
        self.assertIn("CODEKB_IM_CORP_ID", payload["oauth"]["missing_env"])
        self.assertEqual(payload["mcp"]["active_token_bindings"], 1)
        self.assertNotIn("token_store_path", payload["mcp"])
        self.assertFalse(payload["secret_values_written"])
        self.assertNotIn(issued["token"], raw_response)
        self.assertNotIn(str(store_path), raw_response)
        self.assertNotIn("state-secret", raw_response)
        self.assertNotIn("bind-code", raw_response)
        self.assertNotIn("ww-user", raw_response)

    def test_api_current_user_status_validates_token_without_leaking(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env("CODEKB_USER_TOKEN_STORE", "CODEKB_API_BASE_URL")
        with TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "tokens.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ["CODEKB_API_BASE_URL"] = "http://kb.example"
            issued = JsonUserTokenStore(store_path).issue(
                user_id_hash="u_hash",
                display_name="User",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user", "source": "test"},
            )
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post("/auth/im/current-user/status", json={"auth_token": "bad-token"})
                response = client.post(
                    "/auth/im/current-user/status",
                    json={"auth_token": issued["token"]},
                )
            finally:
                _restore_env(original)

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["binding"]["token_id"], issued["binding"]["token_id"])
        self.assertEqual(payload["mcp"]["api_base_url"], "http://kb.example")
        self.assertEqual(payload["mcp"]["auth_token_argument"], "auth_token")
        self.assertNotIn(issued["token"], raw_response)
        self.assertNotIn('"token_hash"', raw_response)
        self.assertNotIn("ww-user", raw_response)
        self.assertIn("im_userid_hash", payload["binding"]["metadata"])

    def test_api_current_user_smoke_validates_route_and_creates_confirmation_without_leaking(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = _capture_env(
            "CODEKB_USER_TOKEN_STORE",
            "CODEKB_USER_CONFIRMATION_OUTBOX",
            "CODEKB_USER_CONFIRMATION_RESPONSES",
            "CODEKB_USER_CONFIRMATION_REPORT",
            "CODEKB_USER_CONFIRMATION_DELIVERY_LOG",
            "CODEKB_FEEDBACK_LOG",
            "CODEKB_CANDIDATE_STORE",
            "CODEKB_PENDING_DOCS_DIR",
            "CODEKB_TRACE_LOG",
            "CODEKB_API_BASE_URL",
        )
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store_path = tmp_path / "tokens.json"
            outbox_path = tmp_path / "outbox.jsonl"
            report_path = tmp_path / "delivery-report.json"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(store_path)
            os.environ["CODEKB_USER_CONFIRMATION_OUTBOX"] = str(outbox_path)
            os.environ["CODEKB_USER_CONFIRMATION_RESPONSES"] = str(tmp_path / "responses.jsonl")
            os.environ["CODEKB_USER_CONFIRMATION_REPORT"] = str(report_path)
            os.environ["CODEKB_USER_CONFIRMATION_DELIVERY_LOG"] = str(tmp_path / "delivery.jsonl")
            os.environ["CODEKB_FEEDBACK_LOG"] = str(tmp_path / "feedback.jsonl")
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_TRACE_LOG"] = str(tmp_path / "trace.jsonl")
            os.environ["CODEKB_API_BASE_URL"] = "http://kb.example"
            issued = JsonUserTokenStore(store_path).issue(
                user_id_hash="u_hash",
                display_name="User",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user", "source": "test"},
            )
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post("/auth/im/current-user/smoke", json={"auth_token": "bad-token"})
                response = client.post(
                    "/auth/im/current-user/smoke",
                    json={"auth_token": issued["token"], "respond": False},
                )
                pending = client.post(
                    "/auth/im/confirmations/pending",
                    json={"auth_token": issued["token"]},
                )
                raw_files = outbox_path.read_text(encoding="utf-8") + report_path.read_text(encoding="utf-8")
            finally:
                _restore_env(original)

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "validated")
        self.assertTrue(payload["confirmation"]["confirmation_id"])
        self.assertEqual(payload["delivery"]["result"]["status"], "validated")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["total"], 1)
        self.assertNotIn(issued["token"], raw_response + raw_files)
        self.assertNotIn("ww-user", raw_response + raw_files)
        self.assertIn("im_userid_hash", payload["auth"]["metadata"])

    def test_api_confirmation_page_serves_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]

        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )
        response = client.get('/auth/im/confirmations/page?confirmation_id=confirm-1%22%3E%3Cscript%3E')

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB Web Push Inbox", response.text)
        self.assertIn("auto-refresh", response.text)
        self.assertIn("confirm-1&quot;&gt;&lt;script&gt;", response.text)
        self.assertNotIn('confirm-1"><script>', response.text)


def _capture_env(*keys: str) -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in keys}


def _restore_env(values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
