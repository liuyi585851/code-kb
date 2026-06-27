import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.p3_usecase_smoke import run_p3_usecase_smoke


class P3UsecaseSmokeTests(unittest.TestCase):
    def test_run_p3_usecase_smoke_covers_ingest_audit_ask_and_publish_outbox(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as tmp:
            report = run_p3_usecase_smoke(
                work_dir=tmp,
                fixture_path=root / "data" / "fixtures" / "sample_corpus.jsonl",
                aliases_path=root / "data" / "entity_aliases.yaml",
                publish_mode="index_page",
                index_docid="401",
            )

            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["ingest"]["status"], "accepted")
            self.assertEqual(report["audit"]["status"], "approved")
            self.assertEqual(report["index_rebuild"]["status"], "rebuilt")
            self.assertFalse(report["ask"]["refused"])
            self.assertIn("LETGOKB_E2E_RULE", report["ask"]["citation_titles"][0])
            self.assertEqual(report["publish"]["process_status"], "validated")
            self.assertEqual(report["publish"]["written"], 1)
            self.assertTrue(Path(report["paths"]["candidate_store"]).exists())
            self.assertTrue(Path(report["paths"]["publish_outbox"]).exists())

    def test_cli_p3_usecase_smoke_outputs_json_report(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as tmp:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codekb",
                    "p3-usecase-smoke",
                    "--work-dir",
                    tmp,
                    "--fixtures",
                    str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    "--aliases",
                    str(root / "data" / "entity_aliases.yaml"),
                    "--publish-mode",
                    "index_page",
                    "--index-docid",
                    "401",
                    "--json",
                ],
                cwd=root,
                env={"PYTHONPATH": str(root / "src")},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["publish"]["process_status"], "validated")


if __name__ == "__main__":
    unittest.main()
