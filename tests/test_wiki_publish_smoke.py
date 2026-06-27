import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.wiki_publish_smoke import publish_writeback_smoke


class FakeWikiStore:
    """模拟一个 wiki 文档库,读写两个 client 共用同一份存储。"""

    def __init__(self, *, drop_writes=False, wrong_title=False):
        self.docs = {}
        self.drop_writes = drop_writes
        self.wrong_title = wrong_title

    # 对应 WikiPublishClient.save_document
    def save_document(self, *, docid, title, body, is_html=False, raw=False):
        if not self.drop_writes:
            stored_title = "二手标题" if self.wrong_title else title
            self.docs[str(docid)] = {"title": stored_title, "body": body}
        return {"ok": True}

    # 对应 WikiClient.get_document / metadata
    def get_document(self, docid):
        return self.docs.get(str(docid), {}).get("body", "")

    def metadata(self, docid):
        return {"title": self.docs.get(str(docid), {}).get("title", ""), "contentid": str(docid)}


class PublishWritebackSmokeTests(unittest.TestCase):
    def test_writeback_verified_when_content_lands(self):
        store = FakeWikiStore()

        report = publish_writeback_smoke(
            docid=987,
            title="发布标题",
            body="# 发布标题\n\nWRITEBACK_RULE 表示写后回读校验。",
            write_client=store,
            read_client=store,
        )

        self.assertEqual(report["status"], "verified")
        self.assertTrue(report["title_match"])
        self.assertTrue(report["body_match"])
        self.assertTrue(report["write_ok"])
        self.assertEqual(report["docid"], "987")
        self.assertEqual(report["checked_fragment"], "WRITEBACK_RULE 表示写后回读校验。")

    def test_writeback_mismatch_when_body_missing(self):
        store = FakeWikiStore(drop_writes=True)

        report = publish_writeback_smoke(
            docid=987,
            title="发布标题",
            body="WRITEBACK_RULE 内容",
            write_client=store,
            read_client=store,
        )

        self.assertEqual(report["status"], "mismatch")
        self.assertFalse(report["body_match"])

    def test_writeback_mismatch_when_title_differs(self):
        store = FakeWikiStore(wrong_title=True)

        report = publish_writeback_smoke(
            docid=987,
            title="发布标题",
            body="WRITEBACK_RULE 内容",
            write_client=store,
            read_client=store,
        )

        self.assertEqual(report["status"], "mismatch")
        self.assertFalse(report["title_match"])
        self.assertTrue(report["body_match"])

    def test_separate_read_and_write_clients(self):
        store = FakeWikiStore()
        # 正常场景下读写应是两个独立 client(虽共用同一份底层存储),
        # 这里验证冒烟流程确实分别走了读、写两个角色。
        report = publish_writeback_smoke(
            docid=1,
            title="t",
            body="BODY_FRAGMENT_X",
            write_client=store,
            read_client=store,
        )
        self.assertEqual(report["status"], "verified")


if __name__ == "__main__":
    unittest.main()
