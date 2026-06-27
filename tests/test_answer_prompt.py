import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.answer_prompt import (
    REFUSAL_SENTINEL,
    CiteCheck,
    build_answer_messages,
    enforce_cite_or_die,
    extract_cited_indices,
)
from codekb.models import CitationPack


def _pack(idx: int) -> CitationPack:
    return CitationPack(
        atom_id=f"atom-{idx}",
        docid="1000000014",
        title=f"标题{idx}",
        anchor=f"a{idx}",
        section_path=("参数",),
        quote=f"引用内容 {idx}",
        score=1.0,
    )


class BuildMessagesTests(unittest.TestCase):
    def test_returns_system_and_prompt(self):
        system, prompt = build_answer_messages("什么是 X", [_pack(1), _pack(2)])
        self.assertIn(REFUSAL_SENTINEL, system)
        self.assertIn("什么是 X", prompt)
        self.assertIn("[1] doc/1000000014", prompt)
        self.assertIn("[2] doc/1000000014", prompt)
        # system 提示约束:只能引用给定的 [n],每条论断都要给出引用,否则拒答。
        self.assertIn("[n]", system)


class ExtractCitedIndicesTests(unittest.TestCase):
    def test_parses_markers(self):
        self.assertEqual(extract_cited_indices("foo [1] bar [2][3]"), {1, 2, 3})

    def test_no_markers(self):
        self.assertEqual(extract_cited_indices("no citations here"), set())


class EnforceCiteOrDieTests(unittest.TestCase):
    def test_plain_text_no_citation_is_uncited(self):
        check = enforce_cite_or_die("DEVICE_SEQ 是设备序号。", n_citations=2)
        self.assertFalse(check.ok)
        self.assertFalse(check.refused)
        self.assertTrue(check.has_uncited_claim)
        self.assertEqual(check.used_indices, ())

    def test_out_of_range(self):
        check = enforce_cite_or_die("结论是这样 [5]。", n_citations=3)
        self.assertFalse(check.ok)
        self.assertEqual(check.out_of_range, (5,))
        self.assertEqual(check.used_indices, (5,))

    def test_refusal_sentinel(self):
        check = enforce_cite_or_die("  NO_SUPPORT  ", n_citations=3)
        self.assertTrue(check.refused)
        self.assertFalse(check.ok)
        self.assertFalse(check.has_uncited_claim)
        self.assertEqual(check.used_indices, ())

    def test_every_sentence_legally_cited_is_ok(self):
        text = "DEVICE_SEQ 是设备序号 [1]。它由平台注入 [2]。"
        check = enforce_cite_or_die(text, n_citations=2)
        self.assertTrue(check.ok)
        self.assertFalse(check.refused)
        self.assertFalse(check.has_uncited_claim)
        self.assertEqual(check.out_of_range, ())
        self.assertEqual(check.used_indices, (1, 2))

    def test_marker_only_answer_is_not_ok(self):
        # 光一个 "[1]" 只有引用、没内容,不能算有效回答。
        check = enforce_cite_or_die("[1]", n_citations=2)
        self.assertFalse(check.ok)

    def test_one_uncited_sentence_among_cited(self):
        text = "第一句有引用 [1]。第二句没有引用。"
        check = enforce_cite_or_die(text, n_citations=1)
        self.assertFalse(check.ok)
        self.assertTrue(check.has_uncited_claim)

    def test_empty_text_not_ok(self):
        check = enforce_cite_or_die("   ", n_citations=2)
        self.assertFalse(check.ok)
        self.assertFalse(check.refused)

    def test_check_is_frozen(self):
        check = CiteCheck(
            ok=True,
            refused=False,
            used_indices=(1,),
            out_of_range=(),
            has_uncited_claim=False,
        )
        with self.assertRaises(Exception):
            check.ok = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
