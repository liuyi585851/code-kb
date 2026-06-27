import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.api import _run_diagnosis
from codekb.cli import main
from codekb.diagnosis import build_diagnostic_result
from codekb.diagnosis_context import DiagnosticContext, parse_diagnostic_context
from codekb.diagnosis_webhook import (
    JsonlDiagnosticWebhookStore,
    effective_diagnostic_webhook_mapping,
    import_diagnostic_webhook_sample,
    normalize_diagnostic_webhook,
    preview_diagnostic_webhook,
    validate_diagnostic_webhook,
    validate_diagnostic_webhook_sample_suite,
)
from codekb.models import AnswerResult
from codekb.service import OfflineKbService


ROOT = Path(__file__).resolve().parents[1]


class DiagnosisWebhookTests(unittest.TestCase):
    def test_code_review_payload_maps_to_diagnostic_context(self):
        payload = normalize_diagnostic_webhook(
            "code_review",
            {
                "repository": {"path": "ym/app", "url": "https://example.invalid/ym/app"},
                "merge_request": {"iid": 123, "source_branch": "feature/udt", "url": "https://example.invalid/mr/123"},
                "pipeline": {"id": "build-456", "url": "https://example.invalid/build/456?token=abc123"},
                "job": {"name": "udt-ci"},
                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败 password=abc123"},
                "log_tail": "Authorization: Bearer secret-token-value\nmissing DEVICE_SEQ",
                "event": "build_failed",
                "sub_kbs": ["testing"],
                "include_governance": False,
            },
        )

        context = parse_diagnostic_context(payload["context"])

        self.assertEqual(context.surface, "code_review")
        self.assertEqual(context.repo, "ym/app")
        self.assertEqual(context.branch, "feature/udt")
        self.assertEqual(context.mr_id, "123")
        self.assertEqual(context.build_id, "build-456")
        self.assertEqual(context.job_name, "udt-ci")
        self.assertEqual(context.error_code, "DEVICE_SEQ")
        self.assertNotIn("abc123", context.error_text)
        self.assertNotIn("secret-token-value", context.log_excerpt)
        self.assertNotIn("abc123", context.links["build"])
        self.assertIn("build_failed", context.tags)
        self.assertEqual(payload["sub_kbs"], ["testing"])

    def test_generic_context_is_preserved_and_augmented(self):
        payload = normalize_diagnostic_webhook(
            "generic",
            {
                "context": {"surface": "custom", "repo": "ym/app", "tags": ["manual"]},
                "error_code": "DEVICE_SEQ",
                "error_text": "DEVICE_SEQ missing",
                "tags": "ci,udt",
            },
        )

        context = parse_diagnostic_context(payload["context"])

        self.assertEqual(context.surface, "custom")
        self.assertEqual(context.repo, "ym/app")
        self.assertEqual(context.error_code, "DEVICE_SEQ")
        self.assertEqual(context.tags, ("manual", "generic", "ci", "udt"))

    def test_issue_tracker_payload_maps_with_draft_mapping(self):
        preview = preview_diagnostic_webhook(
            "issue_tracker",
            {
                "issue_tracker": {
                    "project": {"name": "ym/app"},
                    "bug": {
                        "id": "BUG-123",
                        "title": "DEVICE_SEQ 构建失败",
                        "description": "DEVICE_SEQ password=abc123",
                        "url": "https://issue_tracker.example/bug/123?token=abc123",
                    },
                    "priority": "P1",
                    "status": "new",
                },
                "sub_kbs": ["testing"],
            },
            mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
        )
        text = json.dumps(preview, ensure_ascii=False)

        self.assertEqual(preview["source"], "issue_tracker")
        self.assertEqual(preview["context"]["surface"], "issue_tracker")
        self.assertEqual(preview["context"]["repo"], "ym/app")
        self.assertEqual(preview["context"]["error_code"], "BUG-123")
        self.assertIn("DEVICE_SEQ 构建失败", preview["query"])
        self.assertIn("P1", preview["context"]["tags"])
        self.assertNotIn("abc123", text)
        self.assertIn("[REDACTED]", text)

    def test_crash_payload_maps_with_draft_mapping(self):
        report = validate_diagnostic_webhook(
            "crash",
            {
                "crash": {
                    "app": "ym/app",
                    "version": "1.2.3",
                    "issue_id": "CRASH-9",
                    "summary": "DEVICE_SEQ 相关崩溃",
                    "stack_trace": "Authorization: Bearer secret-token-value\nDEVICE_SEQ",
                    "url": "https://crash.example/issue/9?token=abc123",
                    "platform": "android",
                    "severity": "P1",
                },
                "sub_kbs": ["testing"],
            },
            mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
        )
        text = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["valid"])
        self.assertTrue(report["query_ready"])
        self.assertEqual(report["source"], "crash")
        self.assertEqual(report["context"]["surface"], "crash")
        self.assertEqual(report["context"]["repo"], "ym/app")
        self.assertEqual(report["context"]["error_code"], "CRASH-9")
        self.assertEqual(report["context"]["build_id"], "1.2.3")
        self.assertIn("android", report["context"]["tags"])
        self.assertNotIn("secret-token-value", text)
        self.assertNotIn("abc123", text)

    def test_unknown_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "webhook source"):
            normalize_diagnostic_webhook("unknown", {})

    def test_preview_webhook_derives_redacted_diagnostic_payload(self):
        preview = preview_diagnostic_webhook(
            "code_review",
            {
                "repository": {"path": "ym/app"},
                "pipeline": {"id": "build-456", "url": "https://example.invalid/build?token=abc123"},
                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败 password=abc123"},
                "sub_kbs": ["testing"],
                "include_governance": False,
            },
        )
        text = json.dumps(preview, ensure_ascii=False)

        self.assertEqual(preview["source"], "code_review")
        self.assertEqual(preview["sub_kbs"], ["testing"])
        self.assertEqual(preview["context"]["repo"], "ym/app")
        self.assertIn("DEVICE_SEQ 构建失败", preview["query"])
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("abc123", text)

    def test_validate_webhook_reports_ready_payload(self):
        report = validate_diagnostic_webhook(
            "code_review",
            {
                "repository": {"path": "ym/app"},
                "pipeline": {"url": "https://example.invalid/build?token=abc123"},
                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=abc123"},
                "sub_kbs": ["testing"],
                "top_k": 4,
            },
        )
        text = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["valid"])
        self.assertTrue(report["query_ready"])
        self.assertEqual(report["context"]["repo"], "ym/app")
        self.assertEqual(report["sub_kbs"], ["testing"])
        self.assertTrue(report["extracted_fields"]["context"]["repo"])
        self.assertTrue(report["extracted_fields"]["context"]["error_code"])
        self.assertTrue(report["extracted_fields"]["sub_kbs"])
        self.assertEqual(report["diagnostic_payload"]["top_k"], 4)
        self.assertNotIn("abc123", text)
        self.assertIn("[REDACTED]", text)

    def test_validate_webhook_reports_missing_query_without_raising(self):
        report = validate_diagnostic_webhook("ci", {"repo": "ym/app"})

        self.assertFalse(report["valid"])
        self.assertFalse(report["query_ready"])
        self.assertEqual(report["query"], "")
        self.assertIn("query is required", " ".join(report["errors"]))
        self.assertTrue(report["extracted_fields"]["context"]["repo"])
        self.assertFalse(report["extracted_fields"]["sub_kbs"])
        self.assertEqual(report["diagnostic_payload"], {})

    def test_configured_mapping_paths_are_used_before_builtin_paths(self):
        with TemporaryDirectory() as tmp:
            mapping_path = Path(tmp) / "mapping.yaml"
            mapping_path.write_text(
                """
version: 1
sources:
  code_review:
    query_paths:
      - code_review.issue.title
    sub_kbs_paths:
      - code_review.kb_area
    context_paths:
      repo:
        - code_review.repo_path
      error_code:
        - code_review.failure.code
      error_text:
        - code_review.failure.message
    link_paths:
      build:
        - code_review.pipeline_link
    tag_paths:
      - code_review.event_name
""",
                encoding="utf-8",
            )
            payload = normalize_diagnostic_webhook(
                "code_review",
                {
                    "repository": {"path": "builtin/repo"},
                    "code_review": {
                        "issue": {"title": "DEVICE_SEQ from configured path"},
                        "repo_path": "configured/repo",
                        "failure": {"code": "DEVICE_SEQ", "message": "configured failure password=abc123"},
                        "pipeline_link": "https://example.invalid/build?token=abc123",
                        "event_name": "configured_event",
                        "kb_area": ["testing"],
                    },
                },
                mapping_path=mapping_path,
            )

        context = parse_diagnostic_context(payload["context"])
        text = json.dumps(context.to_dict(), ensure_ascii=False)

        self.assertEqual(payload["query"], "DEVICE_SEQ from configured path")
        self.assertEqual(payload["sub_kbs"], ["testing"])
        self.assertEqual(context.repo, "configured/repo")
        self.assertEqual(context.error_code, "DEVICE_SEQ")
        self.assertIn("configured_event", context.tags)
        self.assertNotIn("abc123", text)
        self.assertIn("[REDACTED]", text)

    def test_effective_mapping_reports_configured_and_builtin_paths(self):
        with TemporaryDirectory() as tmp:
            mapping_path = Path(tmp) / "mapping.yaml"
            mapping_path.write_text(
                """
version: 1
sources:
  code_review:
    context_paths:
      repo:
        - code_review.repo_path
""",
                encoding="utf-8",
            )
            mapping = effective_diagnostic_webhook_mapping("code_review", mapping_path).to_dict()

        repo_paths = mapping["sources"]["code_review"]["context_paths"]["repo"]
        self.assertEqual(repo_paths[0], "code_review.repo_path")
        self.assertIn("repository.path", repo_paths)

    def test_draft_mapping_supports_code_review_alias_paths(self):
        preview = preview_diagnostic_webhook(
            "code_review",
            {
                "code_review": {
                    "repo_path": "ym/configured",
                    "failure": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=abc123"},
                    "pipeline_link": "https://example.invalid/build?token=abc123",
                    "event_name": "configured_event",
                    "kb_area": ["testing"],
                }
            },
            mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
        )
        text = json.dumps(preview, ensure_ascii=False)

        self.assertEqual(preview["context"]["repo"], "ym/configured")
        self.assertEqual(preview["context"]["error_code"], "DEVICE_SEQ")
        self.assertEqual(preview["sub_kbs"], ["testing"])
        self.assertIn("configured_event", preview["context"]["tags"])
        self.assertNotIn("abc123", text)
        self.assertIn("[REDACTED]", text)


    def test_webhook_event_store_appends_redacted_diagnosis_summary(self):
        answer = AnswerResult(
            query="error_code=DEVICE_SEQ password=[REDACTED]",
            answer="我现在没有足够可引用的知识来源来回答这个问题。",
            citations=(),
            refused=True,
            refusal_reason="NO_CITATION",
            answer_id="answer-1",
            trace_id="trace-1",
        )
        diagnosis = build_diagnostic_result(
            answer,
            sub_kbs={"release"},
            context=DiagnosticContext(surface="ci", repo="ym/app", error_code="DEVICE_SEQ"),
        )

        with TemporaryDirectory() as tmp:
            store = JsonlDiagnosticWebhookStore(Path(tmp) / "diagnose-webhook.jsonl")
            event = store.append(source="ci", action="diagnose", status="diagnosed", diagnosis=diagnosis)
            summary = store.summary()

        self.assertEqual(event.source, "ci")
        self.assertEqual(event.status, "diagnosed")
        self.assertEqual(event.context["repo"], "ym/app")
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_source"], {"ci": 1})
        self.assertEqual(summary["events"][0]["diagnosis_id"], diagnosis.diagnosis_id)

    def test_webhook_event_store_appends_redacted_failure_summary(self):
        with TemporaryDirectory() as tmp:
            store = JsonlDiagnosticWebhookStore(Path(tmp) / "diagnose-webhook.jsonl")
            event = store.append_failure(
                source="code_review",
                action="diagnose",
                status="bad_request",
                error_type="ValueError",
                error_message="top_k failed token=abc123",
                normalized={
                    "query": "DEVICE_SEQ password=abc123",
                    "context": {
                        "surface": "code_review",
                        "repo": "ym/app",
                        "error_text": "DEVICE_SEQ failed password=abc123",
                        "links": {"build": "https://example.invalid/build?id=1&token=abc123"},
                    },
                    "sub_kbs": ["testing"],
                },
            )
            raw = (Path(tmp) / "diagnose-webhook.jsonl").read_text(encoding="utf-8")
            summary = store.summary()

        self.assertEqual(event.status, "bad_request")
        self.assertEqual(event.error["type"], "ValueError")
        self.assertNotIn("abc123", raw)
        self.assertIn("[REDACTED]", raw)
        self.assertEqual(summary["by_status"], {"bad_request": 1})
        self.assertEqual(summary["events"][0]["context"]["repo"], "ym/app")

    def test_webhook_event_summary_filters_source_status_action(self):
        answer = AnswerResult(
            query="DEVICE_SEQ 构建失败",
            answer="根据当前可引用知识，先给出可追溯摘要。",
            citations=(),
            refused=False,
            answer_id="answer-1",
            trace_id="trace-1",
            confidence=0.8,
        )
        diagnosis = build_diagnostic_result(
            answer,
            sub_kbs={"testing"},
            context=DiagnosticContext(surface="code_review", repo="ym/app", error_code="DEVICE_SEQ"),
        )

        with TemporaryDirectory() as tmp:
            store = JsonlDiagnosticWebhookStore(Path(tmp) / "diagnose-webhook.jsonl")
            store.append(source="code_review", action="diagnose", status="diagnosed", diagnosis=diagnosis)
            store.append_failure(
                source="ci",
                action="diagnose",
                status="bad_request",
                error_type="ValueError",
                error_message="bad request",
                normalized={"query": "bad"},
            )
            summary = store.summary(source="code_review", status="diagnosed", action="diagnose")

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["unfiltered_total"], 2)
        self.assertEqual(summary["filters"]["source"], "code_review")
        self.assertEqual(summary["by_source"], {"code_review": 1})
        self.assertEqual(summary["events"][0]["diagnosis_id"], diagnosis.diagnosis_id)

    def test_cli_diagnose_webhook_events_outputs_filtered_json(self):
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "diagnose-webhook.jsonl"
            store = JsonlDiagnosticWebhookStore(log_path)
            store.append_failure(
                source="code_review",
                action="diagnose",
                status="bad_request",
                error_type="ValueError",
                error_message="bad token=abc123",
                normalized={"query": "DEVICE_SEQ token=abc123"},
            )
            store.append_failure(
                source="ci",
                action="diagnose",
                status="bad_request",
                error_type="ValueError",
                error_message="bad",
                normalized={"query": "OTHER"},
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-webhook-events",
                        "--log",
                        str(log_path),
                        "--source",
                        "code_review",
                        "--status",
                        "bad_request",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["unfiltered_total"], 2)
        self.assertEqual(payload["events"][0]["source"], "code_review")
        self.assertNotIn("abc123", json.dumps(payload, ensure_ascii=False))

    def test_cli_diagnose_webhook_normalize_outputs_preview_json(self):
        with TemporaryDirectory() as tmp:
            mapping_path = Path(tmp) / "mapping.yaml"
            mapping_path.write_text(
                """
version: 1
sources:
  code_review:
    context_paths:
      repo:
        - code_review.repo_path
""",
                encoding="utf-8",
            )
            payload_json = json.dumps(
                {
                    "code_review": {"repo_path": "configured/repo"},
                    "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=abc123"},
                    "sub_kbs": ["testing"],
                },
                ensure_ascii=False,
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-webhook-normalize",
                        "code_review",
                        "--payload-json",
                        payload_json,
                        "--mapping",
                        str(mapping_path),
                        "--json",
                    ]
                )

        preview = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(preview["source"], "code_review")
        self.assertEqual(preview["context"]["repo"], "configured/repo")
        self.assertEqual(preview["sub_kbs"], ["testing"])
        self.assertNotIn("abc123", json.dumps(preview, ensure_ascii=False))

    def test_cli_diagnose_webhook_validate_outputs_report_json(self):
        payload_json = json.dumps(
            {
                "repository": {"path": "ym/app"},
                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=abc123"},
                "sub_kbs": ["testing"],
            },
            ensure_ascii=False,
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "diagnose-webhook-validate",
                    "code_review",
                    "--payload-json",
                    payload_json,
                    "--json",
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertTrue(report["query_ready"])
        self.assertNotIn("abc123", json.dumps(report, ensure_ascii=False))

    def test_cli_diagnose_webhook_validate_accepts_crash_source(self):
        payload_json = json.dumps(
            {
                "crash": {
                    "app": "ym/app",
                    "issue_id": "CRASH-9",
                    "summary": "DEVICE_SEQ 相关崩溃",
                    "stack_trace": "DEVICE_SEQ",
                }
            },
            ensure_ascii=False,
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "diagnose-webhook-validate",
                    "crash",
                    "--payload-json",
                    payload_json,
                    "--mapping",
                    str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                    "--json",
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertEqual(report["source"], "crash")
        self.assertEqual(report["context"]["error_code"], "CRASH-9")

    def test_cli_diagnose_webhook_validate_returns_nonzero_for_invalid_payload(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "diagnose-webhook-validate",
                    "ci",
                    "--payload-json",
                    '{"repo":"ym/app"}',
                    "--json",
                ]
            )

        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(report["valid"])
        self.assertIn("query is required", " ".join(report["errors"]))

    def test_cli_diagnose_webhook_mapping_outputs_effective_mapping_json(self):
        with TemporaryDirectory() as tmp:
            mapping_path = Path(tmp) / "mapping.yaml"
            mapping_path.write_text(
                """
version: 1
sources:
  code_review:
    context_paths:
      repo:
        - code_review.repo_path
""",
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-webhook-mapping",
                        "code_review",
                        "--mapping",
                        str(mapping_path),
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["sources"]["code_review"]["context_paths"]["repo"][0], "code_review.repo_path")

    def test_sample_suite_validates_default_platform_samples(self):
        summary = validate_diagnostic_webhook_sample_suite(
            ROOT / "docs" / "diagnose-webhook-samples.draft.yaml",
            mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
        )
        text = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["passed"], 6)
        self.assertEqual({sample["source"] for sample in summary["samples"]}, {"code_review", "ci", "mr", "issue_tracker", "crash", "generic"})
        self.assertNotIn("code_review-token-secret", text)
        self.assertNotIn("issue_tracker-token-secret", text)
        self.assertNotIn("generic-refresh-secret", text)

    def test_sample_suite_reports_expected_field_mismatch(self):
        with TemporaryDirectory() as tmp:
            samples_path = Path(tmp) / "samples.yaml"
            samples_path.write_text(
                """
version: 1
samples:
  - name: bad_repo_expectation
    source: code_review
    expected_context:
      repo: expected/repo
    payload:
      repository:
        path: actual/repo
      error:
        code: DEVICE_SEQ
        message: DEVICE_SEQ
""",
                encoding="utf-8",
            )

            summary = validate_diagnostic_webhook_sample_suite(samples_path)

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failed"], 1)
        self.assertIn("context.repo expected", " ".join(summary["samples"][0]["errors"]))

    def test_cli_diagnose_webhook_sample_suite_outputs_json(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "diagnose-webhook-sample-suite",
                    "--samples",
                    str(ROOT / "docs" / "diagnose-webhook-samples.draft.yaml"),
                    "--mapping",
                    str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                    "--json",
                ]
            )

        summary = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["total"], 6)

    def test_import_webhook_sample_sanitizes_real_payload(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "samples.yaml"
            result = import_diagnostic_webhook_sample(
                source="code_review",
                name="real_code_review_case",
                payload={
                    "repository": {"path": "ym/app"},
                    "pipeline": {"url": "https://example.invalid/build?token=real-token-secret"},
                    "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=real-password-secret"},
                    "sub_kbs": ["testing"],
                },
                output_path=output,
                mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
            )
            raw = output.read_text(encoding="utf-8")
            summary = validate_diagnostic_webhook_sample_suite(
                output,
                mapping_path=ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml",
            )

        self.assertEqual(result["status"], "imported")
        self.assertGreaterEqual(result["raw_sensitive_values_detected"], 2)
        self.assertEqual(summary["status"], "passed")
        self.assertIn("real_code_review_case", raw)
        self.assertIn("[REDACTED]", raw)
        self.assertNotIn("real-token-secret", raw)
        self.assertNotIn("real-password-secret", raw)

    def test_cli_diagnose_webhook_sample_import_outputs_json(self):
        with TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "payload.yaml"
            output = Path(tmp) / "samples.yaml"
            payload_path.write_text(
                """
repository:
  path: ym/app
error:
  code: DEVICE_SEQ
  message: DEVICE_SEQ password=cli-password-secret
pipeline:
  url: https://example.invalid/build?access_token=cli-token-secret
sub_kbs:
  - testing
""",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-webhook-sample-import",
                        "code_review",
                        "--name",
                        "cli_imported_code_review",
                        "--payload-file",
                        str(payload_path),
                        "--output",
                        str(output),
                        "--mapping",
                        str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                        "--json",
                    ]
                )
            result = json.loads(stdout.getvalue())
            raw = output.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertNotIn("cli-password-secret", raw)
        self.assertNotIn("cli-token-secret", raw)


if __name__ == "__main__":
    unittest.main()
