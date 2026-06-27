import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.publish import build_publish_plans, process_publish_outbox, write_publish_outbox
from codekb.publish_redline import MASK, RedlineRule, publish_redline, scan_operation_redline


def _write_pending_doc(root: Path, *, candidate_id: str, body: str) -> Path:
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
                "# 红线测试",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    return root


class _FakePublishClient:
    def __init__(self):
        self.calls = []

    def save_document_parts(self, *, id, title, after="", before=""):
        self.calls.append("saveDocumentParts")
        return {"ok": True}

    def copy_document(self, *, docid, new_parentid, is_single=1, language="zh_CN"):
        self.calls.append("copyDocument")
        return {"docid": 987}

    def save_document(self, *, docid, title, body, is_html=False, raw=False):
        self.calls.append("saveDocument")
        return {"ok": True}


class PublishRedlineTests(unittest.TestCase):
    def test_publish_redline_detects_and_masks_secrets(self):
        text = "password: hunter2\ncontact bob@example.com phone 13800138000 host wiki.internal"
        result = publish_redline(text)
        self.assertTrue(result.matched)
        self.assertEqual(
            set(result.matched_rules),
            {"credential_assignment", "email", "phone_cn", "internal_domain"},
        )
        self.assertIn(MASK, result.sanitized)
        self.assertNotIn("hunter2", result.sanitized)
        self.assertNotIn("bob@example.com", result.sanitized)
        self.assertNotIn("13800138000", result.sanitized)

    def test_publish_redline_detects_full_width_colon_credential(self):
        # 中文文档常用全角冒号 U+FF1A,凭据规则必须能识别它,
        # 否则密钥会未脱敏地写出去。
        result = publish_redline("数据库密码：Hunter2_prod")
        self.assertTrue(result.matched)
        self.assertIn("credential_assignment", result.matched_rules)
        self.assertNotIn("Hunter2_prod", result.sanitized)

    def test_publish_redline_clean_text_passes(self):
        result = publish_redline("PUBLISH_RULE 表示发布计划测试规则。")
        self.assertFalse(result.matched)
        self.assertEqual(result.matched_rules, [])

    def test_empty_rules_disables_scanning(self):
        result = publish_redline("password: hunter2", rules=())
        self.assertFalse(result.matched)
        self.assertEqual(result.sanitized, "password: hunter2")

    def test_scan_operation_redline_covers_params(self):
        result = scan_operation_redline("clean body", {"title": "ok", "after": "token=abcdef123"})
        self.assertTrue(result.matched)
        self.assertIn("credential_assignment", result.matched_rules)

    def test_process_publish_outbox_blocks_redline_before_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(
                root, candidate_id="candidate-redline", body="联系人邮箱 secret-owner@example.com 负责审批。"
            )
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                confirm_real_publish=True,
            )

            self.assertEqual(report.status, "blocked")
            self.assertEqual(report.blocked_operations, 1)
            self.assertEqual(report.executed_operations, 0)
            operation = report.results[0].operations[0]
            self.assertEqual(operation.status, "blocked_redline")
            self.assertIn("email", operation.detail)
            self.assertEqual(client.calls, [])  # 根本没走到客户端
            # 报告里带的是脱敏后的预览,不是明文密钥。
            serialized = json.dumps(report.to_dict(), ensure_ascii=False)
            self.assertNotIn("secret-owner@example.com", serialized)
            self.assertIn(MASK, serialized)

    def test_process_publish_outbox_redline_rules_override_allows_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = _write_pending_doc(
                root, candidate_id="candidate-allow", body="联系人邮箱 owner@example.com 负责审批。"
            )
            outbox = root / "outbox" / "publish.jsonl"
            plans = build_publish_plans(pending, mode="index_page", index_docid="401")
            write_publish_outbox(plans, outbox)
            client = _FakePublishClient()

            report = process_publish_outbox(
                outbox,
                execute=True,
                write_enabled=True,
                client=client,
                confirm_real_publish=True,
                redline_rules=(),  # 显式关闭红线检测
            )

            self.assertEqual(report.status, "executed")
            self.assertEqual(report.executed_operations, 1)
            self.assertEqual(client.calls, ["saveDocumentParts"])

    def test_custom_rule_set(self):
        rules = (RedlineRule(name="ticket", pattern=r"ISSUE_TRACKER-\d+"),)
        result = publish_redline("see ISSUE_TRACKER-12345 for details", rules=rules)
        self.assertTrue(result.matched)
        self.assertEqual(result.matched_rules, ["ticket"])
        # 默认规则不会命中这个。
        self.assertFalse(publish_redline("see ISSUE_TRACKER-12345 for details").matched)


if __name__ == "__main__":
    unittest.main()
