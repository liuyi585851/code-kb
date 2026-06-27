import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.diagnosis_readiness import build_p5_readiness_report
from codekb.p5_external_state import build_p5_external_state
from codekb.p5_final_verification_guide import (
    build_p5_final_verification_guide,
    render_p5_final_verification_page,
)


ROOT = Path(__file__).resolve().parents[1]


class P5FinalVerificationGuideTests(unittest.TestCase):
    def test_guide_lists_post_config_phases_without_secret_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "p5.env"
            env_file.write_text(
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n",
                encoding="utf-8",
            )
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
            external_state = build_p5_external_state(
                env_file=str(env_file),
                im_template=str(root / "im-config.todo.env"),
                token_store=str(root / "tokens.json"),
                real_samples=str(root / "samples.real.yaml"),
            )
            guide = build_p5_final_verification_guide(
                readiness,
                external_state,
                api_base_url="http://kb.example",
                env_file=str(env_file),
            )
            html = render_p5_final_verification_page(guide)

        raw = json.dumps(guide, ensure_ascii=False) + html
        phase_ids = {phase["id"] for phase in guide["phases"]}
        self.assertEqual(guide["status"], "pending_external_inputs")
        self.assertFalse(guide["accepted"])
        self.assertIn("im_oauth", phase_ids)
        self.assertIn("current_user_auth", phase_ids)
        self.assertIn("im_delivery", phase_ids)
        self.assertIn("final_gate", phase_ids)
        self.assertIn("im_template", {task["check_id"] for task in guide["pending_tasks"]})
        self.assertIn("operator_handoff", guide)
        ordered_task_ids = guide["operator_handoff"]["ordered_task_ids"]
        self.assertEqual(guide["operator_handoff"]["next_action"]["check_id"], ordered_task_ids[0])
        self.assertLess(ordered_task_ids.index("im_template"), ordered_task_ids.index("im_oauth"))
        self.assertLess(ordered_task_ids.index("im_oauth"), ordered_task_ids.index("mcp_auth"))
        self.assertIn("im_admin", guide["operator_handoff"]["by_owner"])
        self.assertIn("Completion Criteria", html)
        self.assertIn("Operator Handoff", html)
        self.assertIn("All external input tasks are resolved", raw)
        self.assertIn("http://kb.example/diagnose/final-verification/page", raw)
        self.assertIn("http://kb.example/auth/im/token-bindings/page", raw)
        self.assertIn("diagnose-p5-final-verify", raw)
        self.assertIn("current_authenticated_user", raw)
        self.assertIn("interface_person_lookup_enabled", raw)
        self.assertIn("CODEKB_IM_APP_SECRET", raw)
        self.assertNotIn("app-secret-value", raw)
        self.assertNotIn("state-secret-value", raw)

    def test_page_escapes_dynamic_fields(self):
        guide = {
            "status": "pending_external_inputs",
            "readiness_status": "<ready>",
            "accepted": False,
            "pending_count": 1,
            "links": {
                "im_configure_page": "http://kb.example/config?<bad>",
                "setup": "http://kb.example/setup",
                "setup_status": "http://kb.example/status",
                "external_inputs_page": "http://kb.example/inputs",
                "final_verification": "http://kb.example/final",
            },
            "mcp_auth_strategy": {
                "current_user_auth_required": True,
                "auth_token_argument": "auth_token",
                "confirmation_target": "current_authenticated_user",
                "interface_person_lookup_enabled": False,
            },
            "operator_handoff": {
                "ordered_task_ids": ["im_oauth"],
                "by_owner": {"<owner>": ["im_oauth"]},
                "next_action": {
                    "check_id": "im_oauth",
                    "title": "<b>next</b>",
                    "owner": "<owner>",
                    "evidence_needed": "secret <value>",
                    "safe_commands": ["curl '<next>'"],
                    "verification_commands": ["curl '<verify-next>'"],
                },
                "completion_criteria": ["finish <all>"],
            },
            "external_state": {
                "checks": [
                    {
                        "id": "<script>alert(1)</script>",
                        "status": "pending",
                        "message": "needs <secret>",
                        "missing_keys": ["CODEKB_IM_APP_SECRET"],
                    }
                ]
            },
            "phases": [
                {
                    "id": "phase",
                    "title": "<b>phase</b>",
                    "status": "pending",
                    "commands": ["curl '<unsafe>'"],
                }
            ],
            "pending_tasks": [
                {
                    "title": "<b>task</b>",
                    "check_id": "im_oauth",
                    "owner": "admin",
                    "status": "warn",
                    "evidence_needed": "secret <value>",
                }
            ],
            "final_verification": [{"id": "verify", "command": "curl '<verify>'"}],
        }

        html = render_p5_final_verification_page(guide)

        self.assertIn("&lt;ready&gt;", html)
        self.assertIn("http://kb.example/config?&lt;bad&gt;", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("curl '&lt;unsafe&gt;'", html)
        self.assertIn("curl '&lt;verify&gt;'", html)
        self.assertIn("&lt;b&gt;next&lt;/b&gt;", html)
        self.assertIn("curl '&lt;next&gt;'", html)
        self.assertIn("finish &lt;all&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main()
