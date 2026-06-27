import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.models import AtomDraft
from codekb.retrieval import QdrantHybridLiteRetriever, QdrantLiteRetriever, hashed_lexical_vector
from codekb.service import OfflineKbService
from codekb.store import InMemoryAtomStore


class QdrantRetrievalTests(unittest.TestCase):
    def test_qdrant_retriever_queries_points_and_maps_atom_records(self):
        store = InMemoryAtomStore()
        record = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="1000000014",
                source_title="UDT 自动化测试说明",
                source_anchor="device-seq",
                section_path=("UDT", "DEVICE_SEQ"),
                text="DEVICE_SEQ 是 UDT 自动化测试里指定设备序列的参数。",
                contextual_prefix="UDT 运行参数",
            )
        )
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append({"path": self.path, "payload": payload, "api_key": self.headers.get("api-key")})
                body = json.dumps(
                    {
                        "result": {
                            "points": [
                                {
                                    "id": record.atom_id,
                                    "score": 0.91,
                                    "payload": {"sub_kb_id": "testing"},
                                }
                            ]
                        }
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = QdrantLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                api_key="secret-key",
                collection="codekb_atoms",
                timeout_seconds=2,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=3)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.retriever, "qdrant-lite")
        self.assertEqual(result.top_atoms[0].atom.atom_id, record.atom_id)
        self.assertEqual(result.top_atoms[0].score, 0.91)
        self.assertEqual(result.dense_hits, (record.atom_id,))
        self.assertEqual(calls[0]["path"], "/collections/codekb_atoms/points/query")
        self.assertEqual(calls[0]["api_key"], "secret-key")
        self.assertEqual(len(calls[0]["payload"]["query"]), 64)
        self.assertEqual(calls[0]["payload"]["limit"], 20)
        self.assertEqual(
            calls[0]["payload"]["filter"],
            {"must": [{"key": "sub_kb_id", "match": {"any": ["testing"]}}]},
        )

    def test_embedder_failure_falls_back_to_bm25(self):
        store = InMemoryAtomStore()
        store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="1000000014",
                source_title="UDT 自动化测试说明",
                source_anchor="device-seq",
                section_path=("UDT", "DEVICE_SEQ"),
                text="DEVICE_SEQ 是 UDT 自动化测试里指定设备序列的参数。",
                contextual_prefix="UDT 运行参数",
            )
        )

        class _RaisingEmbedder:
            dimensions = 64
            model_id = "raises"

            def embed_query(self, text):
                raise RuntimeError("remote embedding down")

            def embed_documents(self, texts):
                raise RuntimeError("remote embedding down")

        # 这里压根不会发请求：embed_query 先抛错，直接走 BM25 兜底。
        result = QdrantLiteRetriever(
            store,
            url="http://127.0.0.1:9",
            collection="codekb_atoms",
            timeout_seconds=1,
            embedder=_RaisingEmbedder(),
        ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=3)

        self.assertEqual(result.retriever, "qdrant-lite")
        self.assertTrue(result.top_atoms)
        self.assertEqual(getattr(result, "fallback", None), "bm25")

    def test_hashed_lexical_vector_is_normalized_and_stable(self):
        left = hashed_lexical_vector("DEVICE_SEQ 参数")
        right = hashed_lexical_vector("DEVICE_SEQ 参数")

        self.assertEqual(left, right)
        self.assertEqual(len(left), 64)
        self.assertAlmostEqual(sum(value * value for value in left), 1.0, places=5)

    def test_qdrant_retriever_reranks_dense_hits_by_query_overlap(self):
        store = InMemoryAtomStore()
        broad = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-1",
                source_title="多机联调",
                source_anchor="multi-device",
                section_path=("联调",),
                text="主脚本和子脚本进入房间后开启游戏。",
                contextual_prefix="性能脚本联调",
            )
        )
        exact = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-2",
                source_title="UDT 参数",
                source_anchor="device-seq",
                section_path=("参数",),
                text="DEVICE_SEQ 是平台内置环境变量，表示当前设备序号。",
                contextual_prefix="UDT DEVICE_SEQ 参数说明",
            )
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps(
                    {
                        "result": {
                            "points": [
                                {"id": broad.atom_id, "score": 0.99},
                                {"id": exact.atom_id, "score": 0.88},
                            ]
                        }
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = QdrantLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                collection="codekb_atoms",
                timeout_seconds=2,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=1)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.top_atoms[0].atom.atom_id, exact.atom_id)

    def test_qdrant_hybrid_combines_vector_and_bm25_precision(self):
        store = InMemoryAtomStore()
        broad = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-1",
                source_title="多机联调",
                source_anchor="multi-device",
                section_path=("联调",),
                text="主脚本和子脚本进入房间后开启游戏。",
                contextual_prefix="性能脚本联调",
            )
        )
        exact = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-2",
                source_title="UDT 参数",
                source_anchor="device-seq",
                section_path=("参数",),
                text="DEVICE_SEQ 是平台内置环境变量，表示当前设备序号。",
                contextual_prefix="UDT DEVICE_SEQ 参数说明",
            )
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps(
                    {
                        "result": {
                            "points": [
                                {"id": broad.atom_id, "score": 0.99},
                                {"id": exact.atom_id, "score": 0.50},
                            ]
                        }
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = QdrantHybridLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                collection="codekb_atoms",
                timeout_seconds=2,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=1)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.retriever, "qdrant-hybrid-lite")
        self.assertEqual(result.top_atoms[0].atom.atom_id, exact.atom_id)
        self.assertIn(exact.atom_id, result.sparse_hits)
        self.assertIn(broad.atom_id, result.dense_hits)



    def test_qdrant_retriever_uses_injected_embedder_dimensions(self):
        store = InMemoryAtomStore()
        store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-1",
                source_title="UDT 参数",
                source_anchor="device-seq",
                section_path=("参数",),
                text="DEVICE_SEQ 是平台内置环境变量。",
                contextual_prefix="UDT DEVICE_SEQ 参数说明",
            )
        )
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(payload)
                body = json.dumps({"result": {"points": []}}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        class StubEmbedder:
            dimensions = 8
            model_id = "stub-embedder"

            def embed_query(self, text):
                return [1.0] * self.dimensions

            def embed_documents(self, texts):
                return [self.embed_query(text) for text in texts]

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            QdrantLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                collection="codekb_atoms",
                timeout_seconds=2,
                embedder=StubEmbedder(),
                enable_bm25_fallback=False,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=3)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(len(calls[0]["query"]), 8)

    def test_qdrant_lite_empty_recall_falls_back_to_bm25(self):
        store = InMemoryAtomStore()
        record = store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-2",
                source_title="UDT 参数",
                source_anchor="device-seq",
                section_path=("参数",),
                text="DEVICE_SEQ 是平台内置环境变量，表示当前设备序号。",
                contextual_prefix="UDT DEVICE_SEQ 参数说明",
            )
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({"result": {"points": []}}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = QdrantLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                collection="codekb_atoms",
                timeout_seconds=2,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=3)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.retriever, "qdrant-lite")
        self.assertEqual(result.fallback, "bm25")
        self.assertEqual(result.dense_hits, ())
        self.assertTrue(result.top_atoms)
        self.assertEqual(result.top_atoms[0].atom.atom_id, record.atom_id)

    def test_qdrant_lite_fallback_can_be_disabled(self):
        store = InMemoryAtomStore()
        store.upsert_draft(
            AtomDraft(
                sub_kb_id="testing",
                source_docid="doc-3",
                source_title="UDT 参数",
                source_anchor="device-seq",
                section_path=("参数",),
                text="DEVICE_SEQ 是平台内置环境变量。",
                contextual_prefix="UDT DEVICE_SEQ 参数说明",
            )
        )

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({"result": {"points": []}}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = QdrantLiteRetriever(
                store,
                url=f"http://127.0.0.1:{server.server_port}",
                collection="codekb_atoms",
                timeout_seconds=2,
                enable_bm25_fallback=False,
            ).retrieve("DEVICE_SEQ 是什么", sub_kbs={"testing"}, top_k=3)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(result.retriever, "qdrant-lite")
        self.assertEqual(result.fallback, "")
        self.assertEqual(result.top_atoms, ())


if __name__ == "__main__":
    unittest.main()
