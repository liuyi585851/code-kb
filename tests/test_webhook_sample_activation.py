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
from codekb.diagnosis_webhook import import_diagnostic_webhook_sample
from codekb.webhook_sample_activation import activate_diagnostic_webhook_samples


ROOT = Path(__file__).resolve().parents[1]


class WebhookSampleActivationTests(unittest.TestCase):
    def test_activation_plan_validates_real_samples_without_writing_env(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = _write_real_sample(root / "real.yaml")
            env_file = root / "p5.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")

            result = activate_diagnostic_webhook_samples(
                env_file=str(env_file),
                samples_path=str(samples),
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                env={},
            )
            raw_env = env_file.read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["applied"])
        self.assertEqual(result["sample_suite"]["status"], "passed")
        self.assertIn("code_review", result["sample_suite"]["sources"])
        self.assertNotIn("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", raw_env)

    def test_activation_apply_requires_real_sample_confirmation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = _write_real_sample(root / "real.yaml")
            env_file = root / "p5.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")

            result = activate_diagnostic_webhook_samples(
                env_file=str(env_file),
                samples_path=str(samples),
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                env={},
                apply=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "confirmation_required")
        self.assertNotIn("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", raw_env)

    def test_activation_apply_updates_env_file_without_leaking_existing_secrets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = _write_real_sample(root / "real.yaml")
            env_file = root / "p5.env"
            env_file.write_text(
                "\n".join(
                    [
                        "# existing env",
                        "CODEKB_AUTH_ADMIN_TOKEN=admin-secret",
                        "CODEKB_DIAGNOSE_WEBHOOK_SAMPLES=docs/diagnose-webhook-samples.draft.yaml",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = activate_diagnostic_webhook_samples(
                env_file=str(env_file),
                samples_path=str(samples),
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                env={},
                apply=True,
                confirm_real_samples=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")
            mode = stat.S_IMODE(env_file.stat().st_mode)
            raw_result = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "activated")
        self.assertTrue(result["applied"])
        self.assertEqual(mode, 0o600)
        self.assertIn(f"CODEKB_DIAGNOSE_WEBHOOK_SAMPLES={samples}", raw_env)
        self.assertIn(f"CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES={samples}", raw_env)
        self.assertIn("admin-secret", raw_env)
        self.assertNotIn("admin-secret", raw_result)

    def test_cli_sample_activate_outputs_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = _write_real_sample(root / "real.yaml")
            env_file = root / "p5.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-webhook-sample-activate",
                        "--env-file",
                        str(env_file),
                        "--samples",
                        str(samples),
                        "--mapping",
                        str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                        "--apply",
                        "--confirm-real-samples",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "activated")
        self.assertNotIn("admin-secret", stdout.getvalue())

    def test_activation_failed_validation_does_not_update_env(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / "bad.yaml"
            samples.write_text(
                "version: 1\nsamples:\n  - name: bad\n    source: code_review\n    payload: {}\n",
                encoding="utf-8",
            )
            env_file = root / "p5.env"
            env_file.write_text("CODEKB_AUTH_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")

            result = activate_diagnostic_webhook_samples(
                env_file=str(env_file),
                samples_path=str(samples),
                mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                env={},
                apply=True,
                confirm_real_samples=True,
            )
            raw_env = env_file.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "validation_failed")
        self.assertNotIn("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", raw_env)


def _write_real_sample(path: Path) -> Path:
    import_diagnostic_webhook_sample(
        source="code_review",
        name="code_review_real_build_failed_001",
        payload={
            "repository": {"path": "ym/app"},
            "pipeline": {"id": "build-1"},
            "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=real-secret"},
            "sub_kbs": ["testing"],
        },
        output_path=path,
        mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
        append=False,
    )
    return path


if __name__ == "__main__":
    unittest.main()
