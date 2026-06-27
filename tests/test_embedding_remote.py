import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.embedding import HashedLexicalEmbedder
from codekb.embedding_config import EmbeddingConfig, resolve_embedder
from codekb.embedding_remote import RemoteHttpEmbedder


def _make_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _EmbeddingHandlerFactory:
    """构造一个返回固定维度向量的 handler,并记录收到的请求。"""

    def __init__(self, *, dimensions: int, status: int = 200):
        self.dimensions = dimensions
        self.status = status
        self.requests = []

    def handler(self):
        factory = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                factory.requests.append(payload)
                if factory.status >= 400:
                    self.send_response(factory.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                inputs = payload.get("input", [])
                data = [
                    {"index": i, "embedding": [float(i)] * factory.dimensions}
                    for i, _ in enumerate(inputs)
                ]
                body = json.dumps({"data": data, "model": payload.get("model")}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        return Handler


class RemoteHttpEmbedderTests(unittest.TestCase):
    def test_embed_documents_parses_vectors_and_sends_model_and_input(self):
        factory = _EmbeddingHandlerFactory(dimensions=4)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/embeddings",
                api_key="secret",
                model_id="bge-m3",
                dimensions=4,
            )
            vectors = embedder.embed_documents(["alpha", "beta"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 4)
        self.assertEqual(vectors[1], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(factory.requests[0]["model"], "bge-m3")
        self.assertEqual(factory.requests[0]["input"], ["alpha", "beta"])

    def test_embed_query_returns_single_vector(self):
        factory = _EmbeddingHandlerFactory(dimensions=3)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/embeddings",
                model_id="bge-m3",
                dimensions=3,
            )
            vector = embedder.embed_query("hello")
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(len(vector), 3)

    def test_embed_documents_splits_into_batches(self):
        factory = _EmbeddingHandlerFactory(dimensions=2)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/embeddings",
                model_id="bge-m3",
                dimensions=2,
                batch_size=1,
            )
            vectors = embedder.embed_documents(["a", "b", "c"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(len(vectors), 3)
        self.assertEqual(len(factory.requests), 3)
        self.assertEqual([r["input"] for r in factory.requests], [["a"], ["b"], ["c"]])

    def test_dimension_mismatch_raises_runtime_error(self):
        factory = _EmbeddingHandlerFactory(dimensions=4)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/embeddings",
                model_id="bge-m3",
                dimensions=8,  # 服务端返回 4 维,对不上
                max_retries=0,
            )
            with self.assertRaises(RuntimeError):
                embedder.embed_documents(["x"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_server_error_retries_then_raises_runtime_error(self):
        factory = _EmbeddingHandlerFactory(dimensions=4, status=500)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/embeddings",
                model_id="bge-m3",
                dimensions=4,
                max_retries=2,
            )
            with self.assertRaises(RuntimeError):
                embedder.embed_documents(["x"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        # 首次请求加 2 次重试,共 3 次
        self.assertEqual(len(factory.requests), 3)


    def test_client_error_4xx_does_not_retry(self):
        factory = _EmbeddingHandlerFactory(dimensions=8, status=404)
        server, thread = _make_server(factory.handler())
        try:
            embedder = RemoteHttpEmbedder(
                endpoint=f"http://127.0.0.1:{server.server_port}/embeddings",
                model_id="m",
                dimensions=8,
            )
            with self.assertRaises(RuntimeError):
                embedder.embed_documents(["a"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        # 4xx 不是临时错误:只请求一次,不重试。
        self.assertEqual(len(factory.requests), 1)


class ResolveRemoteEmbedderTests(unittest.TestCase):
    def test_remote_provider_with_endpoint_and_model_resolves_remote(self):
        config = EmbeddingConfig(
            provider="remote",
            dimensions=768,
            model_id="bge-m3",
            endpoint="http://embeddings.invalid/v1/embeddings",
            api_key="secret",
        )
        embedder = resolve_embedder(config)
        self.assertIsInstance(embedder, RemoteHttpEmbedder)
        self.assertEqual(embedder.dimensions, 768)
        self.assertEqual(embedder.model_id, "bge-m3")
        self.assertFalse(getattr(embedder, "fallback_reason", ""))

    def test_remote_provider_without_endpoint_falls_back_to_hashed(self):
        config = EmbeddingConfig(provider="remote", dimensions=768, model_id="bge-m3")
        embedder = resolve_embedder(config)
        self.assertIsInstance(embedder, HashedLexicalEmbedder)
        self.assertTrue(getattr(embedder, "fallback_reason", ""))

    def test_remote_provider_without_model_falls_back_to_hashed(self):
        config = EmbeddingConfig(
            provider="remote", dimensions=768, endpoint="http://embeddings.invalid"
        )
        embedder = resolve_embedder(config)
        self.assertIsInstance(embedder, HashedLexicalEmbedder)
        self.assertTrue(getattr(embedder, "fallback_reason", ""))


if __name__ == "__main__":
    unittest.main()
