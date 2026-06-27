import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.cli import main
from codekb.publish import build_publish_plans, process_publish_outbox, write_publish_outbox
from codekb.publish_api import build_publish_readiness, plan_publish_outbox, process_publish_outbox_report
from codekb.publish_config import configure_publish_env


class PublishPlanTests(unittest.TestCase):
    def test_configure_publish_env_applies_target_config_without_printing_values(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("EXISTING_KEY=keep\n", encoding="utf-8")
            process_env = {}

            planned = configure_publish_env(
                env_file=str(env_file),
                values={"mode": "index_page", "index_docid": "401"},
                apply=False,
                env=process_env,
            )
            applied = configure_publish_env(
                env_file=str(env_file),
                values={"mode": "index_page", "index_docid": "401"},
                apply=True,
                env=process_env,
            )

            raw_env = env_file.read_text(encoding="utf-8")
            raw_response = json.dumps([planned, applied], ensure_ascii=False)
            self.assertEqual(planned["status"], "ready_to_apply")
            self.assertFalse(planned["applied"])
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["applied"])
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            self.assertIn("EXISTING_KEY=keep", raw_env)
            self.assertIn("CODEKB_PUBLISH_MODE=index_page", raw_env)
            self.assertIn("CODEKB_PUBLISH_INDEX_DOCID=401", raw_env)
            self.assertEqual(process_env["CODEKB_PUBLISH_MODE"], "index_page")
            self.assertEqual(process_env["CODEKB_PUBLISH_INDEX_DOCID"], "401")
            self.assertNotIn("401", raw_response)

    def test_configure_publish_env_blocks_missing_target_config(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"

            report = configure_publish_env(
                env_file=str(env_file),
                values={"mode": "index_page"},
                apply=True,
                env={},
            )

            self.assertEqual(report["status"], "blocked_missing_inputs")
            self.assertFalse(report["ok"])
            self.assertFalse(report["applied"])
            self.assertIn("CODEKB_PUBLISH_INDEX_DOCID", report["missing_env"])
            self.assertFalse(env_file.exists())

    def test_configure_publish_env_blocks_non_numeric_wiki_ids(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"

            report = configure_publish_env(
                env_file=str(env_file),
                values={"mode": "index_page", "index_docid": "index-1"},
                apply=True,
                env={},
            )

            self.assertEqual(report["status"], "blocked_invalid_config")
            self.assertFalse(report["ok"])
            self.assertIn("CODEKB_PUBLISH_INDEX_DOCID", report["missing_env"])
            self.assertFalse(env_file.exists())

    def test_publish_readiness_reports_missing_index_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pending_doc(root / "pending", candidate_id="candidate-ready-1")

            readiness = build_publish_readiness(
                root / "pending",
                root / "outbox" / "publish.jsonl",
                root / "logs" / "publish-report.json",
                env={"CODEKB_PUBLISH_MODE": "index_page"},
            )

            self.assertEqual(readiness["status"], "missing_target_config")
            self.assertEqual(readiness["mode"], "index_page")
            self.assertIn("CODEKB_PUBLISH_INDEX_DOCID", readiness["missing"])
            self.assertEqual(readiness["pending_docs"]["count"], 1)

    def test_publish_readiness_reports_invalid_mode(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pending_doc(root / "pending", candidate_id="candidate-ready-invalid")

            readiness = build_publish_readiness(
                root / "pending",
                root / "outbox" / "publish.jsonl",
                root / "logs" / "publish-report.json",
                env={"CODEKB_PUBLISH_MODE": "bad_mode"},
            )

            self.assertEqual(readiness["status"], "invalid_config")
            self.assertIn("CODEKB_PUBLISH_MODE", readiness["missing"])

    def test_publish_readiness_reports_non_numeric_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pending_doc(root / "pending", candidate_id="candidate-ready-nonnumeric")

            readiness = build_publish_readiness(
                root / "pending",
                root / "outbox" / "publish.jsonl",
                root / "logs" / "publish-report.json",
                env={"CODEKB_PUBLISH_MODE": "index_page", "CODEKB_PUBLISH_INDEX_DOCID": "index-1"},
            )

            self.assertEqual(readiness["status"], "invalid_config")
            self.assertIn("CODEKB_PUBLISH_INDEX_DOCID", readiness["missing"])

    def test_publish_readiness_reports_ready_for_outbox_with_configured_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pending_doc(root / "pending", candidate_id="candidate-ready-2")

            readiness = build_publish_readiness(
                root / "pending",
                root / "outbox" / "publish.jsonl",
                root / "logs" / "publish-report.json",
                ledger_path=root / "state" / "publish-ledger.jsonl",
                env={
                    "CODEKB_PUBLISH_MODE": "index_page",
                    "CODEKB_PUBLISH_INDEX_DOCID": "401",
                    "CODEKB_ENABLE_WIKI_WRITE": "0",
                },
            )

            self.assertEqual(readiness["status"], "ready_for_outbox")
            self.assertFalse(readiness["write_enabled"])
            self.assertFalse(readiness["real_write_ready"])
            self.assertEqual(readiness["resolved"]["index_docid"], "401")
            self.assertEqual(readiness["ledger_path"], str(root / "state" / "publish-ledger.jsonl"))

    def test_plan_publish_outbox_uses_configured_defaults(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root / "pending", candidate_id="candidate-http-default")
            outbox = root / "outbox" / "publish.jsonl"

            response = plan_publish_outbox(
                pending,
                outbox,
                {"limit": 20},
                env={"CODEKB_PUBLISH_MODE": "index_page", "CODEKB_PUBLISH_INDEX_DOCID": "401"},
            )

            operation = response["plans"][0]["operations"][0]
            self.assertEqual(response["status"], "queued")
            self.assertEqual(response["mode"], "index_page")
            self.assertEqual(operation["tool"], "saveDocumentParts")
            self.assertEqual(operation["params"]["id"], 401)
            self.assertEqual(operation["params"]["title"], "发布计划测试")
            self.assertIn("candidate-http-default", operation["params"]["after"])

    def test_plan_publish_outbox_writes_server_outbox_response(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root / "pending", candidate_id="candidate-http-1")
            outbox = root / "outbox" / "publish.jsonl"

            response = plan_publish_outbox(
                pending,
                outbox,
                {"mode": "index_page", "index_docid": "401", "limit": 20},
            )

            self.assertEqual(response["status"], "queued")
            self.assertEqual(response["written"], 1)
            self.assertEqual(response["outbox_path"], str(outbox))
            self.assertEqual(response["plans"][0]["candidate_id"], "candidate-http-1")
            self.assertTrue(outbox.exists())

    def test_process_publish_outbox_report_writes_server_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root / "pending", candidate_id="candidate-http-2")
            outbox = root / "outbox" / "publish.jsonl"
            report_path = root / "logs" / "publish-report.json"
            write_publish_outbox(build_publish_plans(pending, mode="index_page", index_docid="401"), outbox)

            response = process_publish_outbox_report(
                outbox,
                report_path,
                {"execute": False, "limit": 20},
                ledger_path=root / "state" / "publish-ledger.jsonl",
                write_enabled=False,
            )

            self.assertEqual(response["status"], "validated")
            self.assertEqual(response["processed"], 1)
            self.assertEqual(response["report_path"], str(report_path))
            self.assertEqual(response["ledger_path"], str(root / "state" / "publish-ledger.jsonl"))
            self.assertEqual(response["skipped_operations"], 0)
            self.assertTrue(report_path.exists())

    def test_manual_publish_plan_renders_body(self):
        with TemporaryDirectory() as tmp:
            pending = _write_pending_doc(Path(tmp), candidate_id="candidate-1")

            plans = build_publish_plans(pending, mode="manual")

            self.assertEqual(len(plans), 1)
            plan = plans[0]
            self.assertEqual(plan.candidate_id, "candidate-1")
            self.assertEqual(plan.mode, "manual")
            self.assertEqual(plan.operations[0].tool, "manual_publish")
            self.assertIn("人工审核候选", plan.rendered_body)
            self.assertIn("PUBLISH_RULE", plan.rendered_body)
            self.assertEqual(plan.rendered_body.count("# 发布计划测试"), 1)

    def test_index_page_plan_requires_index_docid(self):
        with TemporaryDirectory() as tmp:
            pending = _write_pending_doc(Path(tmp), candidate_id="candidate-2")

            with self.assertRaises(ValueError):
                build_publish_plans(pending, mode="index_page")
            with self.assertRaisesRegex(ValueError, "numeric Wiki docid"):
                build_publish_plans(pending, mode="index_page", index_docid="index-1")
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")

            self.assertEqual(plans[0].operations[0].tool, "saveDocumentParts")
            self.assertEqual(plans[0].operations[0].params["id"], 401)
            self.assertEqual(plans[0].operations[0].params["title"], "发布计划测试")
            self.assertIn("candidate-2", plans[0].operations[0].params["after"])

    def test_template_copy_plan_requires_template_and_parent(self):
        with TemporaryDirectory() as tmp:
            pending = _write_pending_doc(Path(tmp), candidate_id="candidate-3")

            with self.assertRaises(ValueError):
                build_publish_plans(pending, mode="template_copy", template_docid="tpl")
            with self.assertRaisesRegex(ValueError, "numeric Wiki docid"):
                build_publish_plans(pending, mode="template_copy", template_docid="tpl", target_parentid="403")
            plans = build_publish_plans(
                pending,
                mode="template_copy",
                template_docid="402",
                target_parentid="403",
            )

            self.assertEqual([operation.tool for operation in plans[0].operations], ["copyDocument", "saveDocument"])
            self.assertEqual(plans[0].operations[0].params["docid"], 402)
            self.assertEqual(plans[0].operations[0].params["new_parentid"], 403)
            self.assertEqual(plans[0].operations[0].params["is_single"], 1)

    def test_write_publish_outbox_appends_jsonl(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-4")
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(pending, mode="manual")

            written = write_publish_outbox(plans, outbox)

            self.assertEqual(written, 1)
            payload = json.loads(outbox.read_text(encoding="utf-8"))
            self.assertEqual(payload["candidate_id"], "candidate-4")
            self.assertEqual(payload["operations"][0]["tool"], "manual_publish")

    def test_process_publish_outbox_dry_run_validates_write_operations(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-5")
            outbox = root / "outbox" / "publish.jsonl"
            report_path = root / "report.json"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)

            report = process_publish_outbox(outbox, report_path=report_path)

            self.assertEqual(report.status, "validated")
            self.assertEqual(report.processed, 1)
            self.assertEqual(report.results[0].operations[0].status, "validated")
            self.assertTrue(report_path.exists())

    def test_process_publish_outbox_execute_blocks_without_write_enable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-6")
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=False,
                confirm_real_publish=True,
            )

            self.assertEqual(report.status, "blocked")
            self.assertEqual(report.blocked_operations, 1)
            self.assertEqual(report.results[0].operations[0].status, "blocked_write_disabled")

    def test_process_publish_outbox_executes_with_fake_client(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-7")
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(
                pending,
                mode="template_copy",
                template_docid="402",
                target_parentid="403",
            )
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                confirm_real_publish=True,
            )

            self.assertEqual(report.status, "executed")
            self.assertEqual(report.executed_operations, 2)
            self.assertEqual([call["tool"] for call in client.calls], ["copyDocument", "saveDocument"])
            self.assertEqual(client.calls[1]["docid"], 987)

    def test_process_publish_outbox_execute_requires_explicit_real_publish_confirmation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-8")
            outbox = root / "outbox" / "publish.jsonl"
            ledger = root / "state" / "publish-ledger.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                ledger_path=ledger,
            )

            self.assertEqual(report.status, "blocked_confirmation_required")
            self.assertEqual(report.processed, 1)
            self.assertEqual(report.blocked_operations, 1)
            self.assertEqual(report.results[0].operations[0].status, "blocked_confirmation_required")
            self.assertEqual(client.calls, [])
            self.assertFalse(ledger.exists())

    def test_process_publish_outbox_execute_with_confirmation_records_ledger(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-9")
            outbox = root / "outbox" / "publish.jsonl"
            ledger = root / "state" / "publish-ledger.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                ledger_path=ledger,
                confirm_real_publish=True,
            )

            ledger_records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(report.status, "executed")
            self.assertEqual(report.executed_operations, 1)
            self.assertEqual([call["tool"] for call in client.calls], ["saveDocumentParts"])
            self.assertEqual(ledger_records[0]["publish_id"], plans[0].publish_id)

    def test_process_publish_outbox_ledger_skips_already_executed_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-10")
            outbox = root / "outbox" / "publish.jsonl"
            ledger = root / "state" / "publish-ledger.jsonl"
            plans = build_publish_plans(
                pending,
                mode="template_copy",
                template_docid="402",
                target_parentid="403",
            )
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            first = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                ledger_path=ledger,
                confirm_real_publish=True,
            )
            second = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                ledger_path=ledger,
                confirm_real_publish=True,
            )

            ledger_records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(first.status, "executed")
            self.assertEqual(second.status, "skipped")
            self.assertEqual(second.executed_operations, 0)
            self.assertEqual(second.skipped_operations, 2)
            self.assertEqual(second.results[0].status, "skipped")
            self.assertEqual({item.status for item in second.results[0].operations}, {"skipped_already_executed"})
            self.assertEqual([call["tool"] for call in client.calls], ["copyDocument", "saveDocument"])
            self.assertEqual(ledger_records[0]["publish_id"], plans[0].publish_id)
            self.assertEqual(ledger_records[0]["status"], "executed")

    def test_cli_publish_outbox_injects_client_only_when_executing_with_write_enabled(self):
        created = []

        def factory():
            client = _FakePublishClient()
            created.append(client)
            return client

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-cli-wire")
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)
            def args_with(ledger_name):
                return [
                    "publish-outbox",
                    "--outbox",
                    str(outbox),
                    "--report",
                    str(root / "report.json"),
                    "--ledger",
                    str(root / ledger_name),
                    "--json",
                ]

            original = os.environ.get("CODEKB_ENABLE_WIKI_WRITE")
            try:
                # 写开关打开后执行 -> 走 factory 建客户端并真正执行。
                os.environ["CODEKB_ENABLE_WIKI_WRITE"] = "1"
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(
                        [*args_with("ledger-on.jsonl"), "--execute", "--confirm-real-publish"],
                        wiki_publish_client_factory=factory,
                    )
                report = json.loads(stdout.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(report["status"], "executed")
                self.assertEqual(len(created), 1)
                self.assertEqual(created[0].calls[0]["tool"], "saveDocumentParts")

                # 仅试运行时压根不该建客户端。
                created.clear()
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    main([*args_with("ledger-dry.jsonl")], wiki_publish_client_factory=factory)
                self.assertEqual(created, [])

                # 执行但写开关是关的 -> 客户端为 None -> 被拦截,factory 不会被调用。
                # 换一份全新的台账,免得上一次执行把这个计划标记成已跳过。
                os.environ.pop("CODEKB_ENABLE_WIKI_WRITE", None)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    main(
                        [*args_with("ledger-off.jsonl"), "--execute", "--confirm-real-publish"],
                        wiki_publish_client_factory=factory,
                    )
                blocked = json.loads(stdout.getvalue())
                self.assertEqual(created, [])
                self.assertTrue(blocked["status"].startswith("blocked"))
            finally:
                if original is None:
                    os.environ.pop("CODEKB_ENABLE_WIKI_WRITE", None)
                else:
                    os.environ["CODEKB_ENABLE_WIKI_WRITE"] = original


def _write_pending_doc(root: Path, *, candidate_id: str) -> Path:
    path = root / "testing" / f"{candidate_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f'candidate_id: "{candidate_id}"',
                'sub_kb_id: "testing"',
                'source_type: "manual"',
                'source_ref: "unit-test"',
                'dedupe_key: "dedupe"',
                'approved_at: "2026-06-11T08:15:36Z"',
                "---",
                "",
                "# 发布计划测试",
                "",
                "PUBLISH_RULE 表示发布计划测试规则。",
            ]
        ),
        encoding="utf-8",
    )
    return root


class _FakePublishClient:
    def __init__(self) -> None:
        self.calls = []

    def save_document_parts(self, *, id: int, title: str, after: str = "", before: str = ""):
        self.calls.append({"tool": "saveDocumentParts", "id": id, "title": title, "after": after, "before": before})
        return {"ok": True}

    def copy_document(self, *, docid: int, new_parentid: int, is_single: int = 1, language: str = "zh_CN"):
        self.calls.append(
            {
                "tool": "copyDocument",
                "docid": docid,
                "new_parentid": new_parentid,
                "is_single": is_single,
                "language": language,
            }
        )
        return {"docid": 987}

    def save_document(self, *, docid: int, title: str, body: str, is_html: bool = False, raw: bool = False):
        self.calls.append({"tool": "saveDocument", "docid": docid, "title": title, "body": body})
        return {"ok": True}


if __name__ == "__main__":
    unittest.main()
