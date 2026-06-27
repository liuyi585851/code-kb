import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.code_chunker import chunk_code, markdown_to_drafts
from codekb.code_location import parse_code_location
from codekb.code_nav import file_outline, find_files, get_symbol, list_dir, read_file_range, search_code
from codekb.models import RetrievalResult, RetrievedAtom
from codekb.retrieval import tokenize
from codekb.store import InMemoryAtomStore


def _code_store():
    store = InMemoryAtomStore()
    text = (
        "def alpha(x):\n" + "".join(f"    v{i}=x+{i}\n" for i in range(120))
        + "\ndef beta(y):\n" + "".join(f"    w{i}=y*{i}\n" for i in range(120))
    )
    for draft in chunk_code(text, repo="AIKnowledge", rel_path="Source/m.py"):
        store.upsert_draft(draft)
    return store


class _FakeRetriever:
    def __init__(self, atoms):
        self._atoms = atoms

    def retrieve(self, query, *, sub_kbs=None, top_k=4):
        hits = tuple(
            RetrievedAtom(atom=a, score=1.0 - i * 0.1, matched_terms=())
            for i, a in enumerate(self._atoms[:top_k])
        )
        return RetrievalResult(query=query, top_atoms=hits)


class CodeNavTests(unittest.TestCase):
    def test_search_code_returns_located_hits(self):
        store = _code_store()
        atoms = store.list_atoms()
        out = search_code(_FakeRetriever(atoms), "alpha", top_k=5)
        self.assertEqual(out["sub_kbs"], ["code", "docs"])
        self.assertTrue(out["hits"])
        h = out["hits"][0]
        self.assertEqual(h["file_path"], "AIKnowledge/Source/m.py")
        self.assertTrue(h["start_line"] >= 1)
        self.assertEqual(h["language"], "python")

    def test_get_symbol_matches_by_name(self):
        store = _code_store()
        out = get_symbol(_FakeRetriever(store.list_atoms()), "beta", top_k=5)
        self.assertTrue(out["exact_symbol_match"])
        self.assertTrue(all("beta" in m["symbol"] for m in out["matches"]))

    def test_find_files_matches_path_substring(self):
        store = _code_store()  # source_docid = AIKnowledge/Source/m.py
        out = find_files(store, "m.py")
        self.assertIn("AIKnowledge/Source/m.py", out["files"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(find_files(store, "no-such-file")["count"], 0)

    def test_find_files_token_and_match(self):
        store = _code_store()  # AIKnowledge/Source/m.py
        # 多词(按斜杠/空格分隔)要求每个词都命中,不要求相邻
        self.assertEqual(find_files(store, "AIKnowledge/m.py")["count"], 1)
        self.assertEqual(find_files(store, "source m.py")["count"], 1)
        self.assertEqual(find_files(store, "AIKnowledge nope")["count"], 0)

    def test_list_dir_browses_tree(self):
        store = _code_store()  # AIKnowledge/Source/m.py
        self.assertIn("AIKnowledge", list_dir(store, "")["dirs"])
        self.assertIn("AIKnowledge/Source", list_dir(store, "AIKnowledge")["dirs"])
        self.assertIn("AIKnowledge/Source/m.py", list_dir(store, "AIKnowledge/Source")["files"])

    def test_read_file_range_returns_overlapping_segments(self):
        store = _code_store()
        out = read_file_range(store, "AIKnowledge/Source/m.py", 1, 5)
        self.assertGreaterEqual(out["found"], 1)
        self.assertTrue(out["segments"][0]["start_line"] <= 5)

    def test_file_outline_lists_symbols(self):
        store = _code_store()
        out = file_outline(store, "AIKnowledge/Source/m.py")
        self.assertEqual(out["language"], "python")
        self.assertGreaterEqual(out["count"], 1)
        self.assertEqual(out["symbols"], sorted(out["symbols"], key=lambda s: s["start_line"]))


class MarkdownRoutingTests(unittest.TestCase):
    def test_markdown_becomes_doc_atoms_not_code(self):
        md = "# 标题\n\n这是关于设备的说明文档内容足够长以便成段。\n\n## 子节\n\n更多正文内容在这里足够长。\n"
        drafts = markdown_to_drafts("AIKnowledge", "docs/guide.md", md)
        self.assertTrue(drafts)
        for d in drafts:
            self.assertEqual(d.sub_kb_id, "docs")
            self.assertIsNone(parse_code_location(d))  # 文档不是代码(没有 #L 锚点)
            self.assertFalse(d.text.startswith("« 代码大仓"))  # 没有代码头
        self.assertEqual(drafts[0].source_docid, "AIKnowledge/docs/guide.md")


class IdentifierTokenizeTests(unittest.TestCase):
    def test_camel_and_snake_split_additive(self):
        toks = tokenize("getUserName device_seq AuthSDKError")
        for expected in ["getusername", "get", "user", "name", "device_seq", "device", "seq", "authsdkerror", "auth", "sdk", "error"]:
            self.assertIn(expected, toks, expected)

    def test_plain_words_and_cjk_unchanged(self):
        toks = tokenize("登录 login")
        self.assertIn("login", toks)
        self.assertIn("登", toks)
        # 普通小写词不会拆出额外的子词
        self.assertEqual(toks.count("login"), 1)


if __name__ == "__main__":
    unittest.main()
