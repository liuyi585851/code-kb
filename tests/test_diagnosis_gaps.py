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
from codekb.diagnosis_gaps import summarize_diagnostic_gaps


class DiagnosisGapSummaryTests(unittest.TestCase):
    def test_summarizes_and_clusters_diagnostic_gaps(self):
        with TemporaryDirectory() as tmp:
            store = JsonCandidateStore(Path(tmp) / "candidates.json", pending_docs_dir=Path(tmp) / "pending")
            _submit_gap(
                store,
                title="KB Gap: DEVICE_SEQ 报错怎么办",
                sub_kb_id="testing",
                fingerprint="fp-1",
            )
            _submit_gap(
                store,
                title="KB Gap: DEVICE_SEQ 失败如何处理",
                sub_kb_id="testing",
                fingerprint="fp-2",
                allow_duplicate=True,
            )
            _submit_gap(
                store,
                title="KB Gap: 构建流水线失败怎么办",
                sub_kb_id="release",
                owner="release",
                fingerprint="fp-3",
            )

            summary = summarize_diagnostic_gaps(store, limit=10)

            self.assertEqual(summary.total_candidates, 3)
            self.assertEqual(summary.total_diagnostic_gaps, 3)
            self.assertEqual(summary.clusters_total, 2)
            self.assertEqual(summary.counts_by_sub_kb["testing"], 2)
            testing_cluster = next(cluster for cluster in summary.clusters if cluster.sub_kb_id == "testing")
            self.assertEqual(testing_cluster.total_candidates, 2)
            self.assertEqual(testing_cluster.open_candidates, 2)
            self.assertEqual(testing_cluster.similarity_terms, ("device_seq",))
            self.assertEqual(set(testing_cluster.fingerprints), {"fp-1", "fp-2"})

    def test_cli_diagnose_gap_summary_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / "candidates.json"
            store = JsonCandidateStore(store_path, pending_docs_dir=root / "pending")
            _submit_gap(store, title="KB Gap: DEVICE_SEQ 报错怎么办", sub_kb_id="testing", fingerprint="fp-1")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "diagnose-gap-summary",
                        "--store",
                        str(store_path),
                        "--pending-docs-dir",
                        str(root / "pending"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["total_diagnostic_gaps"], 1)
            self.assertEqual(payload["clusters"][0]["similarity_terms"], ["device_seq"])


def _submit_gap(
    store: JsonCandidateStore,
    *,
    title: str,
    sub_kb_id: str,
    fingerprint: str,
    owner: str = "qa_testing",
    allow_duplicate: bool = False,
):
    return store.submit(
        sub_kb_id=sub_kb_id,
        title=title,
        content=f"# {title}\n\nMissing KB content.",
        source_type="diagnose",
        source_ref=f"trace-{fingerprint}",
        metadata={
            "gap_fingerprint": fingerprint,
            "source_event": "ask_refusal",
            "priority": "P1",
            "suggested_owner": owner,
        },
        allow_duplicate=allow_duplicate,
    )


if __name__ == "__main__":
    unittest.main()
