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

from codekb.storage_integrations import build_qdrant_status, build_storage_readiness, load_env_file


class StorageIntegrationTests(unittest.TestCase):
    def test_storage_readiness_deferred_without_external_config(self):
        report = build_storage_readiness(env={})

        self.assertEqual(report["status"], "deferred")
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["postgres"]["status"], "deferred")
        self.assertEqual(checks["qdrant"]["status"], "deferred")
        self.assertFalse(report["secret_values_written"])

    def test_storage_readiness_checks_qdrant_collections_endpoint(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/collections":
                    body = json.dumps({"result": {"collections": [{"name": "codekb_atoms"}]}}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = build_storage_readiness(
                env={
                    "QDRANT_URL": f"http://127.0.0.1:{server.server_port}",
                    "QDRANT_API_KEY": "secret-qdrant-key",
                },
                timeout_seconds=2,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["qdrant"]["status"], "ok")
        self.assertEqual(checks["qdrant"]["details"]["collections"], ["codekb_atoms"])
        self.assertNotIn("secret-qdrant-key", json.dumps(report, ensure_ascii=False))
        self.assertFalse(report["secret_values_written"])

    def test_qdrant_status_fetches_collection_details(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body = json.dumps({"title": "qdrant", "version": "1.18.2"}).encode("utf-8")
                elif self.path == "/collections":
                    body = json.dumps({"result": {"collections": [{"name": "codekb_atoms"}]}}).encode("utf-8")
                elif self.path == "/collections/codekb_atoms":
                    body = json.dumps(
                        {
                            "result": {
                                "status": "green",
                                "points_count": 89,
                                "indexed_vectors_count": 89,
                                "segments_count": 8,
                                "config": {
                                    "params": {
                                        "vectors": {"size": 64, "distance": "Cosine"},
                                        "on_disk_payload": True,
                                    }
                                },
                                "payload_schema": {"sub_kb_id": {"data_type": "keyword"}},
                            }
                        }
                    ).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
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
            report = build_qdrant_status(
                env={"QDRANT_URL": f"http://127.0.0.1:{server.server_port}", "QDRANT_API_KEY": "secret-key"},
                collection="codekb_atoms",
                timeout_seconds=2,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        raw = json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["version"], "1.18.2")
        self.assertEqual(report["collection"]["name"], "codekb_atoms")
        self.assertEqual(report["collection"]["points_count"], 89)
        self.assertEqual(report["collection"]["vector_size"], 64)
        self.assertEqual(report["collection"]["distance"], "Cosine")
        self.assertEqual(report["collection"]["payload_fields"], ["sub_kb_id"])
        self.assertNotIn("secret-key", raw)
        self.assertFalse(report["secret_values_written"])

    def test_storage_readiness_redacts_postgres_secret_when_driver_missing_or_connect_fails(self):
        report = build_storage_readiness(
            env={"POSTGRES_DSN": "postgresql://kb_user:secret-password@pg.internal:5432/codekb"},
            timeout_seconds=1,
        )

        raw = json.dumps(report, ensure_ascii=False)
        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn(checks["postgres"]["status"], {"pending_driver", "error"})
        self.assertIn("pg.internal", checks["postgres"]["details"]["dsn"])
        self.assertNotIn("secret-password", raw)
        self.assertFalse(report["secret_values_written"])

    def test_storage_readiness_does_not_crash_on_unescaped_postgres_secret(self):
        report = build_storage_readiness(
            env={"POSTGRES_DSN": "postgresql://kb_user:secret/with/slash@pg.internal:5432/codekb"},
            timeout_seconds=1,
        )

        raw = json.dumps(report, ensure_ascii=False)
        checks = {check["id"]: check for check in report["checks"]}
        self.assertIn(checks["postgres"]["status"], {"pending_driver", "error"})
        self.assertNotIn("secret/with/slash", raw)
        self.assertFalse(report["secret_values_written"])

    def test_load_env_file_parses_export_and_quotes(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "storage.env"
            env_file.write_text(
                "\n".join(
                    [
                        "export QDRANT_URL='http://qdrant.internal:6333'",
                        'POSTGRES_DSN="postgresql://user:pass@pg:5432/db"',
                    ]
                ),
                encoding="utf-8",
            )

            values = load_env_file(env_file)

        self.assertEqual(values["QDRANT_URL"], "http://qdrant.internal:6333")
        self.assertEqual(values["POSTGRES_DSN"], "postgresql://user:pass@pg:5432/db")

    def test_storage_readiness_cli_outputs_json(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "storage.env"
            env_file.write_text("", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = "src"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codekb.cli",
                    "storage-readiness",
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
        self.assertEqual(payload["status"], "deferred")
        self.assertFalse(payload["secret_values_written"])


if __name__ == "__main__":
    unittest.main()
