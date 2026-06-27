import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.candidate import JsonCandidateStore
from codekb.external_index_sync import sync_external_index_artifacts


ROOT = Path(__file__).resolve().parents[1]
WIKI_MANIFEST = ROOT / "data" / "fixtures" / "sample_corpus.jsonl"


class CombinedStatusTests(unittest.TestCase):
    def test_dimension_mismatch_is_blocked_not_partial(self):
        from codekb.external_index_sync import _combined_status

        self.assertEqual(
            _combined_status("planned", "dimension_mismatch", "skipped", execute=True),
            "blocked",
        )

    def test_all_synced_is_synced(self):
        from codekb.external_index_sync import _combined_status

        self.assertEqual(_combined_status("synced", "synced", execute=True), "synced")


class ExternalIndexSyncTests(unittest.TestCase):

    def test_sync_external_index_artifacts_includes_opensearch_when_configured(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            env_file = root / "storage.env"
            env_file.write_text(
                "\n".join(
                    [
                        "POSTGRES_DSN=postgresql://user:secret@pg.internal:5432/db",
                        "QDRANT_URL=http://qdrant.internal:6333",
                        "OPENSEARCH_URL=http://opensearch.internal:9200",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = sync_external_index_artifacts(
                fixture_path=WIKI_MANIFEST,
                output_dir=artifacts,
                env_file=env_file,
                execute=False,
            )

            self.assertTrue((artifacts / "opensearch_documents.jsonl").exists())

        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["opensearch"]["status"], "planned")
        self.assertEqual(report["opensearch"]["documents"], report["qdrant"]["points"])
        self.assertNotIn("secret@pg", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
