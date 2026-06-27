import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.query_expand import expand_query, parse_terms


class QueryExpandTests(unittest.TestCase):
    def test_parse_json_array(self):
        self.assertEqual(parse_terms('["login", "account", "MSDK"]'), ["login", "account", "msdk"])

    def test_parse_dedup_and_cap(self):
        self.assertEqual(parse_terms('["a", "a", "b", "c"]', max_terms=2), ["a", "b"])

    def test_parse_token_fallback(self):
        out = parse_terms("login account oauth")  # 不是 JSON 数组，按词元兜底切分
        self.assertIn("login", out)
        self.assertIn("oauth", out)

    def test_parse_empty(self):
        self.assertEqual(parse_terms(""), [])

    def test_expand_query_no_client_is_noop(self):
        self.assertEqual(expand_query("第三方登录", None), [])

    def test_expand_query_uses_client(self):
        class _Stub:
            def generate(self, request):
                class R:
                    text = '["login", "account", "msdk", "oauth"]'
                return R()

        self.assertEqual(expand_query("第三方登录", _Stub()), ["login", "account", "msdk", "oauth"])


if __name__ == "__main__":
    unittest.main()
