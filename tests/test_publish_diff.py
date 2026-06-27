import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.publish import PublishOperation, build_publish_plans, write_publish_outbox
from codekb.publish_api import plan_publish_outbox
from codekb.publish_diff import publish_diff


class FakeReadClient:
    def __init__(self, bodies):
        self.bodies = bodies
        self.reads = []

    def get_document(self, docid):
        self.reads.append(str(docid))
        return self.bodies.get(str(docid), "")

    def metadata(self, docid):
        return {"contentid": str(docid)}


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
                "# Diff 测试",
                "",
                "DIFF_RULE 表示发布 diff 预览测试。",
            ]
        ),
        encoding="utf-8",
    )
    return root


class PublishDiffTests(unittest.TestCase):
    def test_diff_against_existing_document(self):
        operation = PublishOperation(tool="saveDocument", params={"docid": "555", "title": "t", "body": "b"}, risk="r")
        read_client = FakeReadClient({"555": "line one\nold line\n"})

        result = publish_diff(operation, "line one\nnew line\n", read_client=read_client)

        self.assertEqual(result["mode"], "diff")
        self.assertEqual(result["docid"], "555")
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertIn("+new line", result["diff"])
        self.assertIn("-old line", result["diff"])
        self.assertEqual(read_client.reads, ["555"])

    def test_missing_read_client_degrades_to_full_insert(self):
        operation = PublishOperation(tool="saveDocument", params={"docid": "555", "title": "t", "body": "b"}, risk="r")

        result = publish_diff(operation, "alpha\nbeta\n", read_client=None)

        self.assertEqual(result["mode"], "full_insert")
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["removed"], 0)
        self.assertIn("+alpha", result["diff"])

    def test_placeholder_docid_degrades_to_full_insert(self):
        operation = PublishOperation(
            tool="saveDocument", params={"docid": "<copied_docid>", "title": "t", "body": "b"}, risk="r"
        )
        read_client = FakeReadClient({"123": "should not be read"})

        result = publish_diff(operation, "alpha\n", read_client=read_client)

        self.assertEqual(result["mode"], "full_insert")
        self.assertEqual(result["added"], 1)
        self.assertEqual(read_client.reads, [])  # 占位符解析不出来 -> 不会去读

    def test_save_document_parts_uses_id_param(self):
        operation = PublishOperation(tool="saveDocumentParts", params={"id": "401", "title": "idx"}, risk="r")
        read_client = FakeReadClient({"401": "existing index\n"})

        result = publish_diff(operation, "existing index\nappended entry\n", read_client=read_client)

        self.assertEqual(result["mode"], "diff")
        self.assertEqual(result["docid"], "401")
        self.assertEqual(result["added"], 1)

    def test_non_diffable_tool(self):
        operation = PublishOperation(tool="manual_publish", params={"title": "t", "body": "b"}, risk="r")
        result = publish_diff(operation, "body", read_client=None)
        self.assertEqual(result["mode"], "not_applicable")
        self.assertEqual(result["added"], 0)

    def test_plan_publish_outbox_includes_diff_preview_when_requested(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(root, candidate_id="candidate-diff")
            outbox = root / "outbox" / "publish.jsonl"

            without = plan_publish_outbox(pending, outbox, {"mode": "index_page", "index_docid": "401"})
            self.assertNotIn("diff_preview", without)

            read_client = FakeReadClient({"401": "old index body\n"})
            with_diff = plan_publish_outbox(
                pending,
                root / "outbox" / "publish2.jsonl",
                {"mode": "index_page", "index_docid": "401"},
                include_diff=True,
                read_client=read_client,
            )
            self.assertIn("diff_preview", with_diff)
            self.assertEqual(len(with_diff["diff_preview"]), 1)
            ops = with_diff["diff_preview"][0]["operations"]
            self.assertEqual(ops[0]["tool"], "saveDocumentParts")
            self.assertIn(ops[0]["mode"], {"diff", "full_insert"})


if __name__ == "__main__":
    unittest.main()
