import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.diagnosis_context import DiagnosticContext, build_diagnostic_query, parse_diagnostic_context


class DiagnosisContextTests(unittest.TestCase):
    def test_parse_context_normalizes_tags_and_links(self):
        context = parse_diagnostic_context(
            {
                "surface": " code_review ",
                "repo": "ym/app",
                "branch": "main",
                "error_code": "DEVICE_SEQ",
                "error_text": " DEVICE_SEQ 构建失败 ",
                "tags": ["ci", "ci", "udt"],
                "links": {"mr": "https://example.invalid/mr/1"},
            }
        )

        self.assertEqual(context.surface, "code_review")
        self.assertEqual(context.repo, "ym/app")
        self.assertEqual(context.error_text, "DEVICE_SEQ 构建失败")
        self.assertEqual(context.tags, ("ci", "udt"))
        self.assertEqual(context.links, {"mr": "https://example.invalid/mr/1"})
        self.assertEqual(context.to_dict()["tags"], ["ci", "udt"])

    def test_build_query_can_derive_from_error_context(self):
        context = DiagnosticContext(
            surface="ci",
            repo="ym/app",
            branch="main",
            build_id="build-1",
            error_code="DEVICE_SEQ",
            error_text="DEVICE_SEQ 构建失败，需要排查 UDT 参数",
            log_excerpt="Traceback\nmissing DEVICE_SEQ",
            tags=("udt",),
        )

        query = build_diagnostic_query("", context)

        self.assertIn("DEVICE_SEQ 构建失败", query)
        self.assertIn("repo=ym/app", query)
        self.assertIn("log_excerpt=Traceback | missing DEVICE_SEQ", query)

    def test_explicit_query_wins_over_context(self):
        context = DiagnosticContext(error_code="DEVICE_SEQ", error_text="ignored")

        query = build_diagnostic_query("流水线失败怎么排查？", context)

        self.assertEqual(query, "流水线失败怎么排查？")

    def test_explicit_query_is_redacted(self):
        query = build_diagnostic_query("流水线失败 password=abc123", DiagnosticContext())

        self.assertIn("password=[REDACTED]", query)
        self.assertNotIn("abc123", query)

    def test_context_redacts_sensitive_values_before_query_and_metadata(self):
        context = parse_diagnostic_context(
            {
                "error_code": "BUILD_FAIL",
                "error_text": "build failed password=abc123",
                "log_excerpt": "Authorization: Bearer secret-token-value\nACCOUNT_TOKEN is safe",
                "links": {"build": "https://example.invalid/build?id=1&token=abc123"},
            }
        )

        query = build_diagnostic_query("", context)

        self.assertNotIn("abc123", context.error_text)
        self.assertNotIn("secret-token-value", context.log_excerpt)
        self.assertIn("ACCOUNT_TOKEN", context.log_excerpt)
        self.assertNotIn("abc123", context.links["build"])
        self.assertNotIn("abc123", query)
        self.assertNotIn("secret-token-value", query)

    def test_blank_query_requires_error_context(self):
        with self.assertRaisesRegex(ValueError, "query is required"):
            build_diagnostic_query("", DiagnosticContext(repo="ym/app"))

    def test_rejects_invalid_context_shape(self):
        with self.assertRaisesRegex(ValueError, "context must be a JSON object"):
            parse_diagnostic_context(["bad"])
        with self.assertRaisesRegex(ValueError, "context.links"):
            parse_diagnostic_context({"links": ["bad"]})


if __name__ == "__main__":
    unittest.main()
