import contextlib
import io
import json
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.im_config import configure_im_env, write_im_config_template


class IMConfigTests(unittest.TestCase):
    def test_config_plan_uses_existing_state_secret_without_writing_or_leaking(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text(
                "\n".join(
                    [
                        "CODEKB_AUTH_ADMIN_TOKEN=admin-secret",
                        "CODEKB_IM_OAUTH_STATE_SECRET=state-secret",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = configure_im_env(
                env_file=str(env_file),
                env={},
                values={
                    "corp_id": "corp-id",
                    "agent_id": "100001",
                    "app_secret": "app-secret",
                    "redirect_uri": "https://kb.example/auth/im/oauth/callback",
                },
            )
            raw_env = env_file.read_text(encoding="utf-8")
            raw_report = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "ready_to_apply")
        self.assertFalse(report["applied"])
        self.assertIn("CODEKB_IM_APP_SECRET", report["planned_update_keys"])
        self.assertNotIn("CODEKB_IM_CORP_ID=corp-id", raw_env)
        self.assertNotIn("app-secret", raw_report)
        self.assertNotIn("state-secret", raw_report)
        self.assertNotIn("admin-secret", raw_report)

    def test_config_apply_updates_env_file_with_0600_without_result_secret_leak(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")

            report = configure_im_env(
                env_file=str(env_file),
                env={},
                values={
                    "corp_id": "corp-id",
                    "agent_id": "100001",
                    "app_secret": "app-secret",
                    "oauth_state_secret": "state-secret",
                    "redirect_uri": "https://kb.example/auth/im/oauth/callback",
                    "confirm_url_base": "https://kb.example/auth/im/confirmations/page",
                },
                apply=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")
            mode = stat.S_IMODE(env_file.stat().st_mode)
            raw_report = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "applied")
        self.assertTrue(report["applied"])
        self.assertEqual(mode, 0o600)
        self.assertIn("CODEKB_IM_CORP_ID=corp-id", raw_env)
        self.assertIn("CODEKB_IM_APP_SECRET=app-secret", raw_env)
        self.assertIn("CODEKB_IM_OAUTH_STATE_SECRET=state-secret", raw_env)
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN=admin-secret", raw_env)
        self.assertNotIn("app-secret", raw_report)
        self.assertNotIn("state-secret", raw_report)
        self.assertNotIn("admin-secret", raw_report)

    def test_config_enable_send_requires_confirmation(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("", encoding="utf-8")

            report = configure_im_env(
                env_file=str(env_file),
                env={},
                values={
                    "corp_id": "corp-id",
                    "agent_id": "100001",
                    "app_secret": "app-secret",
                    "oauth_state_secret": "state-secret",
                },
                enable_send=True,
                apply=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "confirmation_required")
        self.assertNotIn("CODEKB_ENABLE_IM_SEND=1", raw_env)

    def test_cli_im_configure_json_does_not_print_secrets(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("CODEKB_IM_OAUTH_STATE_SECRET=state-secret\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-im-configure",
                        "--env-file",
                        str(env_file),
                        "--corp-id",
                        "corp-id",
                        "--agent-id",
                        "100001",
                        "--app-secret",
                        "app-secret",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ready_to_apply")
        self.assertNotIn("corp-id", stdout.getvalue())
        self.assertNotIn("app-secret", stdout.getvalue())
        self.assertNotIn("state-secret", stdout.getvalue())

    def test_write_template_omits_existing_secret_values_and_uses_0600(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "p5.env"
            output = root / "im.todo.env"
            env_file.write_text(
                "\n".join(
                    [
                        "CODEKB_IM_OAUTH_STATE_SECRET=state-secret",
                        "CODEKB_AUTH_ADMIN_TOKEN=admin-secret",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = write_im_config_template(
                output_path=str(output),
                env_file=str(env_file),
                api_base_url="https://kb.example",
            )
            raw_template = output.read_text(encoding="utf-8")
            raw_report = json.dumps(report, ensure_ascii=False)
            mode = stat.S_IMODE(output.stat().st_mode)

        self.assertEqual(report["status"], "template_written")
        self.assertEqual(mode, 0o600)
        self.assertIn("CODEKB_IM_CORP_ID=", raw_template)
        self.assertIn("https://kb.example/auth/im/oauth/callback", raw_template)
        self.assertIn("already exists", raw_template)
        self.assertNotIn("state-secret", raw_template + raw_report)
        self.assertNotIn("admin-secret", raw_template + raw_report)

    def test_cli_im_configure_template_json_does_not_print_existing_secrets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "p5.env"
            output = root / "im.todo.env"
            env_file.write_text("CODEKB_IM_OAUTH_STATE_SECRET=state-secret\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-im-configure",
                        "--env-file",
                        str(env_file),
                        "--template-output",
                        str(output),
                        "--api-base-url",
                        "https://kb.example",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            output_exists = output.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "template_written")
        self.assertTrue(output_exists)
        self.assertNotIn("state-secret", stdout.getvalue())

    def test_cli_im_configure_apply_from_template_without_printing_secrets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "p5.env"
            template = root / "im.todo.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")
            template.write_text(
                "\n".join(
                    [
                        "CODEKB_IM_CORP_ID=corp-id",
                        "CODEKB_IM_AGENT_ID=100001",
                        "CODEKB_IM_APP_SECRET=app-secret",
                        "CODEKB_IM_OAUTH_STATE_SECRET=state-secret",
                        "CODEKB_IM_OAUTH_REDIRECT_URI=https://kb.example/auth/im/oauth/callback",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-im-configure",
                        "--env-file",
                        str(env_file),
                        "--from-template",
                        str(template),
                        "--apply",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            raw_env = env_file.read_text(encoding="utf-8")
            raw_output = stdout.getvalue()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "applied")
        self.assertTrue(payload["applied"])
        self.assertEqual(payload["source_template"], str(template))
        self.assertIn("CODEKB_IM_CORP_ID=corp-id", raw_env)
        self.assertIn("CODEKB_IM_APP_SECRET=app-secret", raw_env)
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN=admin-secret", raw_env)
        self.assertNotIn("corp-id", raw_output)
        self.assertNotIn("app-secret", raw_output)
        self.assertNotIn("state-secret", raw_output)
        self.assertNotIn("admin-secret", raw_output)


if __name__ == "__main__":
    unittest.main()
