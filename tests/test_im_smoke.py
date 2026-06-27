import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.user_auth import JsonUserTokenStore
from codekb.im_smoke import run_im_delivery_smoke


class IMSmokeTests(unittest.TestCase):
    def test_im_smoke_credentials_only_uses_safe_output(self):
        client = _FakeIMClient()

        report = run_im_delivery_smoke(
            env=_env(),
            check_credentials=True,
            client=client,
        )
        raw = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "credentials_verified")
        self.assertEqual(report["credentials"]["status"], "verified")
        self.assertTrue(report["credentials"]["access_token_acquired"])
        self.assertNotIn("corp-id", raw)
        self.assertNotIn("app-secret", raw)
        self.assertNotIn("fake-access-token", raw)

    def test_im_smoke_dry_run_validates_current_user_route(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root)

            report = run_im_delivery_smoke(
                env=_env(),
                auth_token=issued["token"],
                token_store_path=str(token_store),
                confirmation_outbox_path=str(root / "outbox.jsonl"),
                delivery_report_path=str(root / "report.json"),
                delivery_log_path=str(root / "delivery.jsonl"),
                client=_FakeIMClient(),
            )
            raw = json.dumps(report, ensure_ascii=False) + (root / "outbox.jsonl").read_text(encoding="utf-8")

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "validated")
        self.assertEqual(report["delivery"]["result"]["status"], "validated")
        self.assertEqual(report["delivery"]["result"]["detail"], "dry_run")
        self.assertNotIn(issued["token"], raw)
        self.assertNotIn("ww-user", raw)

    def test_im_smoke_execute_sends_with_fake_client_when_enabled(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root)
            client = _FakeIMClient()

            report = run_im_delivery_smoke(
                env={**_env(), "CODEKB_ENABLE_IM_SEND": "1"},
                auth_token=issued["token"],
                token_store_path=str(token_store),
                confirmation_outbox_path=str(root / "outbox.jsonl"),
                delivery_report_path=str(root / "report.json"),
                delivery_log_path=str(root / "delivery.jsonl"),
                execute=True,
                write_enabled=True,
                client=client,
            )
            raw = json.dumps(report, ensure_ascii=False) + (root / "report.json").read_text(encoding="utf-8")

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "executed")
        self.assertEqual(report["delivery"]["result"]["response"]["msgid"], "msg-1")
        self.assertEqual(client.calls[0]["to_user"], "ww-user")
        self.assertNotIn(issued["token"], raw)
        self.assertNotIn("ww-user", raw)

    def test_im_smoke_execute_blocks_invalid_delivery_config_before_outbox_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root)
            outbox = root / "outbox.jsonl"
            client = _FakeIMClient(confirmation_url_base="")

            report = run_im_delivery_smoke(
                env={**_env(), "CODEKB_ENABLE_IM_SEND": "1", "CODEKB_IM_CONFIRM_URL_BASE": ""},
                auth_token=issued["token"],
                token_store_path=str(token_store),
                confirmation_outbox_path=str(outbox),
                delivery_report_path=str(root / "report.json"),
                delivery_log_path=str(root / "delivery.jsonl"),
                execute=True,
                write_enabled=True,
                client=client,
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "blocked_invalid_delivery_config")
        self.assertFalse(report["delivery_config"]["ok"])
        self.assertIsNone(report["confirmation"])
        self.assertFalse(outbox.exists())

    def test_im_smoke_missing_credentials_blocks_credential_check(self):
        report = run_im_delivery_smoke(
            env={},
            check_credentials=True,
            client=_FakeIMClient(configured=False),
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "blocked_missing_credentials")
        self.assertIn("CODEKB_IM_CORP_ID", report["credentials"]["missing_env"])

    def test_cli_im_smoke_json_can_skip_credential_check(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-im-smoke",
                        "--skip-credential-check",
                        "--auth-token",
                        issued["token"],
                        "--token-store",
                        str(token_store),
                        "--confirmation-outbox",
                        str(root / "outbox.jsonl"),
                        "--delivery-report",
                        str(root / "report.json"),
                        "--delivery-log",
                        str(root / "delivery.jsonl"),
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["credentials"]["status"], "skipped")
        self.assertNotIn(issued["token"], stdout.getvalue())
        self.assertNotIn("ww-user", stdout.getvalue())


def _env() -> dict[str, str]:
    return {
        "CODEKB_IM_CORP_ID": "corp-id",
        "CODEKB_IM_AGENT_ID": "100001",
        "CODEKB_IM_APP_SECRET": "app-secret",
        "CODEKB_IM_CONFIRM_URL_BASE": "https://kb.example/confirm",
    }


def _issue_token(root: Path):
    token_store = root / "tokens.json"
    issued = JsonUserTokenStore(token_store).issue(
        user_id_hash="u_hash",
        display_name="User",
        scopes=["diagnose"],
        metadata={"im_userid": "ww-user", "source": "test"},
    )
    return token_store, issued


class _FakeIMClient:
    def __init__(self, *, configured: bool = True, confirmation_url_base: str = "https://kb.example/confirm") -> None:
        self.corp_id = "corp-id" if configured else ""
        self.agent_id = "100001" if configured else ""
        self.app_secret = "app-secret" if configured else ""
        self.api_base = "https://im.example/cgi-bin"
        self.confirmation_url_base = confirmation_url_base
        self.calls = []

    def configured(self) -> bool:
        return bool(self.corp_id and self.agent_id and self.app_secret)

    def _get_access_token(self) -> str:
        return "fake-access-token"

    def send_confirmation(self, *, to_user: str, request):
        self.calls.append({"to_user": to_user, "confirmation_id": request.confirmation_id})
        return {"errcode": 0, "errmsg": "ok", "msgid": "msg-1"}


if __name__ == "__main__":
    unittest.main()
