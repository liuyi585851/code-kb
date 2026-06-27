import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.chunker import Chunker
from codekb.models import RawDocument
from codekb.normalizer import DocumentNormalizer


class NormalizerChunkerTests(unittest.TestCase):
    def test_normalize_markdown_sections_and_images(self):
        raw = RawDocument(
            docid="1",
            title="测试文档",
            content_type="DOC",
            body="# 标题\n\n## 参数\n\nDEVICE_SEQ 是设备序号。\n\n![图](https://example/img.png)",
        )

        doc = DocumentNormalizer().normalize(raw)

        self.assertEqual(doc.sections[0].path, ("标题", "参数"))
        self.assertIn("DEVICE_SEQ", doc.sections[0].text)
        self.assertIn("[attachment:image", doc.sections[0].text)

    def test_chunker_creates_atom_drafts(self):
        raw = RawDocument(
            docid="1000000014",
            title="示例UDT自动化测试使用说明",
            content_type="DOC",
            body="## 环境变量\n\n" + "DEVICE_SEQ 表示当前设备序号。" * 20,
        )

        doc = DocumentNormalizer().normalize(raw)
        atoms = Chunker(min_chars=50, max_chars=180).chunk(doc, "testing")

        self.assertGreaterEqual(len(atoms), 1)
        self.assertEqual(atoms[0].source_docid, "1000000014")
        self.assertEqual(atoms[0].sub_kb_id, "testing")
        self.assertIn("环境变量", atoms[0].contextual_prefix)

    def test_txdoc_adds_warning(self):
        raw = RawDocument(docid="2", title="TXDOC", content_type="TXDOC", body="一一、、●●云云游游戏戏")

        doc = DocumentNormalizer().normalize(raw)

        self.assertIn("txdoc_text_noise_high", doc.warnings)

    def test_normalize_markdown_table_rows(self):
        raw = RawDocument(
            docid="1000000003",
            title="复盘",
            content_type="DOC",
            body=(
                "| 模块 | 核心功能项 | 严重问题复盘 |\n"
                "| - | - | - |\n"
                "| 分发平台能力 | 下载组件 | liteapp 非 wifi 无法下载，context 错误。 |\n"
            ),
        )

        doc = DocumentNormalizer().normalize(raw)

        self.assertIn("表格行：模块=分发平台能力", doc.sections[0].text)
        self.assertIn("核心功能项=下载组件", doc.sections[0].text)
        self.assertIn("liteapp 非 wifi", doc.sections[0].text)


if __name__ == "__main__":
    unittest.main()
