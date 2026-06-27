import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.redaction import REDACTED, find_sensitive_values, redact_sensitive_data, redact_sensitive_text, redact_sensitive_url


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_assignments(self):
        text = "password=abc123 api_key='key with space' token:secret-value"

        redacted = redact_sensitive_text(text)

        self.assertNotIn("abc123", redacted)
        self.assertNotIn("key with space", redacted)
        self.assertNotIn("secret-value", redacted)
        self.assertEqual(redacted.count(REDACTED), 3)

    def test_redacts_sensitive_headers(self):
        text = "Authorization: Bearer abcdefghijklmn\nCookie: sid=123\nmessage ok"

        redacted = redact_sensitive_text(text)

        self.assertNotIn("abcdefghijklmn", redacted)
        self.assertNotIn("sid=123", redacted)
        self.assertIn("message ok", redacted)

    def test_redacts_url_query_parameters(self):
        url = "https://example.invalid/build?id=1&token=abc123&branch=main"

        redacted = redact_sensitive_url(url)

        self.assertIn("id=1", redacted)
        self.assertIn("branch=main", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("token=" + REDACTED, redacted)

    def test_redacts_platform_oauth_and_signature_query_parameters(self):
        url = (
            "https://im-api.example.com/cgi-bin/gettoken?"
            "corpid=ww123&corpsecret=corp-secret&access_token=access-secret&signature=sig-secret"
        )

        redacted = redact_sensitive_url(url)

        self.assertIn("corpid=ww123", redacted)
        self.assertNotIn("corp-secret", redacted)
        self.assertNotIn("access-secret", redacted)
        self.assertNotIn("sig-secret", redacted)
        self.assertIn("corpsecret=" + REDACTED, redacted)
        self.assertIn("access_token=" + REDACTED, redacted)
        self.assertIn("signature=" + REDACTED, redacted)

    def test_redacts_json_and_cli_secret_fields(self):
        text = (
            '{"access_token":"access-secret","client_secret":"client-secret",'
            '"user_ticket":"ticket-secret","ACCOUNT_TOKEN":"field name"} '
            "--private-token cli-secret --refresh-token='refresh-secret'"
        )

        redacted = redact_sensitive_text(text)

        self.assertNotIn("access-secret", redacted)
        self.assertNotIn("client-secret", redacted)
        self.assertNotIn("ticket-secret", redacted)
        self.assertNotIn("cli-secret", redacted)
        self.assertNotIn("refresh-secret", redacted)
        self.assertIn('"access_token":"[REDACTED]"', redacted)
        self.assertIn('"client_secret":"[REDACTED]"', redacted)
        self.assertIn('"user_ticket":"[REDACTED]"', redacted)
        self.assertIn('"ACCOUNT_TOKEN":"field name"', redacted)

    def test_keeps_safe_token_like_identifier(self):
        text = "ACCOUNT_TOKEN 是一个变量名，不是 token=secret"

        redacted = redact_sensitive_text(text)

        self.assertIn("ACCOUNT_TOKEN", redacted)
        self.assertIn("token=" + REDACTED, redacted)

    def test_redacts_nested_payload_data_and_finds_sensitive_values(self):
        payload = {
            "repo": "ym/app",
            "access_token": "access-secret",
            "nested": {
                "url": "https://example.invalid/build?id=1&token=url-secret",
                "log": "Authorization: Bearer bearer-secret-value\nclient_secret=client-secret",
            },
        }

        redacted = redact_sensitive_data(payload)
        values = find_sensitive_values(payload)
        text = str(redacted)

        self.assertEqual(redacted["access_token"], REDACTED)
        self.assertNotIn("access-secret", text)
        self.assertNotIn("url-secret", text)
        self.assertNotIn("bearer-secret-value", text)
        self.assertNotIn("client-secret", text)
        self.assertIn("access-secret", values)
        self.assertIn("url-secret", values)
        self.assertIn("bearer-secret-value", values)
        self.assertIn("client-secret", values)


if __name__ == "__main__":
    unittest.main()
