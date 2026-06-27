import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.storage_sync import (
    sync_opensearch_documents,
    sync_postgres_upserts,
    sync_qdrant_points,
)


class _FakeOpenSearchTransport:
    def __init__(self, response=None, exc=None):
        self._response = response if response is not None else {"errors": False, "items": []}
        self._exc = exc
        self.calls = []

    def bulk(self, *, url, body, headers, timeout_seconds):
        self.calls.append({"url": url, "body": body, "headers": headers})
        if self._exc is not None:
            raise self._exc
        return self._response


def _bulk_ok(count):
    return {"errors": False, "items": [{"index": {"_id": f"d{i}", "status": 201}} for i in range(count)]}


def _opensearch_docs(count):
    return [
        {
            "_id": f"atom-{i}",
            "_index": "codekb_atoms",
            "_source": {"text": f"doc {i}", "sub_kb_id": "testing"},
        }
        for i in range(count)
    ]


def _write_opensearch_docs(path, count):
    path.write_text(
        "\n".join(json.dumps(doc) for doc in _opensearch_docs(count)) + "\n",
        encoding="utf-8",
    )


class StorageSyncTests(unittest.TestCase):
    def test_qdrant_sync_dry_run_counts_points_without_network(self):
        with TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "qdrant_points.jsonl"
            points_path.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "point-1", "vector": [1.0, 0.0], "payload": {"sub_kb_id": "testing"}}),
                        json.dumps({"id": "point-2", "vector": [0.0, 1.0], "payload": {"sub_kb_id": "release"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = sync_qdrant_points(
                points_path=points_path,
                url="http://qdrant.invalid:6333",
                collection="codekb_atoms",
                vector_size=2,
                execute=False,
            )

        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["execute"])
        self.assertEqual(report["points"], 2)
        self.assertEqual(report["collection"], "codekb_atoms")

    def test_qdrant_sync_execute_creates_collection_and_upserts_points(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append({"path": self.path, "payload": payload, "api_key": self.headers.get("api-key")})
                body = json.dumps({"status": "ok"}).encode("utf-8")
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
            with TemporaryDirectory() as tmp:
                points_path = Path(tmp) / "qdrant_points.jsonl"
                points_path.write_text(
                    json.dumps({"id": "point-1", "vector": [1.0, 0.0], "payload": {"sub_kb_id": "testing"}}) + "\n",
                    encoding="utf-8",
                )

                report = sync_qdrant_points(
                    points_path=points_path,
                    url=f"http://127.0.0.1:{server.server_port}",
                    collection="codekb_atoms",
                    vector_size=2,
                    api_key="secret-key",
                    execute=True,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["points"], 1)
        self.assertEqual(calls[0]["path"], "/collections/codekb_atoms")
        self.assertEqual(calls[0]["payload"]["vectors"]["size"], 2)
        self.assertEqual(calls[1]["path"], "/collections/codekb_atoms/points?wait=true")
        self.assertEqual(calls[1]["payload"]["points"][0]["id"], "point-1")
        self.assertEqual(calls[1]["api_key"], "secret-key")
        self.assertNotIn("secret-key", json.dumps(report, ensure_ascii=False))

    def test_qdrant_sync_refuses_on_dimension_mismatch(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(
                    {"result": {"config": {"params": {"vectors": {"size": 64, "distance": "Cosine"}}}}}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self):
                length = int(self.headers.get("content-length", "0") or "0")
                self.rfile.read(length)
                calls.append({"method": "PUT", "path": self.path})
                body = json.dumps({"status": "ok"}).encode("utf-8")
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
            with TemporaryDirectory() as tmp:
                points_path = Path(tmp) / "qdrant_points.jsonl"
                points_path.write_text(
                    json.dumps({"id": "point-1", "vector": [0.0] * 128, "payload": {"sub_kb_id": "testing"}}) + "\n",
                    encoding="utf-8",
                )

                report = sync_qdrant_points(
                    points_path=points_path,
                    url=f"http://127.0.0.1:{server.server_port}",
                    collection="codekb_atoms",
                    vector_size=128,
                    execute=True,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(report["status"], "dimension_mismatch")
        self.assertEqual(report["existing_vector_size"], 64)
        self.assertEqual(report["vector_size"], 128)
        # 维度不一致时不能建集合,也不能写入向量点。
        self.assertEqual(calls, [])

    def test_qdrant_sync_recreate_drops_and_rebuilds_on_mismatch(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(
                    {"result": {"config": {"params": {"vectors": {"size": 64, "distance": "Cosine"}}}}}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                calls.append({"method": "DELETE", "path": self.path})
                body = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append({"method": "PUT", "path": self.path, "payload": payload})
                body = json.dumps({"status": "ok"}).encode("utf-8")
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
            with TemporaryDirectory() as tmp:
                points_path = Path(tmp) / "qdrant_points.jsonl"
                points_path.write_text(
                    json.dumps({"id": "point-1", "vector": [0.0] * 128, "payload": {"sub_kb_id": "testing"}}) + "\n",
                    encoding="utf-8",
                )

                report = sync_qdrant_points(
                    points_path=points_path,
                    url=f"http://127.0.0.1:{server.server_port}",
                    collection="codekb_atoms",
                    vector_size=128,
                    execute=True,
                    recreate=True,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["existing_vector_size"], 64)
        methods = [call["method"] for call in calls]
        self.assertEqual(methods[0], "DELETE")
        self.assertIn("PUT", methods)
        put_collection = next(c for c in calls if c["method"] == "PUT" and c["path"] == "/collections/codekb_atoms")
        self.assertEqual(put_collection["payload"]["vectors"]["size"], 128)

    def test_storage_sync_qdrant_cli_dry_run_outputs_json(self):
        with TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "qdrant_points.jsonl"
            env_file = Path(tmp) / "storage.env"
            points_path.write_text(
                json.dumps({"id": "point-1", "vector": [1.0, 0.0], "payload": {"sub_kb_id": "testing"}}) + "\n",
                encoding="utf-8",
            )
            env_file.write_text("QDRANT_URL=http://qdrant.invalid:6333\n", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codekb.cli",
                    "storage-sync-qdrant",
                    "--points",
                    str(points_path),
                    "--env-file",
                    str(env_file),
                    "--vector-size",
                    "2",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=20,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["points"], 1)
        self.assertFalse(payload["execute"])

    def test_postgres_sync_dry_run_counts_upserts_without_connection(self):
        with TemporaryDirectory() as tmp:
            upserts_path = Path(tmp) / "postgres_upserts.jsonl"
            upserts_path.write_text(
                "\n".join(
                    [
                        json.dumps({"target": "source_documents", "sql": "SELECT %s", "params": ["source"]}),
                        json.dumps({"target": "knowledge_atoms", "sql": "SELECT %s", "params": ["atom"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = sync_postgres_upserts(
                upserts_path=upserts_path,
                dsn="postgresql://kb_user:secret@pg.internal:5432/codekb",
                execute=False,
            )

        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["execute"])
        self.assertEqual(report["upserts"], 2)
        self.assertEqual(report["targets"], {"source_documents": 1, "knowledge_atoms": 1})
        self.assertNotIn("secret@pg.internal", json.dumps(report, ensure_ascii=False))

    def test_postgres_sync_execute_runs_upserts_in_order(self):
        fake = _FakePostgres()
        with TemporaryDirectory() as tmp:
            upserts_path = Path(tmp) / "postgres_upserts.jsonl"
            upserts_path.write_text(
                "\n".join(
                    [
                        json.dumps({"target": "source_documents", "sql": "INSERT SOURCE %s", "params": ["source"]}),
                        json.dumps({"target": "knowledge_atoms", "sql": "INSERT ATOM %s", "params": ["atom"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = sync_postgres_upserts(
                upserts_path=upserts_path,
                dsn="postgresql://kb_user:secret@pg.internal:5432/codekb",
                execute=True,
                connect=fake.connect,
            )

        self.assertEqual(report["status"], "synced")
        self.assertEqual(fake.cursor.executions, [("INSERT SOURCE %s", ("source",)), ("INSERT ATOM %s", ("atom",))])
        self.assertEqual(fake.connection.commits, 1)

    def test_storage_sync_postgres_cli_dry_run_outputs_json(self):
        with TemporaryDirectory() as tmp:
            upserts_path = Path(tmp) / "postgres_upserts.jsonl"
            env_file = Path(tmp) / "storage.env"
            upserts_path.write_text(
                json.dumps({"target": "knowledge_atoms", "sql": "SELECT %s", "params": ["atom"]}) + "\n",
                encoding="utf-8",
            )
            env_file.write_text("POSTGRES_DSN=postgresql://user:secret@pg:5432/db\n", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codekb.cli",
                    "storage-sync-postgres",
                    "--upserts",
                    str(upserts_path),
                    "--env-file",
                    str(env_file),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                timeout=20,
                env=env,
                check=False,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["upserts"], 1)
        self.assertFalse(payload["execute"])
        self.assertNotIn("secret@pg", completed.stdout)


class RedactUrlTests(unittest.TestCase):
    def test_redact_url_masks_username_only_credential(self):
        from codekb.storage_sync import _redact_url

        redacted = _redact_url("https://SECRET_TOKEN@os.host:9200/idx?x=1")
        self.assertNotIn("SECRET_TOKEN", redacted)
        self.assertIn("***@os.host:9200", redacted)
        self.assertNotIn("x=1", redacted)

    def test_redact_url_keeps_plain_url(self):
        from codekb.storage_sync import _redact_url

        self.assertEqual(_redact_url("https://os.host:9200"), "https://os.host:9200")


class OpenSearchSyncTests(unittest.TestCase):
    def test_opensearch_dry_run_counts_documents_without_network(self):
        transport = _FakeOpenSearchTransport()
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 3)

            report = sync_opensearch_documents(
                documents_path=docs_path,
                url="https://kb_user:p4ssw0rd-tok@opensearch.invalid:9200",
                execute=False,
                transport=transport,
            )

        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["execute"])
        self.assertEqual(report["documents"], 3)
        self.assertEqual(report["index"], "codekb_atoms")
        # dry-run 不发任何网络请求,并对 url 脱敏。
        self.assertEqual(transport.calls, [])
        self.assertNotIn("p4ssw0rd-tok", json.dumps(report, ensure_ascii=False))
        self.assertIn("***", report["url"])
        self.assertFalse(report["secret_values_written"])

    def test_opensearch_execute_bulk_indexes_documents(self):
        transport = _FakeOpenSearchTransport(response=_bulk_ok(2))
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 2)

            report = sync_opensearch_documents(
                documents_path=docs_path,
                url="https://opensearch.internal:9200",
                api_key="secret-key",
                execute=True,
                transport=transport,
            )

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["synced"], 2)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(transport.calls[0]["url"].endswith("/_bulk"))
        # NDJSON 批量体:每个文档一行 action、一行 source。
        body_lines = [line for line in transport.calls[0]["body"].splitlines() if line]
        self.assertEqual(len(body_lines), 4)
        self.assertEqual(json.loads(body_lines[0]), {"index": {"_index": "codekb_atoms", "_id": "atom-0"}})
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "ApiKey secret-key")
        self.assertNotIn("secret-key", json.dumps(report, ensure_ascii=False))

    def test_opensearch_execute_batches_documents(self):
        transport = _FakeOpenSearchTransport(response=_bulk_ok(1))
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 3)

            report = sync_opensearch_documents(
                documents_path=docs_path,
                url="https://opensearch.internal:9200",
                execute=True,
                batch_size=1,
                transport=transport,
            )

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["synced"], 3)
        self.assertEqual(len(transport.calls), 3)

    def test_opensearch_partial_when_some_items_fail(self):
        mixed = {
            "errors": True,
            "items": [
                {"index": {"_id": "atom-0", "status": 201}},
                {"index": {"_id": "atom-1", "status": 400, "error": "mapping"}},
            ],
        }
        transport = _FakeOpenSearchTransport(response=mixed)
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 2)

            report = sync_opensearch_documents(
                documents_path=docs_path,
                url="https://opensearch.internal:9200",
                execute=True,
                transport=transport,
            )

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["synced"], 1)
        self.assertEqual(report["failed"], 1)

    def test_opensearch_failed_when_transport_raises(self):
        import urllib.error

        transport = _FakeOpenSearchTransport(exc=urllib.error.URLError("boom"))
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 2)

            report = sync_opensearch_documents(
                documents_path=docs_path,
                url="https://opensearch.internal:9200",
                execute=True,
                transport=transport,
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "URLError")

    def test_opensearch_requires_url(self):
        with TemporaryDirectory() as tmp:
            docs_path = Path(tmp) / "opensearch_documents.jsonl"
            _write_opensearch_docs(docs_path, 1)
            with self.assertRaises(ValueError):
                sync_opensearch_documents(documents_path=docs_path, url="", execute=False)


class _FakePostgres:
    def __init__(self):
        self.cursor = _FakeCursor()
        self.connection = _FakeConnection(self.cursor)

    def connect(self, _dsn):
        return self.connection


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


class _FakeCursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self.executions.append((sql, params))


if __name__ == "__main__":
    unittest.main()
