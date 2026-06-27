import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import RawDocument
from codekb.pipeline import ingest_raw_document
from codekb.retrieval import Bm25LiteRetriever, tokenize
from codekb.store import InMemoryAtomStore


class StoreRetrievalTests(unittest.TestCase):
    def test_ingest_and_retrieve(self):
        store = InMemoryAtomStore()
        raw = RawDocument(
            docid="1000000014",
            title="示例UDT自动化测试使用说明",
            content_type="DOC",
            body=(
                "## UDT相关预设参数\n\n"
                "| 变量名 | 来源 | 类型 | 说明 |\n"
                "| - | - | - | - |\n"
                "| DEVICE_SEQ | 平台内置 | int | 当前设备在本次任务中的数字序号 |\n"
            ),
        )
        records = ingest_raw_document(raw, sub_kb_id="testing", store=store)

        self.assertEqual(len(store), len(records))

        result = Bm25LiteRetriever(store).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"})

        self.assertGreaterEqual(len(result.top_atoms), 1)
        self.assertEqual(result.top_atoms[0].atom.source_docid, "1000000014")
        self.assertIn("device_seq", result.top_atoms[0].matched_terms)

    def test_tokenize_keeps_identifiers_and_cjk(self):
        # 标识符感知:保留完整标识符,同时拆出子词(增量召回)。
        self.assertEqual(tokenize("DEVICE_SEQ 是什么"), ["device_seq", "device", "seq"])


if __name__ == "__main__":
    unittest.main()
