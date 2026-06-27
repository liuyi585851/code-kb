import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import AtomDraft
from codekb.rerank import GatewayReranker, LocalBgeReranker, resolve_reranker
from codekb.retrieval import _RERANK_CANDIDATES, _rerank_candidates
from codekb.store import InMemoryAtomStore


class ResolveRerankerTests(unittest.TestCase):
    def test_default_is_none(self):
        self.assertIsNone(resolve_reranker(env={}))
        self.assertIsNone(resolve_reranker(env={"CODEKB_RERANK_PROVIDER": "none"}))

    def test_local_provider_builds_lazily(self):
        r = resolve_reranker(
            env={"CODEKB_RERANK_PROVIDER": "local", "CODEKB_RERANK_MODEL": "BAAI/bge-reranker-v2-m3"}
        )
        self.assertIsInstance(r, LocalBgeReranker)
        self.assertEqual(r.model_id, "BAAI/bge-reranker-v2-m3")


class RerankCandidatesTests(unittest.TestCase):
    def _two_atoms(self):
        store = InMemoryAtomStore()
        ra = store.upsert_draft(
            AtomDraft(sub_kb_id="t", source_docid="d", source_title="T", source_anchor="a",
                      section_path=("a",), text="alpha device seq", contextual_prefix="")
        )
        rb = store.upsert_draft(
            AtomDraft(sub_kb_id="t", source_docid="d", source_title="T", source_anchor="b",
                      section_path=("b",), text="beta unrelated", contextual_prefix="")
        )
        return {ra.atom_id: ra, rb.atom_id: rb}, (ra.atom_id, rb.atom_id)

    def test_reranker_reorders(self):
        atom_by_id, ids = self._two_atoms()

        class _Reverse:
            model_id = "f"

            def rerank(self, query, documents, *, top_n=None):
                return [(len(documents) - 1 - i, 1.0) for i in range(len(documents))]

        out = _rerank_candidates("q", ids, atom_by_id, aliases={}, reranker=_Reverse())
        self.assertEqual(out, (ids[1], ids[0]))

    def test_reranker_error_falls_back(self):
        atom_by_id, ids = self._two_atoms()

        class _Boom:
            model_id = "f"

            def rerank(self, *a, **k):
                raise RuntimeError("reranker down")

        out = _rerank_candidates("alpha", ids, atom_by_id, aliases={}, reranker=_Boom())
        self.assertEqual(set(out), set(ids))  # 不崩，原样返回所有 id

    def test_no_reranker_uses_lexical(self):
        atom_by_id, ids = self._two_atoms()
        out = _rerank_candidates("alpha device", ids, atom_by_id, aliases={}, reranker=None)
        self.assertEqual(set(out), set(ids))

    def test_candidate_cap_limits_reranker_and_keeps_tail(self):
        store = InMemoryAtomStore()
        ids = []
        atom_by_id = {}
        for i in range(_RERANK_CANDIDATES + 6):
            rec = store.upsert_draft(
                AtomDraft(sub_kb_id="t", source_docid="d", source_title="T", source_anchor=f"a{i}",
                          section_path=(f"a{i}",), text=f"doc {i} alpha", contextual_prefix="")
            )
            ids.append(rec.atom_id)
            atom_by_id[rec.atom_id] = rec
        seen = {}

        class _Rev:
            model_id = "f"

            def rerank(self, query, documents, *, top_n=None):
                seen["n"] = len(documents)
                return [(len(documents) - 1 - i, 1.0) for i in range(len(documents))]

        out = _rerank_candidates("q", tuple(ids), atom_by_id, aliases={}, reranker=_Rev())
        self.assertEqual(seen["n"], _RERANK_CANDIDATES)  # 只对头部打分
        self.assertEqual(set(out), set(ids))  # 候选一个不丢
        self.assertEqual(out[-6:], tuple(ids[_RERANK_CANDIDATES:]))  # 尾部维持 RRF 原序


class LocalRerankerQuantizeTests(unittest.TestCase):
    def test_quantize_default_on_and_env_off(self):
        self.assertTrue(LocalBgeReranker.from_env(env={})._quantize)
        self.assertFalse(LocalBgeReranker.from_env(env={"CODEKB_RERANK_QUANTIZE": "0"})._quantize)


class GatewayRerankerTests(unittest.TestCase):
    def test_resolve_gateway_requires_endpoint(self):
        self.assertIsNone(resolve_reranker(env={"CODEKB_RERANK_PROVIDER": "gateway"}))
        r = resolve_reranker(
            env={"CODEKB_RERANK_PROVIDER": "gateway", "CODEKB_RERANK_ENDPOINT": "http://gw/api/llmproxy"}
        )
        self.assertIsInstance(r, GatewayReranker)

    def test_rerank_parses_results_and_sorts(self):
        captured = {}

        def fake_post(url, body):
            captured["url"] = url
            captured["body"] = body
            return {"results": [{"index": 0, "relevance_score": 0.1}, {"index": 2, "relevance_score": 0.9}]}

        r = GatewayReranker(endpoint="http://gw/api/llmproxy", model="qwen3-reranker-8b", post_json=fake_post)
        out = r.rerank("q", ["a", "b", "c"], top_n=2)
        self.assertTrue(captured["url"].endswith("/rerank"))
        self.assertEqual(captured["body"]["top_n"], 2)
        self.assertEqual(out, [(2, 0.9), (0, 0.1)])

    def test_default_post_serializes_and_parses(self):
        # 走真实的 _default_post 路径（json.dumps/loads），这样漏写
        # `import json`（或序列化/解析退化）能被这条用例兜住。
        import urllib.request
        from unittest.mock import patch

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"results":[{"index":1,"relevance_score":0.7},{"index":0,"relevance_score":0.2}]}'

        with patch.object(urllib.request, "urlopen", return_value=_Resp()):
            r = GatewayReranker(endpoint="http://gw/api/llmproxy", api_key="k")
            out = r.rerank("q", ["a", "b"])
        self.assertEqual(out, [(1, 0.7), (0, 0.2)])

    def test_network_error_raises_runtimeerror(self):
        def boom(url, body):
            raise OSError("down")

        r = GatewayReranker(endpoint="http://gw", post_json=boom)
        with self.assertRaises(RuntimeError):
            r.rerank("q", ["a"])


if __name__ == "__main__":
    unittest.main()
