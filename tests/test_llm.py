import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.llm import (
    EchoLlmClient,
    GenerationRequest,
    GenerationResult,
    LlmClient,
    build_constrained_context,
)
from codekb.models import CitationPack


def _pack(idx: int, docid: str = "1000000014") -> CitationPack:
    return CitationPack(
        atom_id=f"atom-{idx}",
        docid=docid,
        title=f"标题{idx}",
        anchor=f"a{idx}",
        section_path=("参数", f"小节{idx}"),
        quote=f"引用内容 {idx}",
        score=1.0 / idx,
    )


class GenerationDataclassTests(unittest.TestCase):
    def test_request_defaults(self):
        req = GenerationRequest(system="s", prompt="p")
        self.assertEqual(req.max_tokens, 16000)
        self.assertEqual(req.timeout_seconds, 30.0)

    def test_result_defaults(self):
        res = GenerationResult(text="hi")
        self.assertEqual(res.model, "")
        self.assertEqual(res.input_tokens, 0)
        self.assertEqual(res.output_tokens, 0)
        self.assertEqual(res.finish_reason, "")


class EchoLlmClientTests(unittest.TestCase):
    def test_satisfies_protocol(self):
        self.assertIsInstance(EchoLlmClient(), LlmClient)

    def test_echoes_prompt_by_default(self):
        client = EchoLlmClient()
        req = GenerationRequest(system="sys", prompt="hello world")
        res = client.generate(req)
        self.assertEqual(res.text, "hello world")
        self.assertEqual(res.model, "echo")
        self.assertEqual(res.finish_reason, "stop")
        self.assertEqual(res.output_tokens, 2)
        self.assertEqual(res.input_tokens, 2)

    def test_deterministic(self):
        client = EchoLlmClient()
        req = GenerationRequest(system="sys", prompt="abc def ghi")
        self.assertEqual(client.generate(req), client.generate(req))

    def test_template_render(self):
        client = EchoLlmClient(template="[{system}] {prompt}")
        req = GenerationRequest(system="S", prompt="P")
        res = client.generate(req)
        self.assertEqual(res.text, "[S] P")

    def test_no_network_import(self):
        # EchoLlmClient 不能引入任何联网 SDK。
        import codekb.llm as mod

        self.assertNotIn("requests", dir(mod))
        self.assertNotIn("anthropic", dir(mod))


class ConstrainedContextTests(unittest.TestCase):
    def test_single_citation(self):
        text = build_constrained_context([_pack(1)])
        self.assertEqual(
            text,
            "[1] doc/1000000014《标题1》#参数 / 小节1\n引用内容 1",
        )

    def test_multiple_citations_blocks(self):
        text = build_constrained_context([_pack(1), _pack(2)])
        self.assertIn("[1] doc/1000000014", text)
        self.assertIn("[2] doc/1000000014", text)
        self.assertEqual(text.count("\n\n"), 1)

    def test_empty(self):
        self.assertEqual(build_constrained_context([]), "")

    def test_non_digit_docid_pending_label(self):
        text = build_constrained_context([_pack(1, docid="ISSUE_TRACKER-12")])
        self.assertIn("[1] pending/ISSUE_TRACKER-12", text)


if __name__ == "__main__":
    unittest.main()
