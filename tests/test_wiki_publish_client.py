import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.wiki_publish_client import HttpWikiPublishClient


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, *, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": dict(body)})
        return dict(self.response)


class HttpWikiPublishClientTests(unittest.TestCase):
    def test_save_document_parts_maps_request_fields(self):
        transport = FakeTransport({"ok": True})
        client = HttpWikiPublishClient(base_url="https://wiki.example/api", token="t0ken", transport=transport)

        response = client.save_document_parts(id=401, title="索引页", after="\n- entry\n", before="")

        self.assertEqual(response, {"ok": True})
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://wiki.example/api/saveDocumentParts")
        self.assertEqual(call["headers"]["Authorization"], "Bearer t0ken")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["body"], {"id": 401, "title": "索引页", "after": "\n- entry\n", "before": ""})

    def test_copy_document_maps_fields_and_parses_contentid(self):
        transport = FakeTransport({"contentid": 987, "ok": True})
        client = HttpWikiPublishClient(base_url="https://wiki.example/api", token="", transport=transport)

        response = client.copy_document(docid=402, new_parentid=403, is_single=1, language="zh_CN")

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://wiki.example/api/copyDocument")
        self.assertNotIn("Authorization", call["headers"])  # 没配 token
        self.assertEqual(
            call["body"],
            {"docid": 402, "new_parentid": 403, "is_single": 1, "language": "zh_CN"},
        )
        # wiki 返回的 contentid 回填到 docid,供后续 saveDocument 使用
        self.assertEqual(response["docid"], 987)
        self.assertEqual(response["contentid"], 987)

    def test_copy_document_parses_nested_and_docid_response_shapes(self):
        nested = HttpWikiPublishClient(
            base_url="https://wiki.example/api", transport=FakeTransport({"data": {"contentid": "555"}})
        )
        self.assertEqual(nested.copy_document(docid=1, new_parentid=2)["docid"], 555)

        docid_shape = HttpWikiPublishClient(
            base_url="https://wiki.example/api", transport=FakeTransport({"docid": 777})
        )
        self.assertEqual(docid_shape.copy_document(docid=1, new_parentid=2)["docid"], 777)

    def test_save_document_maps_request_fields(self):
        transport = FakeTransport({"ok": True})
        client = HttpWikiPublishClient(base_url="https://wiki.example/api/", transport=transport)

        client.save_document(docid=987, title="复制文档", body="正文内容", is_html=False, raw=True)

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://wiki.example/api/saveDocument")  # 末尾斜杠已去掉
        self.assertEqual(
            call["body"],
            {"docid": 987, "title": "复制文档", "body": "正文内容", "is_html": False, "raw": True},
        )

    def test_from_env_reads_base_url_and_token(self):
        env = {
            "CODEKB_WIKI_API_BASE_URL": "https://wiki.internal/api",
            "CODEKB_WIKI_API_TOKEN": "secret-token",
        }
        transport = FakeTransport({"ok": True})
        client = HttpWikiPublishClient.from_env(env, transport=transport)

        client.save_document_parts(id=1, title="t")

        self.assertEqual(transport.calls[0]["url"], "https://wiki.internal/api/saveDocumentParts")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer secret-token")

    def test_transport_must_return_dict(self):
        client = HttpWikiPublishClient(base_url="https://wiki.example/api", transport=lambda **kw: ["nope"])
        with self.assertRaises(ValueError):
            client.save_document(docid=1, title="t", body="b")


if __name__ == "__main__":
    unittest.main()
