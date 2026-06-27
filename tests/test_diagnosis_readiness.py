import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.diagnosis_readiness import build_p5_readiness_report
from codekb.diagnosis_webhook import import_diagnostic_webhook_sample
from codekb.user_auth import JsonUserTokenStore


ROOT = Path(__file__).resolve().parents[1]


class DiagnosisReadinessTests(unittest.TestCase):

    def test_p5_readiness_blocks_missing_core_file(self):
        report = build_p5_readiness_report(
            fixture_path=str(ROOT / "missing-fixture.jsonl"),
            aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
            registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
            governance_policy_path=str(ROOT / "docs" / "governance-policy.draft.yaml"),
            diagnose_webhook_mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
            diagnose_webhook_samples_path=str(ROOT / "docs" / "diagnose-webhook-samples.draft.yaml"),
            user_token_store_path="",
            env={},
        )
        checks = {check["id"]: check for check in report["checks"]}

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(checks["diagnose_core_files"]["status"], "blocked")
        self.assertEqual(checks["mcp_auth"]["status"], "blocked")
        self.assertIn("diagnose_core_files", {item["id"] for item in report["required_actions"]})

    def test_p5_readiness_warns_when_static_mcp_token_is_configured(self):
        with TemporaryDirectory() as tmp:
            token_store = Path(tmp) / "tokens.json"
            JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            report = build_p5_readiness_report(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
                governance_policy_path=str(ROOT / "docs" / "governance-policy.draft.yaml"),
                diagnose_webhook_mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                diagnose_webhook_samples_path=str(ROOT / "docs" / "diagnose-webhook-samples.draft.yaml"),
                user_token_store_path=str(token_store),
                user_confirmation_outbox_path=str(Path(tmp) / "confirmation.jsonl"),
                user_confirmation_responses_path=str(Path(tmp) / "responses.jsonl"),
                env={"CODEKB_MCP_TOKEN": "static-secret-value"},
            )
            text = json.dumps(report, ensure_ascii=False)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["mcp_auth"]["status"], "warn")
        self.assertTrue(checks["mcp_auth"]["details"]["static_mcp_token_configured"])
        self.assertNotIn("static-secret-value", text)

    def test_p5_readiness_reports_inactive_real_sample_suite_details(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_samples = root / "diagnose-webhook-samples.real.yaml"
            _write_real_sample_suite(real_samples)

            report = build_p5_readiness_report(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
                governance_policy_path=str(ROOT / "docs" / "governance-policy.draft.yaml"),
                diagnose_webhook_mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                diagnose_webhook_samples_path=str(ROOT / "docs" / "diagnose-webhook-samples.draft.yaml"),
                env={"CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": str(real_samples)},
            )

        check = {item["id"]: item for item in report["checks"]}["external_platform_samples"]
        details = check["details"]
        self.assertEqual(check["status"], "warn")
        self.assertIn("exists but is not active", check["message"])
        self.assertIn(str(real_samples), check["remediation"])
        self.assertTrue(details["real_samples_exists"])
        self.assertEqual(details["real_samples_status"], "passed")
        self.assertEqual(details["real_samples_total"], 1)
        self.assertEqual(details["real_samples_generated_by_import"], 1)
        self.assertEqual(details["real_samples_sources"], ["code_review"])

    def test_p5_readiness_accepts_active_real_sample_suite(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_samples = root / "diagnose-webhook-samples.real.yaml"
            _write_real_sample_suite(real_samples)

            report = build_p5_readiness_report(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
                governance_policy_path=str(ROOT / "docs" / "governance-policy.draft.yaml"),
                diagnose_webhook_mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                diagnose_webhook_samples_path=str(real_samples),
                env={"CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": str(real_samples)},
            )

        checks = {item["id"]: item for item in report["checks"]}
        details = checks["external_platform_samples"]["details"]
        self.assertEqual(checks["webhook_sample_suite"]["status"], "ok")
        self.assertEqual(checks["external_platform_samples"]["status"], "ok")
        self.assertFalse(details["uses_default_draft_samples"])
        self.assertTrue(details["real_samples_exists"])
        self.assertEqual(details["real_samples_status"], "passed")
        self.assertEqual(details["real_samples_total"], 1)




def _write_real_sample_suite(path: Path) -> None:
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


if __name__ == "__main__":
    unittest.main()
