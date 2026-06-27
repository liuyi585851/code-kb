import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.candidate import JsonCandidateStore
from codekb.cli import main
from codekb.reconcile import reconcile_candidates


class ReconcileTests(unittest.TestCase):
    def _store(self, root: Path) -> JsonCandidateStore:
        return JsonCandidateStore(root / "candidates.json", pending_docs_dir=root / "pending")

    def test_reconcile_reports_ok_for_approved_candidate_with_doc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")

            report = reconcile_candidates(store, root / "pending")

            self.assertEqual(report["approved_candidates"], 1)
            self.assertEqual(report["counts"], {"ok": 1, "orphan_docs": 0, "missing_docs": 0})
            self.assertEqual(report["ok"][0]["candidate_id"], approved.candidate.candidate_id)
            self.assertEqual(report["orphan_docs"], [])
            self.assertEqual(report["missing_docs"], [])
            self.assertEqual(report["sections"], {})

    def test_reconcile_detects_missing_doc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            result = store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")
            Path(result.audit.output_path).unlink()

            report = reconcile_candidates(store, root / "pending")

            self.assertEqual(report["counts"], {"ok": 0, "orphan_docs": 0, "missing_docs": 1})
            self.assertEqual(report["missing_docs"][0]["candidate_id"], approved.candidate.candidate_id)
            self.assertEqual(report["missing_docs"][0]["sub_kb_id"], "testing")
            self.assertTrue(report["missing_docs"][0]["expected_path"].endswith(".md"))

    def test_reconcile_detects_orphan_doc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            # 一个孤立的 markdown 文件，没有对应的已审核候选。
            orphan_dir = root / "pending" / "testing"
            orphan_dir.mkdir(parents=True)
            orphan_path = orphan_dir / "orphan-candidate.md"
            orphan_path.write_text("# orphan", encoding="utf-8")

            report = reconcile_candidates(store, root / "pending")

            self.assertEqual(report["approved_candidates"], 0)
            self.assertEqual(report["counts"], {"ok": 0, "orphan_docs": 1, "missing_docs": 0})
            self.assertEqual(report["orphan_docs"][0]["candidate_id"], "orphan-candidate")
            self.assertEqual(report["orphan_docs"][0]["sub_kb_id"], "testing")
            self.assertEqual(report["orphan_docs"][0]["path"], str(orphan_path))

    def test_reconcile_mixed_and_deterministic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            ok_cand = store.submit(sub_kb_id="testing", title="OK", content="ok 正文")
            store.audit(ok_cand.candidate.candidate_id, action="approve", reviewer_hash="r")
            missing_cand = store.submit(sub_kb_id="release", title="MISSING", content="missing 正文")
            missing_res = store.audit(missing_cand.candidate.candidate_id, action="approve", reviewer_hash="r")
            Path(missing_res.audit.output_path).unlink()
            # 跨子库放两个孤立文档，验证排序是否稳定。
            (root / "pending" / "alpha").mkdir(parents=True)
            (root / "pending" / "alpha" / "z-doc.md").write_text("x", encoding="utf-8")
            (root / "pending" / "alpha" / "a-doc.md").write_text("x", encoding="utf-8")

            report = reconcile_candidates(store, root / "pending")

            self.assertEqual(report["counts"], {"ok": 1, "orphan_docs": 2, "missing_docs": 1})
            # 按 (sub_kb_id, candidate_id) 稳定排序。
            orphan_keys = [(o["sub_kb_id"], o["candidate_id"]) for o in report["orphan_docs"]]
            self.assertEqual(orphan_keys, sorted(orphan_keys))
            self.assertEqual(orphan_keys, [("alpha", "a-doc"), ("alpha", "z-doc")])
            # 只读操作：不动 store 状态（仍记录着 2 个已审核候选）。
            self.assertEqual(store.summary()["approved"], 2)

    def test_reconcile_handles_missing_pending_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            report = reconcile_candidates(store, root / "does-not-exist")
            self.assertEqual(report["counts"], {"ok": 0, "orphan_docs": 0, "missing_docs": 0})
            self.assertEqual(report["total_docs"], 0)

    def test_cli_reconcile_outputs_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"
            pending = root / "pending"
            store = JsonCandidateStore(store_path, pending_docs_dir=pending)
            approved = store.submit(sub_kb_id="testing", title="已审核", content="批准正文")
            store.audit(approved.candidate.candidate_id, action="approve", reviewer_hash="r")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    ["reconcile", "--store", str(store_path), "--pending-docs-dir", str(pending), "--json"]
                )

            report = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"]["ok"], 1)
            self.assertEqual(report["approved_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
