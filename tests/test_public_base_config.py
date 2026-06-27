import contextlib
import io
import json
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main  # noqa: E402
from codekb.public_base_config import configure_public_base_env  # noqa: E402


class PublicBaseConfigTests(unittest.TestCase):
    def test_public_base_plan_does_not_write_env(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("CODEKB_IM_APP_SECRET=app-secret\n", encoding="utf-8")

            report = configure_public_base_env(
                env_file=str(env_file),
                api_base_url="https://kb.example/",
            )
            raw_env = env_file.read_text(encoding="utf-8")

        self.assertEqual(report["status"], "ready_to_apply")
        self.assertTrue(report["ok"])
        self.assertFalse(report["applied"])
        self.assertEqual(report["urls"]["api_base_url"], "https://kb.example")
        self.assertEqual(report["urls"]["oauth_redirect_uri"], "https://kb.example/auth/im/oauth/callback")
        self.assertIn("CODEKB_API_BASE_URL", report["planned_update_keys"])
        self.assertNotIn("CODEKB_API_BASE_URL=https://kb.example", raw_env)

    def test_public_base_apply_updates_env_without_leaking_existing_secret(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text(
                "CODEKB_IM_APP_SECRET=app-secret\n"
                "CODEKB_API_BASE_URL=http://old.example\n",
                encoding="utf-8",
            )

            report = configure_public_base_env(
                env_file=str(env_file),
                api_base_url="http://kb.example:8080",
                apply=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")
            mode = stat.S_IMODE(env_file.stat().st_mode)
            raw_report = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["status"], "applied")
        self.assertTrue(report["restart_required"])
        self.assertEqual(mode, 0o600)
        self.assertIn("CODEKB_API_BASE_URL=http://kb.example:8080", raw_env)
        self.assertIn("CODEKB_IM_OAUTH_REDIRECT_URI=http://kb.example:8080/auth/im/oauth/callback", raw_env)
        self.assertIn("CODEKB_IM_CONFIRM_URL_BASE=http://kb.example:8080/auth/im/confirmations/page", raw_env)
        self.assertIn("CODEKB_IM_APP_SECRET=app-secret", raw_env)
        self.assertNotIn("app-secret", raw_report)

    def test_public_base_rejects_relative_url(self):
        with self.assertRaises(ValueError):
            configure_public_base_env(env_file="/tmp/p5.env", api_base_url="/relative", apply=False)

    def test_cli_public_base_configure_outputs_json(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("CODEKB_IM_APP_SECRET=app-secret\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-public-base-configure",
                        "--env-file",
                        str(env_file),
                        "--api-base-url",
                        "https://kb.example",
                        "--apply",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "applied")
        self.assertNotIn("app-secret", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
