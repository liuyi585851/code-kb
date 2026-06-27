import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.user_auth import JsonUserTokenStore, IMOAuthClient
from codekb.im_oauth_smoke import run_im_oauth_smoke


class IMOAuthSmokeTests(unittest.TestCase):
    def test_oauth_smoke_builds_authorize_url_and_state_without_secret_leak(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )

            report = run_im_oauth_smoke(
                env=_env(),
                token_store_path=str(token_store),
                api_base_url="https://kb.example",
            )
            raw = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["authorize_url"]["host"], "im-oauth.example.com")
        self.assertTrue(report["authorize_url"]["has_state"])
        self.assertEqual(report["state"]["next"], "/auth/im/mcp/setup")
        self.assertEqual(report["token_store"]["active"], 1)
        self.assertNotIn("corp-id", raw)
        self.assertNotIn("app-secret", raw)
        self.assertNotIn("state-secret", raw)
        self.assertNotIn("ww-user", raw)

    def test_oauth_smoke_reports_missing_env(self):
        report = run_im_oauth_smoke(env={}, token_store_path="")

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "blocked_missing_env")
        self.assertIn("CODEKB_IM_CORP_ID", report["missing_env"])
        self.assertEqual(report["state"]["status"], "missing_secret")

    def test_oauth_smoke_can_check_credentials_with_fake_client(self):
        client = IMOAuthClient(
            corp_id="corp-id",
            app_secret="app-secret",
            agent_id="100001",
            get_json=lambda _url: {"errcode": 0, "access_token": "access-token"},
        )

        report = run_im_oauth_smoke(
            env=_env(),
            token_store_path="",
            api_base_url="https://kb.example",
            check_credentials=True,
            client=client,
        )
        raw = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["credentials"]["status"], "verified")
        self.assertTrue(report["credentials"]["access_token_acquired"])
        self.assertNotIn("app-secret", raw)
        self.assertNotIn("access-token", raw)

    def test_oauth_smoke_redacts_credential_check_errors(self):
        client = IMOAuthClient(
            corp_id="corp-id",
            app_secret="app-secret",
            agent_id="100001",
            get_json=lambda _url: (_ for _ in ()).throw(
                RuntimeError(
                    "failed https://im-api.example.com/cgi-bin/gettoken?"
                    "corpid=corp-id&corpsecret=app-secret&access_token=access-token"
                )
            ),
        )

        report = run_im_oauth_smoke(
            env=_env(),
            token_store_path="",
            api_base_url="https://kb.example",
            check_credentials=True,
            client=client,
        )
        raw = json.dumps(report, ensure_ascii=False)

        self.assertFalse(report["ok"])
        self.assertEqual(report["credentials"]["status"], "failed")
        self.assertNotIn("app-secret", raw)
        self.assertNotIn("access-token", raw)
        self.assertIn("[REDACTED]", report["credentials"]["error"])

    def test_cli_oauth_smoke_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store = root / "tokens.json"
            JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            env_file = root / "p5.env"
            env_file.write_text(
                "\n".join(
                    [
                        "CODEKB_IM_CORP_ID=corp-id",
                        "CODEKB_IM_AGENT_ID=100001",
                        "CODEKB_IM_APP_SECRET=app-secret",
                        "CODEKB_IM_OAUTH_STATE_SECRET=state-secret",
                        "CODEKB_IM_OAUTH_REDIRECT_URI=https://kb.example/auth/im/oauth/callback",
                        f"CODEKB_USER_TOKEN_STORE={token_store}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-im-oauth-smoke",
                        "--env-file",
                        str(env_file),
                        "--api-base-url",
                        "https://kb.example",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["token_store"]["active"], 1)
        self.assertNotIn("corp-id", stdout.getvalue())
        self.assertNotIn("app-secret", stdout.getvalue())
        self.assertNotIn("state-secret", stdout.getvalue())
        self.assertNotIn("ww-user", stdout.getvalue())


def _env() -> dict[str, str]:
    return {
        "CODEKB_IM_CORP_ID": "corp-id",
        "CODEKB_IM_AGENT_ID": "100001",
        "CODEKB_IM_APP_SECRET": "app-secret",
        "CODEKB_IM_OAUTH_STATE_SECRET": "state-secret",
        "CODEKB_IM_OAUTH_REDIRECT_URI": "https://kb.example/auth/im/oauth/callback",
    }


if __name__ == "__main__":
    unittest.main()
