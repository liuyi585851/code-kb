import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.candidate import JsonCandidateStore
from codekb.cli import main
from codekb.feedback import JsonlFeedbackStore
from codekb.governance import (
    build_curator_weekly_report,
    build_governance_report,
    build_governance_ticket_plans,
    governance_state_ticketed_item_ids,
    load_governance_policy,
    load_governance_state,
    process_governance_ticket_outbox,
    summarize_governance_state,
    sync_governance_state,
    sync_governance_state_from_ticket_results,
    write_curator_weekly_report,
    write_governance_ticket_outbox,
)
from codekb.governance_artifacts import export_governance_artifacts


class GovernanceReportTests(unittest.TestCase):
    def test_reports_stale_owner_and_missing_registry_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)

            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )

            item_types = {item.item_type for item in report.items}
            self.assertIn("stale_source", item_types)
            self.assertIn("owner_inactive", item_types)
            self.assertIn("owner_missing", item_types)
            self.assertIn("source_missing", item_types)
            self.assertEqual(report.source_documents, 2)
            self.assertEqual(report.counts_by_type["owner_inactive"], 1)
            self.assertTrue(any(owner.owner == "alice" and owner.status == "inactive" for owner in report.owners))

    def test_load_missing_governance_policy_is_empty(self):
        policy = load_governance_policy("missing-governance-policy.yaml")

        self.assertFalse(policy.exists)
        self.assertEqual(policy.owner_replacements, {})
        self.assertIsNone(policy.stale_after_days)

    def test_policy_overrides_thresholds_and_assignees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            policy_path = root / "policy.yaml"
            policy_path.write_text(
                """
version: 1
stale_after_days_by_sub_kb:
  release: 3650
owner_group_assignees:
  release_team: release_queue
owner_replacements:
  alice: release_owner
item_type_assignees:
  source_missing: curator_queue
""".strip()
                + "\n",
                encoding="utf-8",
            )

            report = build_governance_report(
                [manifest],
                registry_path=registry,
                policy_path=policy_path,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )

            items_by_type = {item.item_type: item for item in report.items}
            self.assertNotIn("stale_source", items_by_type)
            self.assertEqual(items_by_type["owner_inactive"].suggested_owner, "release_owner")
            self.assertEqual(items_by_type["owner_missing"].suggested_owner, "release_queue")
            self.assertEqual(items_by_type["source_missing"].suggested_owner, "curator_queue")
            self.assertEqual(report.to_dict()["policy_path"], str(policy_path))

    def test_reports_feedback_badcases_and_candidate_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            feedback_log = root / "feedback.jsonl"
            candidate_store = root / "candidates.json"
            JsonlFeedbackStore(feedback_log).append(
                answer_id="answer-1",
                trace_id="trace-1",
                rating=-1,
                reason="missing release step",
                corrected_answer="Use the release checklist.",
            )
            store = JsonCandidateStore(candidate_store, pending_docs_dir=root / "pending")
            store.submit(sub_kb_id="release", title="Same title", content="first")
            conflict = store.submit(sub_kb_id="release", title="Same title", content="second")

            report = build_governance_report(
                [manifest],
                registry_path=registry,
                feedback_log_path=feedback_log,
                candidate_store_path=candidate_store,
                pending_docs_dir=root / "pending",
                stale_after_days=3650,
                now="2026-06-11T00:00:00Z",
            )

            item_types = {item.item_type for item in report.items}
            self.assertIn("feedback_badcase", item_types)
            self.assertIn("candidate_conflict", item_types)
            conflict_items = [item for item in report.items if item.item_type == "candidate_conflict"]
            self.assertEqual(conflict_items[0].evidence["candidate_id"], conflict.candidate.candidate_id)

    def test_cli_writes_report_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            output = root / "governance-report.json"

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "governance-report",
                        "--fixtures",
                        str(manifest),
                        "--registry",
                        str(registry),
                        "--feedback-log",
                        str(root / "missing-feedback.jsonl"),
                        "--candidate-store",
                        str(root / "missing-candidates.json"),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--output",
                        str(output),
                        "--item-limit",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_documents"], 2)
            self.assertIn("items", payload)

    def test_builds_governance_ticket_plans_for_issue_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )

            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=2)

            self.assertEqual(len(plans), 2)
            self.assertEqual(plans[0].target, "issue_tracker")
            self.assertEqual(plans[0].operations[0].tool, "issue_tracker_create_ticket")
            self.assertIn("item_id", plans[0].description)

    def test_process_governance_ticket_outbox_dry_run_and_blocks_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            outbox = root / "outbox" / "tickets.jsonl"
            report_path = root / "ticket-report.json"

            written = write_governance_ticket_outbox(plans, outbox)
            dry_run = process_governance_ticket_outbox(outbox, report_path=report_path)
            blocked = process_governance_ticket_outbox(outbox, execute=True, write_enabled=False)

            self.assertEqual(written, 1)
            self.assertEqual(dry_run.status, "validated")
            self.assertEqual(dry_run.results[0].operations[0].status, "validated")
            self.assertTrue(report_path.exists())
            self.assertEqual(blocked.status, "blocked")
            self.assertEqual(blocked.results[0].operations[0].status, "blocked_write_disabled")

    def test_process_governance_ticket_outbox_executes_with_fake_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            outbox = root / "tickets.jsonl"
            client = _FakeTicketClient()

            write_governance_ticket_outbox(plans, outbox)
            result = process_governance_ticket_outbox(outbox, execute=True, write_enabled=True, client=client)

            self.assertEqual(result.status, "executed")
            self.assertEqual(result.executed_operations, 1)
            self.assertEqual(client.created[0]["priority"], plans[0].severity)

    def test_sync_governance_state_from_ticket_results_writes_back_real_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            state_path = root / "state" / "governance-state.json"
            # 先用计划中的(内部)工单初始化状态。
            sync_governance_state(report, state_path, ticket_plans=plans)

            outbox = root / "tickets.jsonl"
            write_governance_ticket_outbox(plans, outbox)
            client = _FakeTicketClient()
            exec_report = process_governance_ticket_outbox(
                outbox, execute=True, write_enabled=True, client=client
            )
            self.assertEqual(exec_report.status, "executed")

            writeback = sync_governance_state_from_ticket_results(exec_report, state_path)

            self.assertEqual(writeback["recorded_tickets"], 1)
            self.assertEqual(writeback["updated_items"], 1)
            self.assertEqual(writeback["recorded"][0]["external_ticket_id"], "issue_tracker-1")

            state = load_governance_state(state_path)
            plan = plans[0]
            item = next(item for item in state["items"] if item["item_id"] == plan.item_id)
            self.assertIn("issue_tracker-1", item["ticket_ids"])
            self.assertEqual(item["ticket_count"], len(item["ticket_ids"]))
            self.assertEqual(item["status"], "ticketed")
            ticket = next(t for t in state["tickets"] if t["ticket_id"] == plan.ticket_id)
            self.assertEqual(ticket["external_ref"], "issue_tracker-1")
            self.assertEqual(ticket["status"], "executed")

    def test_sync_governance_state_from_ticket_results_noop_when_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            state_path = root / "state" / "governance-state.json"
            sync_governance_state(report, state_path, ticket_plans=plans)
            before = state_path.read_text(encoding="utf-8")

            outbox = root / "tickets.jsonl"
            write_governance_ticket_outbox(plans, outbox)
            # 执行但禁用写入 -> 被拦截,不产生外部 id。
            blocked_report = process_governance_ticket_outbox(outbox, execute=True, write_enabled=False)
            writeback = sync_governance_state_from_ticket_results(blocked_report, state_path)

            self.assertEqual(writeback["recorded_tickets"], 0)
            self.assertEqual(writeback["updated_items"], 0)
            state = load_governance_state(state_path)
            for item in state["items"]:
                self.assertNotIn("issue_tracker-1", item.get("ticket_ids", []))

    def test_cli_governance_ticket_outbox_injects_client_only_when_executing_with_write(self):
        created = []

        def factory():
            client = _FakeTicketClient()
            created.append(client)
            return client

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            outbox = root / "tickets.jsonl"
            write_governance_ticket_outbox(plans, outbox)
            state_path = root / "state.json"
            sync_governance_state(report, state_path, ticket_plans=plans)
            base = [
                "governance-ticket-outbox",
                "--outbox",
                str(outbox),
                "--report",
                str(root / "report.json"),
                "--state",
                str(state_path),
                "--json",
            ]

            original = os.environ.get("CODEKB_ENABLE_GOVERNANCE_WRITE")
            try:
                os.environ["CODEKB_ENABLE_GOVERNANCE_WRITE"] = "1"
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main([*base, "--execute"], governance_ticket_client_factory=factory)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(payload["status"], "executed")
                self.assertEqual(len(created), 1)
                self.assertEqual(payload["state_writeback"]["recorded_tickets"], 1)

                # 试运行:不调用 factory。
                created.clear()
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    main([*base], governance_ticket_client_factory=factory)
                self.assertEqual(created, [])

                # 执行但写入开关关闭:不调用 factory,被拦截。
                os.environ.pop("CODEKB_ENABLE_GOVERNANCE_WRITE", None)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    main([*base, "--execute"], governance_ticket_client_factory=factory)
                blocked = json.loads(stdout.getvalue())
                self.assertEqual(created, [])
                self.assertEqual(blocked["status"], "blocked")
            finally:
                if original is None:
                    os.environ.pop("CODEKB_ENABLE_GOVERNANCE_WRITE", None)
                else:
                    os.environ["CODEKB_ENABLE_GOVERNANCE_WRITE"] = original

    def test_cli_writes_governance_ticket_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            outbox = root / "ticket-plan.jsonl"

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "governance-ticket-plan",
                        "--fixtures",
                        str(manifest),
                        "--registry",
                        str(registry),
                        "--feedback-log",
                        str(root / "missing-feedback.jsonl"),
                        "--candidate-store",
                        str(root / "missing-candidates.json"),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--target",
                        "issue_tracker",
                        "--limit",
                        "1",
                        "--outbox",
                        str(outbox),
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(outbox.read_text(encoding="utf-8"))
            self.assertEqual(payload["target"], "issue_tracker")
            self.assertEqual(payload["operations"][0]["tool"], "issue_tracker_create_ticket")

    def test_sync_governance_state_tracks_ticketed_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            state_path = root / "governance-state.json"
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)

            first = sync_governance_state(report, state_path)
            second = sync_governance_state(report, state_path, ticket_plans=plans)
            ticketed = governance_state_ticketed_item_ids(state_path, targets=["issue_tracker"])
            summary = summarize_governance_state(state_path)

            self.assertEqual(first.new_items, len(report.items))
            self.assertEqual(second.ticket_plans, 1)
            self.assertIn(plans[0].item_id, ticketed)
            self.assertEqual(summary["tickets"], 1)
            self.assertEqual(summary["ticket_targets"]["issue_tracker"], 1)

    def test_build_curator_weekly_report_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            state_path = root / "governance-state.json"
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            sync_governance_state(report, state_path)
            weekly = build_curator_weekly_report(
                report,
                state_summary=summarize_governance_state(state_path),
                item_limit=2,
            )

            self.assertEqual(weekly.summary["open_items"], len(report.items))
            self.assertIn("# Code-KB Curator Weekly Report", weekly.markdown)
            self.assertIn("## Sub KB Health", weekly.markdown)
            self.assertLessEqual(len(weekly.top_items), 2)

    def test_write_curator_weekly_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            output = root / "weekly.md"
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            weekly = build_curator_weekly_report(report, item_limit=1)

            write_curator_weekly_report(weekly, output)

            self.assertTrue(output.exists())
            self.assertIn("Top Governance Items", output.read_text(encoding="utf-8"))

    def test_cli_writes_curator_weekly_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            state_path = root / "governance-state.json"
            output = root / "weekly.md"
            json_output = root / "weekly.json"
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            sync_governance_state(report, state_path)

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "governance-weekly-report",
                        "--fixtures",
                        str(manifest),
                        "--registry",
                        str(registry),
                        "--feedback-log",
                        str(root / "missing-feedback.jsonl"),
                        "--candidate-store",
                        str(root / "missing-candidates.json"),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--state",
                        str(state_path),
                        "--item-limit",
                        "2",
                        "--output",
                        str(output),
                        "--json-output",
                        str(json_output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue(output.exists())
            payload = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertIn("markdown", payload)

    def test_build_ticket_plans_can_skip_ticketed_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            state_path = root / "governance-state.json"
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            first_plan = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            sync_governance_state(report, state_path, ticket_plans=first_plan)

            skipped = governance_state_ticketed_item_ids(state_path, targets=["issue_tracker"])
            next_plans = build_governance_ticket_plans(
                report,
                target="issue_tracker",
                min_severity="P1",
                skip_item_ids=skipped,
                limit=10,
            )

            self.assertNotIn(first_plan[0].item_id, {plan.item_id for plan in next_plans})

    def test_export_governance_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            report = build_governance_report(
                [manifest],
                registry_path=registry,
                stale_after_days=180,
                now="2026-06-11T00:00:00Z",
            )
            plans = build_governance_ticket_plans(report, target="issue_tracker", min_severity="P1", limit=1)
            output = root / "artifacts"

            summary = export_governance_artifacts(report, plans, output)

            self.assertEqual(summary["governance_items"], len(report.items))
            self.assertEqual(summary["governance_ticket_plans"], 1)
            self.assertTrue((output / "postgres_upserts.jsonl").exists())
            first_upsert = json.loads((output / "postgres_upserts.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first_upsert["target"], "governance_items")

    def test_cli_exports_governance_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, registry = _write_source_files(root)
            output = root / "artifacts"

            with contextlib.redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "governance-export",
                        "--fixtures",
                        str(manifest),
                        "--registry",
                        str(registry),
                        "--feedback-log",
                        str(root / "missing-feedback.jsonl"),
                        "--candidate-store",
                        str(root / "missing-candidates.json"),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--target",
                        "issue_tracker",
                        "--ticket-limit",
                        "1",
                        "--output-dir",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue((output / "governance_items.jsonl").exists())
            self.assertTrue((output / "governance_ticket_plans.jsonl").exists())


class _FakeTicketClient:
    def __init__(self):
        self.created = []

    def create_issue_tracker_ticket(self, *, title, description, priority, assignee, labels):
        payload = {
            "ticket_id": "issue_tracker-1",
            "title": title,
            "description": description,
            "priority": priority,
            "assignee": assignee,
            "labels": list(labels),
        }
        self.created.append(payload)
        return payload

    def create_git_issue(self, *, title, description, priority, assignee, labels):
        payload = {
            "issue_id": "gf-1",
            "title": title,
            "description": description,
            "priority": priority,
            "assignee": assignee,
            "labels": list(labels),
        }
        self.created.append(payload)
        return payload


def _write_source_files(root: Path) -> tuple[Path, Path]:
    raw_dir = root / "raw"
    raw_dir.mkdir()
    (raw_dir / "old.md").write_text("# Old\n\nRelease knowledge.", encoding="utf-8")
    (raw_dir / "no-owner.md").write_text("# No Owner\n\nTesting knowledge.", encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        """
version: 1
documents:
  - docid: "old-doc"
    sub_kb_id: release
    title: "Old Release Doc"
    content_type: DOC
    url: "https://wiki.example.com/p/old-doc"
    body_path: "raw/old.md"
    metadata:
      system: wiki
      owner: alice
      owner_displayname: "alice(\\u5df2\\u79bb\\u804c)"
      last_modified: "2025-01-01 00:00:00"
  - docid: "no-owner-doc"
    sub_kb_id: release
    title: "No Owner Doc"
    content_type: DOC
    url: "https://wiki.example.com/p/no-owner-doc"
    body_path: "raw/no-owner.md"
    metadata:
      system: wiki
      last_modified: "2026-05-01 00:00:00"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    registry = root / "registry.yaml"
    registry.write_text(
        """
version: 0.1
updated_at: "2026-06-11"
status: draft
defaults: {}
sub_kbs:
  - id: release
    name: Release
    owner_group: release_team
    status: pilot
    description: Release docs
    source_docs:
      - system: wiki
        docid: "old-doc"
        title: "Old Release Doc"
        mode: deep
        priority: P0
      - system: wiki
        docid: "no-owner-doc"
        title: "No Owner Doc"
        mode: deep
        priority: P1
      - system: wiki
        docid: "missing-doc"
        title: "Missing Doc"
        mode: deep
        priority: P1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest, registry


if __name__ == "__main__":
    unittest.main()
