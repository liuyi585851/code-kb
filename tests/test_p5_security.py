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
from codekb.p5_security import build_p5_security_bootstrap, render_p5_security_env, write_p5_security_env_file


class P5SecurityTests(unittest.TestCase):
    def test_build_p5_security_bootstrap_generates_expected_env(self):
        counter = {"value": 0}

        def token_factory(token_bytes: int) -> str:
            counter["value"] += 1
            return f"generated-{token_bytes}-{counter['value']}"

        payload = build_p5_security_bootstrap(
            include_admin_token=True,
            include_static_mcp_token=False,
            token_bytes=24,
            token_factory=token_factory,
        )
        env = payload["env"]

        self.assertEqual(env["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"], "generated-24-1")
        self.assertEqual(env["CODEKB_IM_OAUTH_STATE_SECRET"], "generated-24-2")
        self.assertEqual(env["CODEKB_AUTH_ADMIN_TOKEN"], "generated-24-3")
        self.assertNotIn("CODEKB_MCP_TOKEN", env)
        self.assertIn("diagnose-readiness", " ".join(payload["next_steps"]))

    def test_build_p5_security_bootstrap_marks_static_mcp_token_local_only(self):
        payload = build_p5_security_bootstrap(
            include_static_mcp_token=True,
            token_bytes=16,
            token_factory=lambda token_bytes: f"generated-{token_bytes}",
        )

        self.assertIn("CODEKB_MCP_TOKEN", payload["env"])
        self.assertIn("local diagnose smoke only", " ".join(payload["warnings"]))
        self.assertIn("--token-store", " ".join(payload["warnings"]))

    def test_render_and_write_p5_security_env_file_uses_0600(self):
        payload = {
            "env": {
                "CODEKB_DIAGNOSE_WEBHOOK_TOKEN": "webhook-token",
                "CODEKB_IM_OAUTH_STATE_SECRET": "state-secret",
            }
        }
        content = render_p5_security_env(payload)
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "p5-secrets.env"
            write_p5_security_env_file(output, content)
            mode = stat.S_IMODE(output.stat().st_mode)

        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_TOKEN=webhook-token", content)
        self.assertEqual(mode, 0o600)

    def test_cli_diagnose_security_bootstrap_outputs_json(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(["diagnose-security-bootstrap", "--no-admin-token", "--token-bytes", "16", "--json"])
        payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", payload["env"])
        self.assertIn("CODEKB_IM_OAUTH_STATE_SECRET", payload["env"])
        self.assertNotIn("CODEKB_AUTH_ADMIN_TOKEN", payload["env"])
        self.assertNotIn("CODEKB_MCP_TOKEN", payload["env"])

    def test_cli_diagnose_security_bootstrap_can_write_output_file(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "p5-secrets.env"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["diagnose-security-bootstrap", "--output", str(output)])
            raw = output.read_text(encoding="utf-8")
            mode = stat.S_IMODE(output.stat().st_mode)

        self.assertEqual(code, 0)
        self.assertIn("output=", stdout.getvalue())
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_TOKEN=", raw)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
