import json
import io
import contextlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.candidate import (
    APPROVED_STATUS,
    NEEDS_REVISION_STATUS,
    PENDING_STATUS,
    JsonCandidateStore,
    parse_audit_payload,
    parse_ingest_payload,
    parse_revision_payload,
)
from codekb.cli import main


class CandidateTests(unittest.TestCase):
    def test_submit_candidate_detects_duplicates(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")

            first = store.submit(
                sub_kb_id="testing",
                title="DEVICE_SEQ 使用说明",
                content="DEVICE_SEQ 表示设备序号。",
                source_type="im",
                source_ref="msg-1",
                submitted_by_hash="u_1",
            )
            duplicate = store.submit(
                sub_kb_id="testing",
                title=" DEVICE_SEQ 使用说明 ",
                content="DEVICE_SEQ   表示设备序号。",
                source_type="im",
                source_ref="msg-2",
            )

            self.assertFalse(first.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.existing_candidate_id, first.candidate.candidate_id)
            self.assertEqual(len(store.list()), 1)

    def test_submit_candidate_marks_title_conflict(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")
            first = store.submit(sub_kb_id="release", title="灰度参数", content="参数 A 表示灰度比例。")
            second = store.submit(sub_kb_id="release", title="灰度参数", content="参数 B 表示灰度比例。")

            self.assertEqual(second.candidate.conflict_candidate_id, first.candidate.candidate_id)
            self.assertEqual(store.summary()["conflicts"], 1)

    def test_audit_approve_writes_pending_document(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonCandidateStore(root / "candidates.json", pending_docs_dir=root / "pending")
            submission = store.submit(sub_kb_id="incident", title="事故复盘补充", content="补充止血步骤。")

            result = store.audit(
                submission.candidate.candidate_id,
                action="approve",
                reviewer_hash="reviewer",
                comment="ok",
            )

            self.assertEqual(result.candidate.status, APPROVED_STATUS)
            self.assertTrue(Path(result.audit.output_path).exists())
            doc = Path(result.audit.output_path).read_text(encoding="utf-8")
            self.assertIn("candidate_id", doc)
            self.assertIn("补充止血步骤", doc)
            self.assertEqual(store.get(submission.candidate.candidate_id).approved_doc_path, result.audit.output_path)
            with self.assertRaises(ValueError):
                store.audit(submission.candidate.candidate_id, action="reject")

    def test_audit_request_revision_keeps_candidate_open(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")
            submission = store.submit(sub_kb_id="testing", title="补充", content="需要补充日志字段。")

            result = store.audit(submission.candidate.candidate_id, action="request_revision")

            self.assertEqual(result.audit.previous_status, PENDING_STATUS)
            self.assertEqual(result.candidate.status, NEEDS_REVISION_STATUS)
            self.assertFalse(result.audit.output_path)

    def test_revise_candidate_moves_back_to_pending_review(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")
            submission = store.submit(sub_kb_id="testing", title="补充", content="需要补充日志字段。")
            store.audit(submission.candidate.candidate_id, action="request_revision", comment="补充细节")

            result = store.revise(
                submission.candidate.candidate_id,
                content="需要补充日志字段和排查入口。",
                metadata={"revision": 1},
                submitted_by_hash="u_2",
                comment="已补充",
            )
            audits = store.audits(candidate_id=submission.candidate.candidate_id)

            self.assertEqual(result.candidate.status, PENDING_STATUS)
            self.assertEqual(result.candidate.title, "补充")
            self.assertIn("排查入口", result.candidate.content)
            self.assertEqual(result.candidate.metadata["revision"], 1)
            self.assertEqual(result.audit.action, "revise")
            self.assertEqual(result.audit.previous_status, NEEDS_REVISION_STATUS)
            self.assertEqual(result.audit.new_status, PENDING_STATUS)
            self.assertEqual({audit.action for audit in audits}, {"request_revision", "revise"})

    def test_revise_candidate_rejects_wrong_state_and_duplicate(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")
            first = store.submit(sub_kb_id="testing", title="标题 A", content="正文 A")
            second = store.submit(sub_kb_id="testing", title="标题 B", content="正文 B")
            store.audit(second.candidate.candidate_id, action="request_revision")

            with self.assertRaises(ValueError):
                store.revise(first.candidate.candidate_id, content="正文 A2")
            with self.assertRaises(ValueError):
                store.revise(second.candidate.candidate_id, title="标题 A", content="正文 A")

    def test_payload_parsers_validate_fields(self):
        parsed = parse_ingest_payload(
            {
                "sub_kb": "testing",
                "title": "标题",
                "content": "正文",
                "source": {"type": "im", "ref": "msg-1"},
                "metadata": {"k": "v"},
            }
        )

        self.assertEqual(parsed["sub_kb_id"], "testing")
        self.assertEqual(parsed["source_type"], "im")
        self.assertEqual(parsed["source_ref"], "msg-1")
        self.assertEqual(parse_audit_payload({"action": "needs_revision"})["action"], "request_revision")
        revision = parse_revision_payload({"content": "修订正文", "metadata": {"k": "v"}, "submitted_by_hash": "u"})
        self.assertEqual(revision["content"], "修订正文")
        self.assertEqual(revision["metadata"]["k"], "v")
        with self.assertRaises(ValueError):
            parse_ingest_payload({"metadata": []})
        with self.assertRaises(ValueError):
            parse_audit_payload({"action": "delete"})
        with self.assertRaises(ValueError):
            parse_revision_payload({"metadata": []})

    def test_state_file_is_json(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            store = JsonCandidateStore(path, pending_docs_dir=Path(tmp) / "pending")
            store.submit(sub_kb_id="release", title="标题", content="正文")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(len(payload["candidates"]), 1)
            self.assertEqual(payload["audits"], [])

    def test_cli_candidate_revise_outputs_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"
            pending = root / "pending"
            store = JsonCandidateStore(store_path, pending_docs_dir=pending)
            submission = store.submit(sub_kb_id="testing", title="CLI 修订", content="初稿")
            store.audit(submission.candidate.candidate_id, action="request_revision")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "candidate-revise",
                        "--store",
                        str(store_path),
                        "--pending-docs-dir",
                        str(pending),
                        "--candidate-id",
                        submission.candidate.candidate_id,
                        "--content",
                        "CLI 修订正文",
                        "--metadata-json",
                        '{"revision":2}',
                        "--submitted-by-hash",
                        "author",
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["candidate"]["status"], PENDING_STATUS)
            self.assertEqual(payload["candidate"]["metadata"]["revision"], 2)
            self.assertEqual(payload["audit"]["action"], "revise")

    def test_purge_dry_run_does_not_mutate_store_or_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"
            pending = root / "pending"
            store = JsonCandidateStore(store_path, pending_docs_dir=pending)
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            approved_audit = store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")
            rejected = store.submit(sub_kb_id="testing", title="被拒", content="拒绝正文")
            store.audit(rejected.candidate.candidate_id, action="reject", reviewer_hash="r")
            store.submit(sub_kb_id="testing", title="待审", content="待审正文")
            state_before = store_path.read_text(encoding="utf-8")

            plan = store.purge(status=APPROVED_STATUS)

            self.assertTrue(plan["dry_run"])
            self.assertEqual(plan["status"], APPROVED_STATUS)
            self.assertEqual(plan["matched_candidates"], 1)
            self.assertEqual(plan["matched_audits"], 1)
            self.assertEqual(plan["pending_docs"], 1)
            self.assertEqual(plan["remaining_candidates"], 2)
            self.assertEqual(plan["candidate_ids"], [approved.candidate.candidate_id])
            self.assertEqual(plan["removed_pending_docs"], [])
            # 磁盘上没有任何改动。
            self.assertEqual(store_path.read_text(encoding="utf-8"), state_before)
            self.assertTrue(Path(approved_audit.audit.output_path).exists())
            self.assertEqual(store.summary()["total"], 3)

    def test_purge_apply_removes_candidates_audits_and_docs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonCandidateStore(root / "candidates.json", pending_docs_dir=root / "pending")
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            approved_audit = store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")
            kept = store.submit(sub_kb_id="testing", title="待审", content="待审正文")
            doc_path = Path(approved_audit.audit.output_path)
            self.assertTrue(doc_path.exists())

            plan = store.purge(status=APPROVED_STATUS, dry_run=False)

            self.assertFalse(plan["dry_run"])
            self.assertEqual(plan["matched_candidates"], 1)
            self.assertEqual(plan["removed_pending_docs"], [str(doc_path)])
            self.assertFalse(doc_path.exists())
            # 已审核的候选连同审核记录都被清掉,待审的那条保留。
            remaining_ids = {c.candidate_id for c in store.list(limit=100)}
            self.assertEqual(remaining_ids, {kept.candidate.candidate_id})
            self.assertEqual(store.audits(limit=100), ())
            self.assertEqual(store.summary()["approved"], 0)

    def test_purge_apply_skips_missing_pending_doc_safely(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonCandidateStore(root / "candidates.json", pending_docs_dir=root / "pending")
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            approved_audit = store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")
            # 模拟待发布文档被外部手工删掉的情况。
            Path(approved_audit.audit.output_path).unlink()

            plan = store.purge(status=APPROVED_STATUS, dry_run=False)

            self.assertEqual(plan["matched_candidates"], 1)
            self.assertEqual(plan["pending_docs"], 0)
            self.assertEqual(plan["removed_pending_docs"], [])
            self.assertEqual(store.summary()["total"], 0)

    def test_purge_requires_status(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonCandidateStore(root / "candidates.json", pending_docs_dir=root / "pending")
            with self.assertRaises(ValueError):
                store.purge(status="  ")

    def test_cli_candidate_purge_dry_run_then_apply(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"
            pending = root / "pending"
            store = JsonCandidateStore(store_path, pending_docs_dir=pending)
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")

            base_args = ["--store", str(store_path), "--pending-docs-dir", str(pending), "--status", APPROVED_STATUS]

            dry_stdout = io.StringIO()
            with contextlib.redirect_stdout(dry_stdout):
                dry_code = main(["candidate-purge", *base_args, "--json"])
            dry_plan = json.loads(dry_stdout.getvalue())
            self.assertEqual(dry_code, 0)
            self.assertTrue(dry_plan["dry_run"])
            self.assertEqual(dry_plan["matched_candidates"], 1)
            # 试运行不动候选数据。
            self.assertEqual(store.summary()["approved"], 1)

            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                apply_code = main(["candidate-purge", *base_args, "--apply", "--json"])
            apply_plan = json.loads(apply_stdout.getvalue())
            self.assertEqual(apply_code, 0)
            self.assertFalse(apply_plan["dry_run"])
            self.assertEqual(apply_plan["matched_candidates"], 1)
            self.assertEqual(store.summary()["total"], 0)


if __name__ == "__main__":
    unittest.main()
