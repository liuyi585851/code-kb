import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.citation import build_citation_pack
from codekb.code_chunker import _is_boundary, chunk_code, walk_repo
from codekb.code_location import parse_code_location
from codekb.models import AtomDraft, AtomRecord, RetrievalResult, RetrievedAtom


def _fn(name: str, body_lines: int) -> str:
    head = f"def {name}(x):\n"
    return head + "".join(f"    a{i} = x + {i}\n" for i in range(body_lines))


class ChunkCodeTests(unittest.TestCase):
    def test_small_file_is_one_whole_chunk(self):
        text = "import os\n\n\ndef alpha(x):\n    return x + 1\n"
        drafts = chunk_code(text, repo="R", rel_path="m.py")
        self.assertEqual(len(drafts), 1)
        self.assertIn("def alpha", drafts[0].text)
        self.assertTrue(drafts[0].text.startswith("« 代码大仓 · R · m.py:L"))

    def test_functions_kept_whole_not_fragmented(self):
        text = _fn("alpha", 120) + "\n" + _fn("beta", 120)
        drafts = chunk_code(text, repo="R", rel_path="pkg/m.py")
        self.assertGreaterEqual(len(drafts), 2)
        # 每个函数各自成块,不会有块横跨两个函数
        self.assertIn("def alpha", drafts[0].text)
        self.assertNotIn("def beta", drafts[0].text)
        self.assertTrue(any("def beta" in d.text for d in drafts[1:]))

    def test_huge_function_window_split_with_symbol(self):
        text = _fn("huge", 300)
        drafts = chunk_code(text, repo="R", rel_path="m.py")
        self.assertGreaterEqual(len(drafts), 2)
        for d in drafts:
            loc = parse_code_location(d)
            self.assertIsNotNone(loc)
            self.assertIn("huge", loc.qualified_symbol)

    def test_metadata_fields_and_roundtrip(self):
        drafts = chunk_code(_fn("alpha", 30), repo="AIKnowledge", rel_path="Source/m.py")
        d = drafts[0]
        self.assertEqual(d.sub_kb_id, "code")
        self.assertEqual(d.source_docid, "AIKnowledge/Source/m.py")
        self.assertTrue(d.source_anchor.startswith("AIKnowledge/Source/m.py#L1-"))
        self.assertEqual(d.section_path[0], "AIKnowledge")
        loc = parse_code_location(d)
        self.assertEqual(loc.repo_id, "AIKnowledge")
        self.assertEqual(loc.file_path, "AIKnowledge/Source/m.py")
        self.assertEqual(loc.start_line, 1)
        self.assertEqual(loc.language, "python")
        self.assertIn("alpha", loc.qualified_symbol)

    def test_lua_and_markdown_boundaries(self):
        lua = "local function fire(self)\n    return 1\nend\n\nfunction Weapon:Reload()\n    return 2\nend\n"
        drafts = chunk_code(lua, repo="R", rel_path="w.lua")
        self.assertTrue(all(d.text.startswith("« 代码大仓") for d in drafts))
        md = "# Title\n\nsome intro text here that is long enough\n\n## Section\n\nmore body text content here\n"
        mdrafts = chunk_code(md, repo="R", rel_path="doc.md")
        self.assertTrue(parse_code_location(mdrafts[0]).language == "markdown")


