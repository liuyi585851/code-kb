import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.p5_final_verify import (  # noqa: E402
    build_p5_final_verification_commands,
    render_p5_final_verification_text,
    run_p5_final_verification,
    write_p5_final_verification_report,
)


ROOT = Path(__file__).resolve().parents[1]


class P5FinalVerifyTests(unittest.TestCase):
    def test_final_verification_accepts_when_all_required_checks_pass(self):
        calls = []

        def runner(args, timeout_seconds, env):
            calls.append((tuple(args), timeout_seconds, dict(env)))
            joined = " ".join(args)
            if "diagnose-acceptance" in joined:
                return 0, json.dumps({"status": "accepted", "accepted": True}), ""
            if "diagnose-readiness" in joined:
                return 0, json.dumps({"status": "ready"}), ""
            if "diagnose-external-inputs" in joined:
                return 0, json.dumps({"status": "complete", "pending_count": 0}), ""
            if "diagnose-webhook-sample-suite" in joined:
                return 0, json.dumps({"status": "passed", "total": 2}), ""
            return 0, "ok", ""

        report = run_p5_final_verification(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example",
            include_slow=False,
            include_http=False,
            include_worker=False,
            runner=runner,
        )

        self.assertEqual(report["status"], "accepted")
        self.assertTrue(report["ok"])
        self.assertTrue(report["accepted"])
        self.assertEqual(report["failed_required"], [])
        self.assertFalse(report["secret_values_written"])
        self.assertTrue(all(call[2]["PYTHONPATH"] == "src" for call in calls))
        self.assertIn("diagnose_p5_final_verify status=accepted", render_p5_final_verification_text(report))

    def test_final_verification_reports_pending_external_inputs_and_redacts_output(self):
        def runner(args, timeout_seconds, env):
            joined = " ".join(args)
            if "diagnose-acceptance" in joined:
                return (
                    1,
                    json.dumps(
                        {
                            "status": "pending_external_inputs",
                            "accepted": False,
                            "external_inputs": [
                                {
                                    "check_id": "im_oauth",
                                    "evidence_needed": "IM credentials configured",
                                }
                            ],
                        }
                    ),
                    "",
                )
            if "diagnose-readiness" in joined:
                return 0, json.dumps({"status": "ready_with_warnings"}), ""
            if "diagnose-external-inputs" in joined:
                return 0, json.dumps({"status": "pending_external_inputs", "pending_count": 1}), ""
            if "diagnose-webhook-sample-suite" in joined:
                return 0, json.dumps({"status": "passed"}), ""
            if "diagnose-im-oauth-smoke" in joined:
                return 1, json.dumps({"status": "blocked_missing_env"}), ""
            if "p3-usecase-smoke" in joined:
                return 0, json.dumps({"status": "passed"}), ""
            if "quality-check" in joined:
                return 0, "quality_gate=PASS password=abc123", ""
            return 0, json.dumps({"status": "ok"}), ""

        report = run_p5_final_verification(
            env_file="/safe/p5.env",
            include_slow=False,
            include_http=False,
            include_worker=False,
            runner=runner,
        )
        raw = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["status"], "pending_external_inputs")
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_required"], [])
        self.assertIn("acceptance", report["pending_required"])
        self.assertIn("readiness", report["pending_required"])
        self.assertIn("acceptance", report["incomplete_required"])
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertGreaterEqual(report["summary"]["pending"], 3)
        self.assertIn("resolve im_oauth", report["next_steps"][0])
        self.assertNotIn("abc123", raw)

    def test_final_verification_next_steps_prefer_external_input_plan_order(self):
        def runner(args, timeout_seconds, env):
            joined = " ".join(args)
            if "diagnose-acceptance" in joined:
                return (
                    1,
                    json.dumps(
                        {
                            "status": "pending_external_inputs",
                            "accepted": False,
                            "external_inputs": [
                                {
                                    "check_id": "im_oauth",
                                    "evidence_needed": "IM credentials configured",
                                }
                            ],
                        }
                    ),
                    "",
                )
            if "diagnose-readiness" in joined:
                return 0, json.dumps({"status": "ready_with_warnings"}), ""
            if "diagnose-external-inputs" in joined:
                return (
                    0,
                    json.dumps(
                        {
                            "status": "pending_external_inputs",
                            "pending_count": 2,
                            "tasks": [
                                {
                                    "check_id": "im_oauth",
                                    "evidence_needed": "IM OAuth env configured",
                                },
                                {
                                    "check_id": "im_template",
                                    "evidence_needed": "IM config template completed",
                                },
                            ],
                            "operator_handoff": {
                                "ordered_task_ids": ["im_template", "im_oauth"],
                                "next_action": {
                                    "check_id": "im_template",
                                    "owner": "im_admin",
                                    "evidence_needed": "IM config template completed",
                                    "safe_commands": ["diagnose-im-configure --template-output /safe/im.env"],
                                    "verification_commands": ["diagnose-p5-external-state --json"],
                                },
                            },
                        }
                    ),
                    "",
                )
            if "diagnose-webhook-sample-suite" in joined:
                return 0, json.dumps({"status": "passed"}), ""
            if "p3-usecase-smoke" in joined:
                return 0, json.dumps({"status": "passed"}), ""
            return 0, json.dumps({"status": "ok"}), ""

        report = run_p5_final_verification(
            env_file="/safe/p5.env",
            include_slow=False,
            include_http=False,
            include_worker=False,
            runner=runner,
        )

        self.assertEqual(report["status"], "pending_external_inputs")
        self.assertEqual(report["next_steps"][0], "resolve im_template: IM config template completed")
        self.assertEqual(report["next_steps"][1], "resolve im_oauth: IM OAuth env configured")
        self.assertEqual(
            report["external_input_handoff"]["ordered_task_ids"],
            ["im_template", "im_oauth"],
        )
        self.assertEqual(report["external_input_handoff"]["next_action"]["check_id"], "im_template")
        self.assertEqual(
            report["external_input_handoff"]["next_action"]["evidence_needed"],
            "IM config template completed",
        )
        self.assertEqual(
            report["external_input_handoff"]["next_action"]["safe_commands"],
            ["diagnose-im-configure --template-output /safe/im.env"],
        )
        self.assertFalse(report["external_input_handoff"]["secret_values_written"])
        rendered = render_p5_final_verification_text(report)
        self.assertIn("HANDOFF status=pending_external_inputs", rendered)
        self.assertIn("next_action=im_template", rendered)
        self.assertIn("ordered_task_ids=im_template,im_oauth", rendered)
        self.assertIn("HANDOFF_SAFE diagnose-im-configure --template-output /safe/im.env", rendered)
        self.assertIn("HANDOFF_VERIFY diagnose-p5-external-state --json", rendered)

    def test_final_verification_marks_command_errors_as_failed(self):
        def runner(args, timeout_seconds, env):
            joined = " ".join(args)
            if "diagnose-readiness" in joined:
                return 2, "", "boom"
            if "diagnose-acceptance" in joined:
                return 1, json.dumps({"status": "pending_external_inputs", "accepted": False}), ""
            return 0, json.dumps({"status": "ok"}), ""

        report = run_p5_final_verification(
            env_file="/safe/p5.env",
            include_slow=False,
            include_http=False,
            include_worker=False,
            runner=runner,
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("readiness", report["failed_required"])
        self.assertIn("acceptance", report["pending_required"])

    def test_command_builder_can_skip_slow_http_and_worker_checks(self):
        original_token = os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
        try:
            commands = build_p5_final_verification_commands(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example",
                include_slow=False,
                include_http=False,
                include_worker=False,
            )
        finally:
            if original_token is not None:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        command_ids = {command.id for command in commands}

        self.assertNotIn("unit_tests", command_ids)
        self.assertNotIn("http_readiness", command_ids)
        self.assertNotIn("http_external_state", command_ids)
        self.assertNotIn("http_mcp_setup_status", command_ids)
        self.assertNotIn("confirmation_worker_once", command_ids)
        self.assertIn("acceptance", command_ids)
        self.assertIn("p3_usecase_smoke", command_ids)
        self.assertIn("mcp_auth_error_fallback", command_ids)
        self.assertIn("mcp_static_token_default_reject", command_ids)
        self.assertIn("mcp_token_store_static_reject", command_ids)
        self.assertIn("handoff_bundle_smoke", command_ids)
        self.assertIn("im_oauth_smoke", command_ids)
        self.assertIn("im_delivery_config_validation", command_ids)
        self.assertIn("im_smoke", command_ids)
        self.assertIn("current_user_smoke", command_ids)

    def test_command_builder_includes_required_p3_usecase_smoke(self):
        commands = build_p5_final_verification_commands(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example",
            include_slow=False,
            include_http=False,
            include_worker=False,
        )
        by_id = {command.id: command for command in commands}

        self.assertIn("p3_usecase_smoke", by_id)
        self.assertTrue(by_id["p3_usecase_smoke"].required)
        self.assertFalse(by_id["p3_usecase_smoke"].slow)
        self.assertIn("p3-usecase-smoke", by_id["p3_usecase_smoke"].args)
        self.assertIn("--publish-mode", by_id["p3_usecase_smoke"].args)
        self.assertIn("index_page", by_id["p3_usecase_smoke"].args)
        self.assertIn("--index-docid", by_id["p3_usecase_smoke"].args)
        self.assertIn("401", by_id["p3_usecase_smoke"].args)

    def test_current_user_smoke_checks_are_skipped_without_user_token(self):
        original_token = os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
        try:
            commands = build_p5_final_verification_commands(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example",
                include_slow=False,
                include_http=False,
                include_worker=False,
            )
        finally:
            if original_token is not None:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        by_id = {command.id: command for command in commands}

        self.assertIn("im_oauth_smoke", by_id)
        self.assertFalse(by_id["im_oauth_smoke"].required)
        self.assertGreater(by_id["im_oauth_smoke"].timeout_seconds, 0)
        self.assertIn("im_delivery_config_validation", by_id)
        self.assertTrue(by_id["im_delivery_config_validation"].required)
        self.assertGreater(by_id["im_delivery_config_validation"].timeout_seconds, 0)
        self.assertFalse(by_id["im_smoke"].required)
        self.assertEqual(by_id["im_smoke"].timeout_seconds, 0)
        self.assertIn("CODEKB_USER_AUTH_TOKEN", by_id["im_smoke"].skip_reason)
        self.assertFalse(by_id["current_user_smoke"].required)
        self.assertEqual(by_id["current_user_smoke"].timeout_seconds, 0)
        self.assertIn("CODEKB_USER_AUTH_TOKEN", by_id["current_user_smoke"].skip_reason)

    def test_optional_smoke_checks_report_pending_or_skipped_without_failing_required_gate(self):
        original_token = os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
        try:
            def runner(args, timeout_seconds, env):
                joined = " ".join(args)
                if "diagnose-acceptance" in joined:
                    return 1, json.dumps({"status": "pending_external_inputs", "accepted": False}), ""
                if "diagnose-readiness" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "diagnose-external-inputs" in joined:
                    return 0, json.dumps({"status": "complete", "pending_count": 0}), ""
                if "diagnose-webhook-sample-suite" in joined:
                    return 0, json.dumps({"status": "passed"}), ""
                if "diagnose-im-oauth-smoke" in joined:
                    return 1, json.dumps({"status": "blocked_missing_env"}), ""
                if "p3-usecase-smoke" in joined:
                    return 0, json.dumps({"status": "passed"}), ""
                return 0, json.dumps({"status": "ok"}), ""

            report = run_p5_final_verification(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example",
                include_slow=False,
                include_http=False,
                include_worker=False,
                runner=runner,
            )
        finally:
            if original_token is not None:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        by_id = {result["id"]: result for result in report["results"]}

        self.assertEqual(report["status"], "pending_external_inputs")
        self.assertEqual(report["failed_required"], [])
        self.assertEqual(by_id["im_oauth_smoke"]["status"], "pending")
        self.assertEqual(by_id["im_smoke"]["status"], "skipped")
        self.assertIn("CODEKB_USER_AUTH_TOKEN", by_id["im_smoke"]["stderr_tail"])
        self.assertEqual(by_id["current_user_smoke"]["status"], "skipped")

    def test_command_builder_includes_http_external_state_as_optional_check(self):
        original_token = os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
        try:
            commands = build_p5_final_verification_commands(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
        finally:
            if original_token is not None:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        by_id = {command.id: command for command in commands}

        self.assertIn("http_external_state", by_id)
        self.assertFalse(by_id["http_external_state"].required)
        self.assertIn("http://kb.example/diagnose/external-state", by_id["http_external_state"].args)
        self.assertIn("external_input_plan_alignment", by_id)
        self.assertFalse(by_id["external_input_plan_alignment"].required)
        self.assertIn("/safe/p5.env", by_id["external_input_plan_alignment"].args)
        self.assertIn("http_webhook_token_guard", by_id)
        self.assertFalse(by_id["http_webhook_token_guard"].required)
        self.assertIn("http://kb.example/diagnose/webhook/code_review/validate", by_id["http_webhook_token_guard"].args)
        self.assertIn("/safe/p5.env", by_id["http_webhook_token_guard"].args)
        self.assertIn("http_webhook_sample_import_smoke", by_id)
        self.assertFalse(by_id["http_webhook_sample_import_smoke"].required)
        self.assertIn("http://kb.example/diagnose/webhook/code_review/sample-import", by_id["http_webhook_sample_import_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["http_webhook_sample_import_smoke"].args)
        self.assertIn("mcp_auth_error_fallback", by_id)
        self.assertTrue(by_id["mcp_auth_error_fallback"].required)
        self.assertIn("http://kb.example", by_id["mcp_auth_error_fallback"].args)
        self.assertIn("mcp_static_token_default_reject", by_id)
        self.assertTrue(by_id["mcp_static_token_default_reject"].required)
        self.assertIn("http://kb.example", by_id["mcp_static_token_default_reject"].args)
        self.assertIn("mcp_token_store_static_reject", by_id)
        self.assertTrue(by_id["mcp_token_store_static_reject"].required)
        self.assertIn("http://kb.example", by_id["mcp_token_store_static_reject"].args)
        self.assertIn("mcp_explicit_confirmation_reasons", by_id)
        self.assertTrue(by_id["mcp_explicit_confirmation_reasons"].required)
        self.assertIn("http://kb.example", by_id["mcp_explicit_confirmation_reasons"].args)
        self.assertIn("handoff_bundle_smoke", by_id)
        self.assertFalse(by_id["handoff_bundle_smoke"].required)
        self.assertIn("/safe/p5-handoff", by_id["handoff_bundle_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["handoff_bundle_smoke"].args)
        self.assertIn("http://kb.example", by_id["handoff_bundle_smoke"].args)
        self.assertIn("http_mcp_setup_status", by_id)
        self.assertFalse(by_id["http_mcp_setup_status"].required)
        self.assertIn("http://kb.example/auth/im/mcp/setup/status", by_id["http_mcp_setup_status"].args)
        self.assertIn("http_mcp_setup_page", by_id)
        self.assertFalse(by_id["http_mcp_setup_page"].required)
        self.assertIn("http://kb.example/auth/im/mcp/setup", by_id["http_mcp_setup_page"].args)
        self.assertIn("http_im_oauth_callback_guard", by_id)
        self.assertFalse(by_id["http_im_oauth_callback_guard"].required)
        self.assertIn("http://kb.example/auth/im/oauth/callback", by_id["http_im_oauth_callback_guard"].args)
        self.assertIn("/safe/p5.env", by_id["http_im_oauth_callback_guard"].args)
        self.assertIn("http_token_binding_page", by_id)
        self.assertFalse(by_id["http_token_binding_page"].required)
        self.assertIn("http://kb.example/auth/im/token-bindings/page", by_id["http_token_binding_page"].args)
        self.assertIn("http_token_binding_fallback_smoke", by_id)
        self.assertFalse(by_id["http_token_binding_fallback_smoke"].required)
        self.assertIn("http://kb.example/auth/im/token-bindings", by_id["http_token_binding_fallback_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["http_token_binding_fallback_smoke"].args)
        self.assertIn("http_token_revoke_auth_smoke", by_id)
        self.assertFalse(by_id["http_token_revoke_auth_smoke"].required)
        self.assertIn("http://kb.example/auth/im/token-bindings", by_id["http_token_revoke_auth_smoke"].args)
        self.assertIn("http://kb.example/auth/im/current-user/status", by_id["http_token_revoke_auth_smoke"].args)
        self.assertIn("http://kb.example/auth/im/confirmations", by_id["http_token_revoke_auth_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["http_token_revoke_auth_smoke"].args)
        self.assertIn("http_explicit_confirmation_reasons_smoke", by_id)
        self.assertFalse(by_id["http_explicit_confirmation_reasons_smoke"].required)
        self.assertIn(
            "http://kb.example/auth/im/confirmations/request",
            by_id["http_explicit_confirmation_reasons_smoke"].args,
        )
        self.assertIn(
            "http://kb.example/auth/im/token-bindings",
            by_id["http_explicit_confirmation_reasons_smoke"].args,
        )
        self.assertIn("/safe/p5.env", by_id["http_explicit_confirmation_reasons_smoke"].args)
        self.assertIn("http_confirmation_response_auth_smoke", by_id)
        self.assertFalse(by_id["http_confirmation_response_auth_smoke"].required)
        self.assertIn(
            "http://kb.example/auth/im/confirmations",
            by_id["http_confirmation_response_auth_smoke"].args,
        )
        self.assertIn(
            "http://kb.example/auth/im/token-bindings",
            by_id["http_confirmation_response_auth_smoke"].args,
        )
        self.assertIn("/safe/p5.env", by_id["http_confirmation_response_auth_smoke"].args)
        self.assertIn("http_confirmation_response_summary_guard_smoke", by_id)
        self.assertFalse(by_id["http_confirmation_response_summary_guard_smoke"].required)
        self.assertIn(
            "http://kb.example/auth/im/confirmations",
            by_id["http_confirmation_response_summary_guard_smoke"].args,
        )
        self.assertIn(
            "http://kb.example/auth/im/token-bindings",
            by_id["http_confirmation_response_summary_guard_smoke"].args,
        )
        self.assertIn("/safe/p5.env", by_id["http_confirmation_response_summary_guard_smoke"].args)
        self.assertIn("http_diagnose_confirmation_policy_smoke", by_id)
        self.assertFalse(by_id["http_diagnose_confirmation_policy_smoke"].required)
        self.assertIn("http://kb.example/diagnose", by_id["http_diagnose_confirmation_policy_smoke"].args)
        self.assertIn("http://kb.example/auth/im/token-bindings", by_id["http_diagnose_confirmation_policy_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["http_diagnose_confirmation_policy_smoke"].args)
        self.assertIn("http_webhook_confirmation_policy_smoke", by_id)
        self.assertFalse(by_id["http_webhook_confirmation_policy_smoke"].required)
        self.assertIn("http://kb.example/diagnose/webhook/code_review", by_id["http_webhook_confirmation_policy_smoke"].args)
        self.assertIn("http://kb.example/auth/im/token-bindings", by_id["http_webhook_confirmation_policy_smoke"].args)
        self.assertIn("/safe/p5.env", by_id["http_webhook_confirmation_policy_smoke"].args)
        self.assertIn("http_im_configure_page", by_id)
        self.assertFalse(by_id["http_im_configure_page"].required)
        self.assertIn("http://kb.example/auth/im/configure/page", by_id["http_im_configure_page"].args)
        self.assertIn("http_im_configure_guard", by_id)
        self.assertFalse(by_id["http_im_configure_guard"].required)
        self.assertIn("http://kb.example/auth/im/configure", by_id["http_im_configure_guard"].args)
        self.assertIn("http_im_configure_plan", by_id)
        self.assertFalse(by_id["http_im_configure_plan"].required)
        self.assertIn("http://kb.example/auth/im/configure", by_id["http_im_configure_plan"].args)
        self.assertIn("/safe/p5.env", by_id["http_im_configure_plan"].args)
        self.assertIn("http_external_inputs_page", by_id)
        self.assertFalse(by_id["http_external_inputs_page"].required)
        self.assertIn("http://kb.example/diagnose/external-inputs/page", by_id["http_external_inputs_page"].args)
        self.assertIn("http_external_inputs_markdown", by_id)
        self.assertFalse(by_id["http_external_inputs_markdown"].required)
        self.assertIn("http://kb.example/diagnose/external-inputs.md", by_id["http_external_inputs_markdown"].args)
        self.assertIn("http_final_verification_guide", by_id)
        self.assertFalse(by_id["http_final_verification_guide"].required)
        self.assertIn("http://kb.example/diagnose/final-verification", by_id["http_final_verification_guide"].args)
        self.assertIn("http_final_verification_page", by_id)
        self.assertFalse(by_id["http_final_verification_page"].required)
        self.assertIn("http://kb.example/diagnose/final-verification/page", by_id["http_final_verification_page"].args)
        self.assertIn("http_current_user_smoke", by_id)
        self.assertFalse(by_id["http_current_user_smoke"].required)
        self.assertEqual(by_id["http_current_user_smoke"].timeout_seconds, 0)
        self.assertIn("CODEKB_USER_AUTH_TOKEN", by_id["http_current_user_smoke"].skip_reason)
        self.assertIn("http_confirmation_request", by_id)
        self.assertFalse(by_id["http_confirmation_request"].required)
        self.assertEqual(by_id["http_confirmation_request"].timeout_seconds, 0)
        self.assertIn("CODEKB_USER_AUTH_TOKEN", by_id["http_confirmation_request"].skip_reason)

    def test_http_current_user_checks_run_with_token_without_exposing_token_in_command(self):
        original_token = os.environ.get("CODEKB_USER_AUTH_TOKEN")
        os.environ["CODEKB_USER_AUTH_TOKEN"] = "secret-user-token"
        try:
            commands = build_p5_final_verification_commands(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
        finally:
            if original_token is None:
                os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
            else:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        by_id = {command.id: command for command in commands}
        smoke_command_text = " ".join(by_id["http_current_user_smoke"].args)
        request_command_text = " ".join(by_id["http_confirmation_request"].args)

        self.assertFalse(by_id["http_current_user_smoke"].required)
        self.assertGreater(by_id["http_current_user_smoke"].timeout_seconds, 0)
        self.assertIn("http://kb.example/auth/im/current-user/smoke", by_id["http_current_user_smoke"].args)
        self.assertIn("CODEKB_USER_AUTH_TOKEN", smoke_command_text)
        self.assertNotIn("secret-user-token", smoke_command_text)
        self.assertFalse(by_id["http_confirmation_request"].required)
        self.assertGreater(by_id["http_confirmation_request"].timeout_seconds, 0)
        self.assertIn(
            "http://kb.example/auth/im/confirmations/request",
            by_id["http_confirmation_request"].args,
        )
        self.assertIn("CODEKB_USER_AUTH_TOKEN", request_command_text)
        self.assertNotIn("secret-user-token", request_command_text)

    def test_mcp_auth_error_fallback_command_validates_error_payload(self):
        commands = build_p5_final_verification_commands(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example/",
            include_slow=False,
            include_http=False,
            include_worker=False,
        )
        command = {item.id: item for item in commands}["mcp_auth_error_fallback"]
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"

        completed = subprocess.run(
            list(command.args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["reason"], "missing_auth_token")
        self.assertEqual(payload["setup_url"], "http://kb.example/auth/im/mcp/setup")
        self.assertEqual(payload["token_binding_page_url"], "http://kb.example/auth/im/token-bindings/page")
        self.assertTrue(payload["has_oauth_login"])
        self.assertFalse(payload["secret_values_written"])

    def test_mcp_token_store_static_reject_command_validates_current_user_auth_strategy(self):
        commands = build_p5_final_verification_commands(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example/",
            include_slow=False,
            include_http=False,
            include_worker=False,
        )
        command = {item.id: item for item in commands}["mcp_token_store_static_reject"]
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"

        completed = subprocess.run(
            list(command.args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "validated")
        self.assertTrue(payload["static_token_rejected"])
        self.assertTrue(payload["bound_token_accepted"])
        self.assertTrue(payload["token_store_configured"])
        self.assertTrue(payload["static_token_configured"])
        self.assertTrue(payload["response_masks_tokens"])
        self.assertFalse(payload["secret_values_written"])

    def test_mcp_static_token_default_reject_command_validates_default_policy(self):
        commands = build_p5_final_verification_commands(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example/",
            include_slow=False,
            include_http=False,
            include_worker=False,
        )
        command = {item.id: item for item in commands}["mcp_static_token_default_reject"]
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"

        completed = subprocess.run(
            list(command.args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["reason"], "current_user_token_store_required")
        self.assertTrue(payload["static_token_configured"])
        self.assertFalse(payload["static_token_allowed"])
        self.assertFalse(payload["token_store_configured"])
        self.assertEqual(payload["setup_url"], "http://kb.example/auth/im/mcp/setup")
        self.assertEqual(payload["token_binding_page_url"], "http://kb.example/auth/im/token-bindings/page")
        self.assertTrue(payload["response_masks_token"])
        self.assertFalse(payload["secret_values_written"])

    def test_mcp_explicit_confirmation_reasons_command_validates_current_user_targets(self):
        commands = build_p5_final_verification_commands(
            env_file="/safe/p5.env",
            api_base_url="http://kb.example/",
            include_slow=False,
            include_http=False,
            include_worker=False,
        )
        command = {item.id: item for item in commands}["mcp_explicit_confirmation_reasons"]
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"

        completed = subprocess.run(
            list(command.args),
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["reasons"], ["interaction_complete", "problem_solved"])
        self.assertEqual(payload["records_count"], 2)
        self.assertTrue(payload["outbox_masks_tokens"])
        self.assertFalse(payload["secret_values_written"])
        for result in payload["results"]:
            self.assertTrue(result["confirmation_id"])
            self.assertTrue(result["target_is_current_user_token"])
            self.assertFalse(result["target_is_legacy_interface"])
            self.assertFalse(result["full_hash_exposed"])
            self.assertTrue(result["payload_keeps_context"])

    def test_token_binding_fallback_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_token_binding_fallback_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_token_revoke_auth_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_token_revoke_auth_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_diagnose_confirmation_policy_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_diagnose_confirmation_policy_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_explicit_confirmation_reasons_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_explicit_confirmation_reasons_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_explicit_confirmation_reasons_smoke_ignores_historical_outbox_records(self):
        import hashlib
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / "p5.env"
            store_path = tmp_path / "tokens.json"
            outbox_path = tmp_path / "confirmation.jsonl"
            outbox_path.write_text(
                json.dumps(
                    {
                        "confirmation_id": "historical-confirmation",
                        "reason": "problem_solved",
                        "payload": {"source": "p5_final_verify_http_explicit_confirmation"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            env_file.write_text(
                "\n".join(
                    [
                        "CODEKB_AUTH_ADMIN_TOKEN=admin-token",
                        f"CODEKB_USER_TOKEN_STORE={store_path}",
                        f"CODEKB_USER_CONFIRMATION_OUTBOX={outbox_path}",
                    ]
                ),
                encoding="utf-8",
            )
            issued_tokens = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    length = int(self.headers.get("content-length", "0") or "0")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if self.path == "/auth/im/token-bindings":
                        if self.headers.get("X-CodeKB-Admin-Token") != "admin-token":
                            self.send_response(401)
                            self.end_headers()
                            return
                        token = f"issued-token-{len(issued_tokens) + 1}"
                        issued_tokens.append(token)
                        self._send_json({"status": "issued", "token": token, "binding": {"token_id": token}})
                        return
                    if self.path == "/auth/im/confirmations/request":
                        token = payload["auth_token"]
                        reason = payload["reason"]
                        confirmation_id = f"new-{len(issued_tokens)}-{reason}"
                        prefix = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
                        confirmation = {
                            "confirmation_id": confirmation_id,
                            "reason": reason,
                            "target_user_token_hash_prefix": prefix,
                            "payload": payload.get("payload") or {},
                        }
                        with outbox_path.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(confirmation, ensure_ascii=False) + "\n")
                        self._send_json({"status": "queued", "confirmation": confirmation})
                        return
                    self.send_response(404)
                    self.end_headers()

                def _send_json(self, payload):
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                commands = build_p5_final_verification_commands(
                    env_file=str(env_file),
                    api_base_url=base_url,
                    include_slow=False,
                    include_http=True,
                    include_worker=False,
                )
                command = {item.id: item for item in commands}["http_explicit_confirmation_reasons_smoke"]
                env = dict(os.environ)
                env["PYTHONPATH"] = "src"

                completed = subprocess.run(
                    list(command.args),
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    env=env,
                    check=False,
                )
                payload = json.loads(completed.stdout)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(payload["records_count"], 2)
        self.assertEqual(payload["reasons"], ["interaction_complete", "problem_solved"])
        self.assertTrue(payload["outbox_masks_tokens"])
        self.assertTrue(payload["response_masks_tokens"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_confirmation_response_auth_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_confirmation_response_auth_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_confirmation_response_summary_guard_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_confirmation_response_summary_guard_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_webhook_confirmation_policy_smoke_command_skips_without_admin_or_webhook_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_webhook_confirmation_policy_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])


    def test_webhook_token_guard_command_skips_without_webhook_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_webhook_token_guard"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_im_oauth_callback_guard_skips_without_state_secret(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_im_oauth_callback_guard"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_IM_OAUTH_STATE_SECRET", payload["reason"])
        self.assertFalse(payload["secret_values_written"])


    def test_webhook_sample_import_smoke_command_skips_without_admin_token(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            commands = build_p5_final_verification_commands(
                env_file=str(env_file),
                api_base_url="http://kb.example/",
                include_slow=False,
                include_http=True,
                include_worker=False,
            )
            command = {item.id: item for item in commands}["http_webhook_sample_import_smoke"]
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                list(command.args),
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("CODEKB_AUTH_ADMIN_TOKEN", payload["reason"])
        self.assertFalse(payload["secret_values_written"])

    def test_http_current_user_results_map_to_passed_when_token_is_available(self):
        original_token = os.environ.get("CODEKB_USER_AUTH_TOKEN")
        os.environ["CODEKB_USER_AUTH_TOKEN"] = "secret-user-token"
        try:
            def runner(args, timeout_seconds, env):
                joined = " ".join(args)
                self.assertNotIn("secret-user-token", joined)
                if "diagnose-acceptance" in joined or "/diagnose/acceptance" in joined:
                    return 0, json.dumps({"status": "accepted", "accepted": True}), ""
                if "diagnose-readiness" in joined or "/diagnose/readiness" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "diagnose-external-inputs" in joined:
                    return 0, json.dumps({"status": "complete", "pending_count": 0}), ""
                if "diagnose-p5-external-state" in joined or "/diagnose/external-state" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "diagnose-p5-handoff-bundle" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "bundle_status": "complete",
                            "has_webhook_diagnose": True,
                            "has_webhook_shared_token": True,
                            "has_confirmation_policy": True,
                            "has_current_user_token": True,
                            "has_no_interface_lookup": True,
                            "has_audit_exclusion": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "diagnose-webhook-sample-suite" in joined:
                    return 0, json.dumps({"status": "passed"}), ""
                if "/diagnose/webhook/code_review/validate" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "missing_token_http_401": True,
                            "valid_token_validated": True,
                            "response_masks_webhook_token": True,
                            "query_ready": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "/diagnose/webhook/code_review/sample-import" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "denied_without_admin": True,
                            "import_status": "imported",
                            "validation_status": "passed",
                            "raw_sensitive_values_detected": 2,
                            "response_masks_secrets": True,
                            "output_masks_secrets": True,
                            "output_is_active": False,
                            "secret_values_written": False,
                        }
                    ), ""
                if "mcp_server" in joined and "p5_mcp_marker" not in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "reason": "missing_auth_token",
                            "setup_url": "http://kb.example/auth/im/mcp/setup",
                            "token_binding_page_url": "http://kb.example/auth/im/token-bindings/page",
                            "has_oauth_login": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "diagnose-im-oauth-smoke" in joined:
                    return 0, json.dumps({"status": "verified"}), ""
                if "validate_im_delivery_configuration" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "valid_ok": True,
                            "missing_url_blocked": True,
                            "bad_agent_blocked": True,
                            "relative_url_blocked": True,
                            "fragment_url_blocked": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "diagnose-im-smoke" in joined:
                    return 0, json.dumps({"status": "validated"}), ""
                if "diagnose-current-user-smoke" in joined:
                    return 0, json.dumps({"status": "responded"}), ""
                if "/auth/im/mcp/setup/status" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "/auth/im/token-bindings/page" in joined:
                    return 0, "<html>token binding</html>", ""
                if "/auth/im/oauth/callback" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "invalid_state_http_400": True,
                            "token_store_unchanged": True,
                            "response_masks_state_secret": True,
                            "response_masks_bad_state": True,
                            "response_masks_bad_code": True,
                            "no_token_issued": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_explicit_confirmation" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "reasons": ["interaction_complete", "problem_solved"],
                            "records_count": 2,
                            "outbox_masks_tokens": True,
                            "response_masks_tokens": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_confirmation_response_summary" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "denied_without_admin_http_401": True,
                            "confirmation_id_present": True,
                            "response_recorded": True,
                            "summary_http_200": True,
                            "summary_contains_response": True,
                            "summary_decision": "confirmed",
                            "summary_masks_secrets": True,
                            "files_mask_token_and_route": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_confirmation_response" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "confirmation_id_present": True,
                            "target_is_current_user_token": True,
                            "pending_before_count": 1,
                            "other_pending_count": 0,
                            "other_detail_http_401": True,
                            "target_detail_ok": True,
                            "other_response_http_401": True,
                            "target_response_recorded": True,
                            "pending_after_count": 0,
                            "include_responded_status": "responded",
                            "response_masks_tokens": True,
                            "response_masks_routes": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_token_revoke" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "token_issued": True,
                            "token_id_present": True,
                            "status_before_http_200": True,
                            "confirmation_before_revoke_queued": True,
                            "revoke_http_200": True,
                            "status_after_http_401": True,
                            "pending_after_http_401": True,
                            "request_after_http_401": True,
                            "detail_after_http_401": True,
                            "response_after_http_401": True,
                            "response_masks_secrets": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_diagnose_confirmation" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "invalid_token_http_401": True,
                            "diagnosis_returned": True,
                            "confirmation_queued": True,
                            "target_is_current_user_token": True,
                            "response_masks_token": True,
                            "outbox_hashes_token": True,
                            "outbox_masks_route": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_final_verify_http_webhook_confirmation" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "invalid_user_token_http_401": True,
                            "diagnosis_returned": True,
                            "confirmation_queued": True,
                            "target_is_current_user_token": True,
                            "response_outbox_log_masks_secrets": True,
                            "webhook_log_excludes_confirmation_args": True,
                            "outbox_hashes_token": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "p5_static" in joined and "default" in joined and "reject" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "reason": "current_user_token_store_required",
                            "static_token_configured": True,
                            "static_token_allowed": False,
                            "token_store_configured": False,
                            "response_masks_token": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "interaction_complete" in joined and "problem_solved" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "reasons": ["interaction_complete", "problem_solved"],
                            "records_count": 2,
                            "outbox_masks_tokens": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "/auth/im/token-bindings" in joined:
                    return 0, json.dumps(
                        {
                            "status": "validated",
                            "issue_without_admin_http_401": True,
                            "summary_without_admin_http_401": True,
                            "revoke_without_admin_http_401": True,
                            "summary_with_admin_http_200": True,
                            "revoke_with_admin_http_200": True,
                            "token_returned_once": True,
                            "token_id_present": True,
                            "derived_user_hash_len": 64,
                            "summary_total_at_least_one": True,
                            "response_masks_im_userid": True,
                            "response_masks_admin_token": True,
                            "store_keeps_token_hash_only": True,
                            "metadata_has_route_hash": True,
                            "secret_values_written": False,
                        }
                    ), ""
                if "/diagnose/final-verification/page" in joined:
                    return 0, "<html>ok</html>", ""
                if "/diagnose/final-verification" in joined:
                    return 0, json.dumps({"status": "pending_external_inputs", "secret_values_written": False}), ""
                if "/auth/im/current-user/smoke" in joined:
                    self.assertEqual(env["CODEKB_USER_AUTH_TOKEN"], "secret-user-token")
                    return 0, json.dumps({"status": "responded", "ok": True}), ""
                if "/auth/im/confirmations/request" in joined:
                    self.assertEqual(env["CODEKB_USER_AUTH_TOKEN"], "secret-user-token")
                    return 0, json.dumps({"status": "queued", "confirmation": {"confirmation_id": "c1"}}), ""
                return 0, "ok", ""

            report = run_p5_final_verification(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example",
                include_slow=False,
                include_http=True,
                include_worker=False,
                runner=runner,
            )
        finally:
            if original_token is None:
                os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
            else:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token
        by_id = {result["id"]: result for result in report["results"]}
        raw = json.dumps(report, ensure_ascii=False)

        self.assertEqual(by_id["http_current_user_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_confirmation_request"]["status"], "passed")
        self.assertEqual(by_id["mcp_auth_error_fallback"]["status"], "passed")
        self.assertEqual(by_id["mcp_static_token_default_reject"]["status"], "passed")
        self.assertEqual(by_id["mcp_token_store_static_reject"]["status"], "passed")
        self.assertEqual(by_id["mcp_explicit_confirmation_reasons"]["status"], "passed")
        self.assertEqual(by_id["handoff_bundle_smoke"]["status"], "passed")
        self.assertEqual(by_id["im_delivery_config_validation"]["status"], "passed")
        self.assertEqual(by_id["http_webhook_token_guard"]["status"], "passed")
        self.assertEqual(by_id["http_webhook_sample_import_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_im_oauth_callback_guard"]["status"], "passed")
        self.assertEqual(by_id["http_token_binding_page"]["status"], "passed")
        self.assertEqual(by_id["http_token_binding_fallback_smoke"]["status"], "passed")
        token_binding_json = by_id["http_token_binding_fallback_smoke"]["json"]
        self.assertTrue(token_binding_json["issue_without_admin_http_401"])
        self.assertTrue(token_binding_json["summary_without_admin_http_401"])
        self.assertTrue(token_binding_json["revoke_without_admin_http_401"])
        self.assertTrue(token_binding_json["summary_with_admin_http_200"])
        self.assertTrue(token_binding_json["revoke_with_admin_http_200"])
        self.assertTrue(token_binding_json["summary_total_at_least_one"])
        self.assertEqual(by_id["http_token_revoke_auth_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_explicit_confirmation_reasons_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_confirmation_response_auth_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_confirmation_response_summary_guard_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_diagnose_confirmation_policy_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_webhook_confirmation_policy_smoke"]["status"], "passed")
        self.assertEqual(by_id["http_final_verification_guide"]["status"], "passed")
        self.assertNotIn("secret-user-token", raw)

    def test_user_token_is_only_inherited_by_current_user_smoke_commands(self):
        original_token = os.environ.get("CODEKB_USER_AUTH_TOKEN")
        os.environ["CODEKB_USER_AUTH_TOKEN"] = "secret-user-token"
        env_by_command = {}
        try:
            def runner(args, timeout_seconds, env):
                joined = " ".join(args)
                if "unittest" in joined:
                    env_by_command["unit_tests"] = dict(env)
                    return 0, "ok", ""
                if "diagnose-im-smoke" in joined:
                    env_by_command["im_smoke"] = dict(env)
                    return 0, json.dumps({"status": "validated"}), ""
                if "diagnose-current-user-smoke" in joined:
                    env_by_command["current_user_smoke"] = dict(env)
                    return 0, json.dumps({"status": "responded"}), ""
                if "diagnose-acceptance" in joined:
                    return 0, json.dumps({"status": "accepted", "accepted": True}), ""
                if "diagnose-readiness" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "diagnose-external-inputs" in joined:
                    return 0, json.dumps({"status": "complete", "pending_count": 0}), ""
                if "diagnose-p5-external-state" in joined:
                    return 0, json.dumps({"status": "ready"}), ""
                if "diagnose-webhook-sample-suite" in joined:
                    return 0, json.dumps({"status": "passed"}), ""
                if "diagnose-im-oauth-smoke" in joined:
                    return 0, json.dumps({"status": "verified"}), ""
                return 0, "ok", ""

            run_p5_final_verification(
                env_file="/safe/p5.env",
                api_base_url="http://kb.example",
                include_slow=True,
                include_http=False,
                include_worker=False,
                runner=runner,
            )
        finally:
            if original_token is None:
                os.environ.pop("CODEKB_USER_AUTH_TOKEN", None)
            else:
                os.environ["CODEKB_USER_AUTH_TOKEN"] = original_token

        self.assertNotIn("CODEKB_USER_AUTH_TOKEN", env_by_command["unit_tests"])
        self.assertEqual(env_by_command["im_smoke"]["CODEKB_USER_AUTH_TOKEN"], "secret-user-token")
        self.assertEqual(env_by_command["current_user_smoke"]["CODEKB_USER_AUTH_TOKEN"], "secret-user-token")

    def test_write_final_verification_report_uses_0600(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "logs" / "p5-final-verify-report.json"
            report = {"status": "pending_external_inputs", "secret_values_written": False}
            write_p5_final_verification_report(output, report)
            loaded = json.loads(output.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(output.stat().st_mode)

        self.assertEqual(loaded["status"], "pending_external_inputs")
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
