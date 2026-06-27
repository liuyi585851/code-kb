import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.current_user_smoke import run_current_user_smoke
from codekb.user_auth import JsonUserTokenStore


class CurrentUserSmokeTests(unittest.TestCase):
    def test_current_user_smoke_validates_route_and_records_response_without_raw_secrets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root, route_user="ww-user")

            report = run_current_user_smoke(
                auth_token=issued["token"],
                token_store_path=str(token_store),
                confirmation_outbox_path=str(root / "outbox.jsonl"),
                confirmation_responses_path=str(root / "responses.jsonl"),
                delivery_report_path=str(root / "delivery-report.json"),
                delivery_log_path=str(root / "delivery.jsonl"),
                fixture_path="data/fixtures/sample_corpus.jsonl",
                candidate_store_path=str(root / "candidates.json"),
                pending_docs_dir=str(root / "pending-docs"),
                respond=True,
            )
            raw_output = json.dumps(report, ensure_ascii=False)
            raw_outbox = (root / "outbox.jsonl").read_text(encoding="utf-8")
            raw_delivery_report = (root / "delivery-report.json").read_text(encoding="utf-8")
            raw_responses = (root / "responses.jsonl").read_text(encoding="utf-8")

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "responded")
        self.assertEqual(report["delivery"]["result"]["status"], "validated")
        self.assertEqual(report["response"]["decision"], "confirmed")
        self.assertTrue(report["diagnosis"]["diagnosis_id"])
        self.assertNotIn(issued["token"], raw_output + raw_outbox + raw_delivery_report + raw_responses)
        self.assertNotIn("ww-user", raw_output + raw_outbox + raw_delivery_report + raw_responses)
        self.assertIn("im_userid_hash", report["auth"]["metadata"])

    def test_current_user_smoke_reports_missing_im_route(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root, route_user="")

            report = run_current_user_smoke(
                auth_token=issued["token"],
                token_store_path=str(token_store),
                confirmation_outbox_path=str(root / "outbox.jsonl"),
                confirmation_responses_path=str(root / "responses.jsonl"),
                fixture_path="data/fixtures/sample_corpus.jsonl",
                candidate_store_path=str(root / "candidates.json"),
                pending_docs_dir=str(root / "pending-docs"),
            )

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "blocked_missing_route")
        self.assertEqual(report["delivery"]["result"]["status"], "blocked_missing_route")

    def test_cli_current_user_smoke_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_store, issued = _issue_token(root, route_user="ww-user")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-current-user-smoke",
                        "--auth-token",
                        issued["token"],
                        "--token-store",
                        str(token_store),
                        "--confirmation-outbox",
                        str(root / "outbox.jsonl"),
                        "--confirmation-responses",
                        str(root / "responses.jsonl"),
                        "--delivery-report",
                        str(root / "delivery-report.json"),
                        "--delivery-log",
                        str(root / "delivery.jsonl"),
                        "--fixtures",
                        "data/fixtures/sample_corpus.jsonl",
                        "--candidate-store",
                        str(root / "candidates.json"),
                        "--pending-docs-dir",
                        str(root / "pending-docs"),
                        "--respond",
                        "--json",
                    ]
                )
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "responded")
        self.assertNotIn(issued["token"], stdout.getvalue())
        self.assertNotIn("ww-user", stdout.getvalue())


def _issue_token(root: Path, *, route_user: str):
    token_store = root / "tokens.json"
    metadata = {"source": "test"}
    if route_user:
        metadata["im_userid"] = route_user
    issued = JsonUserTokenStore(token_store).issue(
        user_id_hash="u_hash",
        display_name="User",
        scopes=["diagnose"],
        metadata=metadata,
    )
    return token_store, issued


if __name__ == "__main__":
    unittest.main()
