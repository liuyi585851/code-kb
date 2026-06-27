import contextlib
import io
import json
import sys
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main  # noqa: E402
from codekb.p5_external_state import build_p5_external_state  # noqa: E402


class P5ExternalStateTests(unittest.TestCase):
    def test_external_state_reports_pending_without_secret_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "p5.env"
            env.write_text(
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n",
                encoding="utf-8",
            )
            template = root / "im-config.todo.env"
            template.write_text("CODEKB_IM_CORP_ID=\n", encoding="utf-8")
            report = build_p5_external_state(
                env_file=str(env),
                im_template=str(template),
                token_store=str(root / "tokens.json"),
                real_samples=str(root / "real.yaml"),
            )
            raw = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["status"], "pending_external_inputs")
        self.assertFalse(report["ok"])
        self.assertIn("mcp_auth", report["pending_checks"])
        self.assertIn("im_env", report["pending_checks"])
        self.assertIn("CODEKB_IM_CORP_ID", raw)
        self.assertNotIn("app-secret-value", raw)
        self.assertNotIn("state-secret-value", raw)

    def test_external_state_ready_when_all_evidence_present(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "p5.env"
            real = root / "real.yaml"
            real.write_text("samples: []\n", encoding="utf-8")
            env.write_text(
                "CODEKB_IM_CORP_ID=corp\n"
                "CODEKB_IM_AGENT_ID=100001\n"
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n"
                "CODEKB_IM_CONFIRM_URL_BASE=https://kb.example/auth/im/confirmations/page\n"
                "CODEKB_ENABLE_IM_SEND=1\n"
                f"CODEKB_DIAGNOSE_WEBHOOK_SAMPLES={real}\n",
                encoding="utf-8",
            )
            token_store = root / "tokens.json"
            token_store.write_text(
                json.dumps(
                    [
                        {
                            "token_id": "tok-1",
                            "token_hash": sha256(b"token").hexdigest(),
                            "revoked_at": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = build_p5_external_state(
                env_file=str(env),
                im_template=str(root / "missing-template.env"),
                token_store=str(token_store),
                real_samples=str(real),
            )

        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ok"])
        self.assertEqual(report["pending_checks"], [])

    def test_external_state_ready_with_self_binding_and_web_inbox_without_im_app(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "p5.env"
            real = root / "real.yaml"
            real.write_text("samples: []\n", encoding="utf-8")
            env.write_text(
                "CODEKB_USER_BINDING_CODE=bind-code\n"
                f"CODEKB_DIAGNOSE_WEBHOOK_SAMPLES={real}\n",
                encoding="utf-8",
            )
            token_store = root / "tokens.json"
            token_store.write_text(
                json.dumps(
                    [
                        {
                            "token_id": "tok-1",
                            "token_hash": sha256(b"token").hexdigest(),
                            "revoked_at": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = build_p5_external_state(
                env_file=str(env),
                im_template=str(root / "missing-template.env"),
                token_store=str(token_store),
                real_samples=str(real),
            )

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ok"])
        self.assertEqual(report["pending_checks"], [])
        self.assertTrue(checks["im_env"]["self_binding_configured"])
        self.assertEqual(checks["im_delivery"]["status"], "ok")
        self.assertFalse(checks["im_delivery"]["send_enabled"])

    def test_external_state_keeps_delivery_pending_without_confirmation_url(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "p5.env"
            env.write_text(
                "CODEKB_IM_CORP_ID=corp\n"
                "CODEKB_IM_AGENT_ID=100001\n"
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n"
                "CODEKB_ENABLE_IM_SEND=1\n",
                encoding="utf-8",
            )
            report = build_p5_external_state(
                env_file=str(env),
                im_template=str(root / "missing-template.env"),
                token_store=str(root / "tokens.json"),
                real_samples=str(root / "real.yaml"),
            )
            delivery = next(check for check in report["checks"] if check["id"] == "im_delivery")

        self.assertIn("im_delivery", report["pending_checks"])
        self.assertEqual(delivery["status"], "pending")
        self.assertFalse(delivery["delivery_config"]["ok"])
        self.assertIn("CODEKB_IM_CONFIRM_URL_BASE", " ".join(delivery["delivery_config"]["errors"]))

    def test_external_state_does_not_count_expired_tokens_as_active(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store = root / "tokens.json"
            token_store.write_text(
                json.dumps(
                    [
                        {
                            "token_id": "tok-expired",
                            "token_hash": sha256(b"token").hexdigest(),
                            "created_at": "2020-01-01T00:00:00Z",
                            "expires_at": "2020-01-02T00:00:00Z",
                            "revoked_at": "",
                            "user_id_hash": "u_hash",
                            "scopes": ["diagnose"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = build_p5_external_state(
                env_file=str(root / "p5.env"),
                im_template=str(root / "im-config.todo.env"),
                token_store=str(token_store),
                real_samples=str(root / "real.yaml"),
            )
            mcp_auth = next(check for check in report["checks"] if check["id"] == "mcp_auth")

        self.assertEqual(mcp_auth["status"], "pending")
        self.assertEqual(mcp_auth["active_tokens"], 0)
        self.assertEqual(mcp_auth["expired_tokens"], 1)
        self.assertIn("mcp_auth", report["pending_checks"])

    def test_cli_external_state_outputs_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-p5-external-state",
                        "--env-file",
                        str(root / "p5.env"),
                        "--im-template",
                        str(root / "im-config.todo.env"),
                        "--token-store",
                        str(root / "tokens.json"),
                        "--real-samples",
                        str(root / "real.yaml"),
                        "--json",
                    ]
                )
            report = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "pending_external_inputs")
        self.assertIn("checks", report)


if __name__ == "__main__":
    unittest.main()