class CFamilyBoundaryTests(unittest.TestCase):
    def test_detects_real_signatures(self):
        positives = [
            ("int Weapon::Fire(Target* t) {", "cpp"),
            ("void reload() {", "cpp"),
            ("class Weapon : public Actor {", "cpp"),
            ("public void DoThing(int x) {", "csharp"),
            ("    private async Task<int> Run() {", "csharp"),
            ("func (w *Weapon) Fire(t *Target) error {", "go"),
            ("func main() {", "go"),
            ("export function build(opts) {", "typescript"),
        ]
        for line, lang in positives:
            self.assertTrue(_is_boundary(line, lang), f"should be boundary: {line!r} ({lang})")

    def test_rejects_control_flow_and_calls(self):
        negatives = [
            ("if (x > 0) {", "cpp"),
            ("for (int i = 0; i < n; i++) {", "cpp"),
            ("} else {", "csharp"),
            ("while (cond) {", "go"),
            ("doStuff(x);", "cpp"),
            ("    return foo(bar);", "csharp"),
        ]
        for line, lang in negatives:
            self.assertFalse(_is_boundary(line, lang), f"should NOT be boundary: {line!r} ({lang})")

    def test_cpp_two_functions_kept_whole(self):
        def cfn(name, n):
            return f"int {name}(int x) {{\n" + "".join(f"    int v{i} = x + {i};\n" for i in range(n)) + "}\n"
        text = cfn("alpha", 120) + "\n" + cfn("beta", 120)
        drafts = chunk_code(text, repo="R", rel_path="src/m.cpp")
        self.assertGreaterEqual(len(drafts), 2)
        self.assertIn("int alpha(", drafts[0].text)
        self.assertNotIn("int beta(", drafts[0].text)


class WalkRepoTests(unittest.TestCase):
    def test_walk_prunes_and_maps_subrepo(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AIKnowledge" / "Source").mkdir(parents=True)
            (root / "AIKnowledge" / "Source" / "weapon.py").write_text(_fn("fire", 20), encoding="utf-8")
            (root / "AIKnowledge" / "node_modules").mkdir(parents=True)
            (root / "AIKnowledge" / "node_modules" / "junk.js").write_text("export const x=1\n", encoding="utf-8")
            (root / "BuildTools").mkdir(parents=True)
            (root / "BuildTools" / "tool.lua").write_text("function go()\n return 1\nend\n", encoding="utf-8")
            drafts = list(walk_repo(str(root)))
        repos = {d.section_path[0] for d in drafts}
        self.assertEqual(repos, {"AIKnowledge", "BuildTools"})
        self.assertFalse(any("node_modules" in d.source_docid for d in drafts))
        self.assertTrue(any(d.source_docid == "AIKnowledge/Source/weapon.py" for d in drafts))


class CitationCodeFieldsTests(unittest.TestCase):
    def _record(self, draft: AtomDraft) -> AtomRecord:
        now = datetime(2026, 6, 24, tzinfo=timezone.utc)
        return AtomRecord(atom_id="a1", draft=draft, created_at=now, updated_at=now)

    def test_code_atom_citation_has_location(self):
        draft = chunk_code(_fn("alpha", 20), repo="AIKnowledge", rel_path="Source/m.py")[0]
        result = RetrievalResult(
            query="q", top_atoms=(RetrievedAtom(atom=self._record(draft), score=0.9, matched_terms=()),)
        )
        pack = build_citation_pack(result)[0]
        self.assertEqual(pack.file_path, "AIKnowledge/Source/m.py")
        self.assertEqual(pack.start_line, 1)
        self.assertEqual(pack.language, "python")
        self.assertEqual(pack.repo_id, "AIKnowledge")
        self.assertEqual(pack.title, "m.py")  # 没有源标题时用文件名兜底

    def test_doc_atom_citation_has_no_code_fields(self):
        draft = AtomDraft(
            sub_kb_id="testing", source_docid="1000000014", source_title="设备文档",
            source_anchor="device-seq", section_path=("设备", "序号"),
            text="DEVICE_SEQ 是设备序号", contextual_prefix="",
        )
        result = RetrievalResult(
            query="q", top_atoms=(RetrievedAtom(atom=self._record(draft), score=0.9, matched_terms=()),)
        )
        pack = build_citation_pack(result)[0]
        self.assertEqual(pack.file_path, "")
        self.assertEqual(pack.start_line, 0)
        self.assertEqual(pack.title, "设备文档")


if __name__ == "__main__":
    unittest.main()
