import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.sync import sync_source_path
from codekb.source import load_source_bundle
from codekb.local_index import build_local_index, local_index_stats
from codekb.service import OfflineKbService

ROOT = Path(__file__).resolve().parents[1]
WIKI_MANIFEST = ROOT / "data" / "fixtures" / "sample_corpus.jsonl"
ALIASES = ROOT / "data" / "entity_aliases.yaml"
WIKI_DOC_COUNT = len(load_source_bundle(WIKI_MANIFEST).documents)


class SyncTests(unittest.TestCase):

    def test_sync_skips_unchanged_documents(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"

            first = sync_source_path(WIKI_MANIFEST, state_path=state_path)
            second = sync_source_path(WIKI_MANIFEST, state_path=state_path)

            self.assertEqual(first.indexed, WIKI_DOC_COUNT)
            self.assertEqual(second.indexed, 0)
            self.assertEqual(second.skipped, WIKI_DOC_COUNT)
            self.assertTrue(all(result.reason == "unchanged" for result in second.results))

    def test_sync_reindexes_when_body_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            body_path = root / "doc.md"
            body_path.write_text("# 文档\n\nDEVICE_SEQ 表示设备序号。", encoding="utf-8")
            manifest_path = _write_manifest(root, body_path)
            state_path = root / "state.json"

            first = sync_source_path(manifest_path, state_path=state_path)
            body_path.write_text("# 文档\n\nDEVICE_SEQ 表示设备序号。\n\n新增日志字段。", encoding="utf-8")
            second = sync_source_path(manifest_path, state_path=state_path)

            self.assertEqual(first.indexed, 1)
            self.assertEqual(second.indexed, 1)
            self.assertEqual(second.results[0].reason, "body_changed")

    def test_empty_document_fails_and_is_not_marked_synced(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "docs.jsonl"
            fixture.write_text(
                json.dumps(
                    {
                        "docid": "empty",
                        "sub_kb_id": "testing",
                        "title": "空文档",
                        "content_type": "DOC",
                        "body": "",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path = root / "state.json"

            report = sync_source_path(fixture, state_path=state_path)

            self.assertEqual(report.failed, 1)
            self.assertEqual(report.results[0].reason, "empty_atom_set")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["documents"], {})

    def test_pending_docs_directory_loads_as_source_bundle(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pending_doc(root, candidate_id="candidate-1")

            bundle = load_source_bundle(root)

            self.assertEqual(len(bundle.documents), 1)
            doc = bundle.documents[0]
            self.assertEqual(doc.docid, "candidate-1")
            self.assertEqual(doc.title, "DEVICE_SEQ 人工补充")
            self.assertEqual(bundle.sub_kbs["candidate-1"], "testing")
            self.assertEqual(doc.metadata["system"], "pending")
            self.assertIn("PENDING_DEVICE_SEQ_RULE", doc.body)

    def test_combined_local_index_can_search_approved_pending_doc(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending_root = root / "pending-docs"
            _write_pending_doc(pending_root, candidate_id="candidate-2")
            db_path = root / "index.sqlite3"

            summary = build_local_index(WIKI_MANIFEST, db_path, include_paths=(pending_root,))
            stats = local_index_stats(db_path)
            service = OfflineKbService(
                fixture_path=str(WIKI_MANIFEST),
                aliases_path=str(ALIASES),
                index_db_path=str(db_path),
            )
            answer = service.ask("PENDING_DEVICE_SEQ_RULE 是什么？", sub_kbs={"testing"}, top_k=1)

            self.assertEqual(summary.source_documents, WIKI_DOC_COUNT + 1)
            self.assertEqual(stats["source_documents"], WIKI_DOC_COUNT + 1)
            self.assertFalse(answer.refused)
            self.assertEqual(answer.citations[0].docid, "candidate-2")
            self.assertIn("pending/candidate-2", answer.answer)


def _write_manifest(root: Path, body_path: Path) -> Path:
    manifest_path = root / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "documents:",
                '  - docid: "1000000014"',
                "    sub_kb_id: testing",
                "    title: 示例UDT自动化测试使用说明",
                "    content_type: DOC",
                "    url: https://wiki.example.com/p/1000000014",
                f"    body_path: {body_path.name}",
                "    metadata:",
                '      last_modified: "2026-06-11 10:00:00"',
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path


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
                'approved_at: "2026-06-11T08:15:36Z"',
                "---",
                "",
                "# DEVICE_SEQ 人工补充",
                "",
                "PENDING_DEVICE_SEQ_RULE 表示审核通过后进入 pending docs 的测试设备序号规则。",
            ]
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
