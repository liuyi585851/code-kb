import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.wiki import WikiSourceConnector


class FakeWikiClient:
    def metadata(self, docid: str):
        return {
            "contentid": int(docid),
            "title": "测试文档",
            "content_type": "DOC",
            "owner": "owner",
            "creator": "creator",
            "content_changetime": "2026-06-11 10:00:00",
            "can_edit": True,
            "spacekey": "example",
            "spaceid": 1000000002,
            "parents_obj": [{"title": "示例wiki空间"}, {"title": "AI专区"}],
        }

    def get_document(self, docid: str):
        return "# 测试文档\n\n正文"

    def list_children(self, parentid: str):
        return [{"docid": 1, "title": "child", "parentid": int(parentid), "has_children": False}]


class WikiConnectorTests(unittest.TestCase):
    def test_get_document(self):
        connector = WikiSourceConnector(FakeWikiClient())

        raw = connector.get_document("1234567")

        self.assertEqual(raw.docid, "1234567")
        self.assertEqual(raw.title, "测试文档")
        self.assertEqual(raw.metadata["system"], "wiki")
        self.assertEqual(raw.metadata["parent_path"], "示例wiki空间 / AI专区")
        self.assertTrue(raw.metadata["can_edit"])
        self.assertEqual(raw.metadata["visibility"], "wiki_acl_snapshot")
        self.assertEqual(len(raw.metadata["source_acl_hash"]), 64)

    def test_list_children(self):
        connector = WikiSourceConnector(FakeWikiClient())

        children = connector.list_children("123")

        self.assertEqual(children[0]["title"], "child")


if __name__ == "__main__":
    unittest.main()
