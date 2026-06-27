import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.diagnosis_acceptance import build_p5_external_input_plan
from codekb.diagnosis_readiness import build_p5_readiness_report
from codekb.p5_external_input_page import render_p5_external_input_page


ROOT = Path(__file__).resolve().parents[1]


class P5ExternalInputPageTests(unittest.TestCase):
    def test_page_renders_current_user_strategy_without_secret_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            readiness = build_p5_readiness_report(
                fixture_path=str(ROOT / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(ROOT / "data" / "entity_aliases.yaml"),
                registry_path=str(ROOT / "docs" / "kb-registry.draft.yaml"),
                governance_policy_path=str(ROOT / "docs" / "governance-policy.draft.yaml"),
                diagnose_webhook_mapping_path=str(ROOT / "docs" / "diagnose-webhook-mapping.draft.yaml"),
                diagnose_webhook_samples_path=str(ROOT / "docs" / "diagnose-webhook-samples.draft.yaml"),
                user_token_store_path=str(root / "tokens.json"),
                user_confirmation_outbox_path=str(root / "confirmation.jsonl"),
                user_confirmation_responses_path=str(root / "responses.jsonl"),
                env={"CODEKB_IM_APP_SECRET": "app-secret-value"},
            )
            plan = build_p5_external_input_plan(
                readiness,
                api_base_url="http://kb.example",
                env_file="/safe/p5.env",
                external_state_report={
                    "status": "pending_external_inputs",
                    "ok": False,
                    "pending_checks": ["im_template"],
                    "paths": {"im_template": "/safe/im-config.todo.env"},
                    "checks": [
                        {
                            "id": "im_template",
                            "status": "pending",
                            "message": "template missing keys",
                            "missing_keys": ["CODEKB_IM_CORP_ID"],
                        }
                    ],
                },
            )
            html = render_p5_external_input_page(plan)

        raw = json.dumps(plan, ensure_ascii=False) + html
        self.assertIn("Current User Auth Strategy", html)
        self.assertIn("Operator Handoff", html)
        self.assertIn("Tasks By Owner", html)
        self.assertIn("Completion Criteria", html)
        self.assertIn("External state", html)
        self.assertIn("State checks", html)
        self.assertIn("current_authenticated_user", html)
        self.assertIn("Interface-person lookup", html)
        self.assertIn("im_template", raw)
        self.assertIn("im_admin", raw)
        self.assertIn("All external input tasks are resolved", raw)
        self.assertIn("/safe/im-config.todo.env", raw)
        self.assertIn("diagnose-current-user-smoke", html)
        self.assertIn("/auth/im/confirmations/request", html)
        self.assertIn("CODEKB_IM_APP_SECRET", html)
        self.assertIn("http://kb.example/diagnose/external-inputs.md", raw)
        self.assertIn("http://kb.example/diagnose/external-inputs/page", raw)
        self.assertIn("http://kb.example/diagnose/final-verification/page", raw)
        self.assertIn("http://kb.example/diagnose/final-verification", raw)
        self.assertIn("http://kb.example/auth/im/self-bindings/page", raw)
        self.assertIn("http://kb.example/auth/im/token-bindings/page", raw)
        self.assertIn("http://kb.example/auth/im/confirmations/page", raw)
        self.assertIn("http://kb.example/demo/current-user", raw)
        self.assertIn("http://kb.example/demo/webhook", raw)
        self.assertNotIn("app-secret-value", raw)

    def test_page_escapes_dynamic_task_fields(self):
        plan = {
            "status": "pending_external_inputs",
            "readiness_status": "ready_with_warnings",
            "pending_count": 1,
            "setup_url": "http://kb.example/auth/im/mcp/setup",
            "setup_status_url": "http://kb.example/auth/im/mcp/setup/status",
            "external_inputs_markdown_url": "http://kb.example/diagnose/external-inputs.md?<bad>",
            "current_user_demo_url": "http://kb.example/demo/current-user?<bad>",
            "webhook_demo_url": "http://kb.example/demo/webhook?<bad>",
            "current_user_smoke_url": "http://kb.example/auth/im/current-user/smoke",
            "mcp_auth_strategy": {
                "current_user_auth_required": True,
                "setup_page_required": True,
                "auth_token_argument": "auth_token",
                "confirmation_target": "current_authenticated_user",
                "interface_person_lookup_enabled": False,
            },
            "operator_handoff": {
                "ordered_task_ids": ["mcp_auth"],
                "by_owner": {"<owner>": ["mcp_auth"]},
                "next_action": {
                    "check_id": "mcp_auth",
                    "title": "<b>next</b>",
                    "owner": "<owner>",
                    "evidence_needed": "token <required>",
                    "safe_commands": ["curl '<next>'"],
                    "verification_commands": ["curl '<verify-next>'"],
                },
                "completion_criteria": ["finish <all>"],
            },
            "tasks": [
                {
                    "title": "<script>alert(1)</script>",
                    "check_id": "mcp_auth",
                    "owner": "im",
                    "status": "warn",
                    "evidence_needed": "token",
                    "remediation": "open setup",
                    "required_inputs": ["CODEKB_IM_APP_SECRET"],
                    "safe_commands": ["curl '<unsafe>'"],
                    "verification_commands": [],
                    "notes": ["do not paste <secret>"],
                }
            ],
            "final_verification": [{"id": "verify", "command": "curl '<verify>'"}],
        }

        html = render_p5_external_input_page(plan)

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("curl '&lt;unsafe&gt;'", html)
        self.assertIn("curl '&lt;verify&gt;'", html)
        self.assertIn("&lt;b&gt;next&lt;/b&gt;", html)
        self.assertIn("curl '&lt;next&gt;'", html)
        self.assertIn("finish &lt;all&gt;", html)
        self.assertIn("external-inputs.md?&lt;bad&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main()
