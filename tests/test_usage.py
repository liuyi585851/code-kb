import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.usage import record_event, summarize_usage


class UsageTests(unittest.TestCase):
    def test_record_and_summarize(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "usage.jsonl")
            record_event("search_code", source="http", query="login", results=3, latency_ms=120, path=p)
            record_event("search_code", source="web", query="empty", results=0, latency_ms=80, path=p)
            record_event("ask", source="mcp", results=2, refused=False, latency_ms=500, path=p)
            s = summarize_usage(p)
            self.assertEqual(s["total"], 3)
            tools = {t["tool"]: t for t in s["by_tool"]}
            self.assertEqual(tools["search_code"]["count"], 2)
            self.assertEqual(tools["search_code"]["empty"], 1)  # results==0 counts as empty
            self.assertEqual(tools["search_code"]["empty_rate"], 0.5)
            self.assertEqual(s["by_source"]["web"], 1)
            self.assertEqual(len(s["recent"]), 3)
            self.assertEqual(s["recent"][0]["tool"], "ask")  # most recent first

    def test_no_path_is_noop(self):
        record_event("x", path="")  # must not raise
        self.assertEqual(summarize_usage("")["total"], 0)


if __name__ == "__main__":
    unittest.main()
