import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.qdrant_migrate import (
    build_collection_name,
    migrate_collection,
    rollback_alias,
)

ALIAS = "codekb_atoms"
NEW = "codekb_atoms_bge-m3_128_20260622t0900"
OLD = "codekb_atoms_bge-m3_64_20260101"
OLDER = "codekb_atoms_bge-m3_64_20251201"
OLDEST = "codekb_atoms_bge-m3_64_20251101"


def _write_points(path: Path, count: int = 2) -> None:
    lines = [
        json.dumps({"id": f"point-{i}", "vector": [0.0] * 128, "payload": {"sub_kb_id": "testing"}})
        for i in range(count)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _MigrateHandlerFactory:
    def __init__(self):
        self.requests = []  # 按调用顺序记录 (method, path, body)

    def handler(self):
        factory = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/aliases":
                    self._send(200, {"result": {"aliases": [{"alias_name": ALIAS, "collection_name": OLD}]}})
                    return
                if self.path == "/collections":
                    self._send(
                        200,
                        {"result": {"collections": [
                            {"name": NEW},
                            {"name": OLD},
                            {"name": OLDER},
                            {"name": OLDEST},
                        ]}},
                    )
                    return
                # 探测新建集合的向量维度 -> 还查不到。
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

            def do_PUT(self):
                length = int(self.headers.get("content-length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                body = json.loads(raw) if raw else None
                factory.requests.append(("PUT", self.path, body))
                self._send(200, {"status": "ok"})

            def do_DELETE(self):
                factory.requests.append(("DELETE", self.path, None))
                self._send(200, {"status": "ok"})

            def log_message(self, format, *args):
                return

        return Handler


def _make_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class BuildCollectionNameTests(unittest.TestCase):
    def test_name_is_deterministic_from_inputs(self):
        name = build_collection_name("codekb_atoms", "BGE-m3", 128, "20260622T0900")
        self.assertEqual(name, "codekb_atoms_bge-m3_128_20260622t0900")


class MigrateDryRunTests(unittest.TestCase):
    def test_dry_run_returns_plan_without_network(self):
        with TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "qdrant_points.jsonl"
            _write_points(points_path, count=3)

            report = migrate_collection(
                url="http://qdrant.invalid:6333",
                alias=ALIAS,
                points_path=points_path,
                vector_size=128,
                model_id="bge-m3",
                suffix="20260622t0900",
                execute=False,
            )

        self.assertEqual(report["status"], "planned")
        self.assertFalse(report["execute"])
        self.assertEqual(report["new_collection"], NEW)
        self.assertEqual(report["vector_size"], 128)
        self.assertEqual(report["steps"], ["create_collection", "upsert_points", "alias_swap", "cleanup"])
        self.assertEqual(report["upsert"]["status"], "planned")
        self.assertEqual(report["upsert"]["points"], 3)
        # 别名原子切换的计划完全能离线推导出来。
        self.assertEqual(report["alias_actions"][0]["delete_alias"]["alias_name"], ALIAS)
        self.assertEqual(report["alias_actions"][1]["create_alias"]["collection_name"], NEW)

    def test_suffix_is_required(self):
        with TemporaryDirectory() as tmp:
            points_path = Path(tmp) / "qdrant_points.jsonl"
            _write_points(points_path)
            with self.assertRaises(ValueError):
                migrate_collection(
                    url="http://qdrant.invalid:6333",
                    points_path=points_path,
                    vector_size=128,
                    model_id="bge-m3",
                    suffix="",
                )


class MigrateExecuteTests(unittest.TestCase):
    def test_execute_runs_create_upsert_swap_cleanup_in_order(self):
        factory = _MigrateHandlerFactory()
        server, thread = _make_server(factory.handler())
        try:
            with TemporaryDirectory() as tmp:
                points_path = Path(tmp) / "qdrant_points.jsonl"
                _write_points(points_path, count=1)
                report = migrate_collection(
                    url=f"http://127.0.0.1:{server.server_port}",
                    api_key="secret",
                    alias=ALIAS,
                    points_path=points_path,
                    vector_size=128,
                    model_id="bge-m3",
                    suffix="20260622t0900",
                    execute=True,
                    keep_old=2,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(report["status"], "migrated")
        self.assertEqual(report["old_collection"], OLD)

        mutations = [(method, path) for method, path, _ in factory.requests]
        self.assertEqual(
            mutations,
            [
                ("PUT", f"/collections/{NEW}"),
                ("PUT", f"/collections/{NEW}/points?wait=true"),
                ("PUT", "/collections/aliases"),
                ("DELETE", f"/collections/{OLDEST}"),
            ],
        )

        swap_body = next(body for method, path, body in factory.requests if path == "/collections/aliases")
        self.assertEqual(swap_body["actions"][0]["delete_alias"]["alias_name"], ALIAS)
        self.assertEqual(swap_body["actions"][1]["create_alias"]["collection_name"], NEW)
        self.assertEqual(swap_body["actions"][1]["create_alias"]["alias_name"], ALIAS)

        # keep_old=2 保留最新的两个旧集合,只删最老的那个。
        self.assertEqual(report["deleted_collections"], [OLDEST])


class MigrateCleanupFailureTests(unittest.TestCase):
    def test_cleanup_failure_keeps_migrated_status(self):
        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/aliases":
                    self._send(200, {"result": {"aliases": [{"alias_name": ALIAS, "collection_name": OLD}]}})
                    return
                if self.path == "/collections":
                    self._send(200, {"result": {"collections": [
                        {"name": NEW}, {"name": OLD}, {"name": OLDER}, {"name": OLDEST},
                    ]}})
                    return
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

            def do_PUT(self):
                length = int(self.headers.get("content-length", "0") or "0")
                if length:
                    self.rfile.read(length)
                self._send(200, {"status": "ok"})

            def do_DELETE(self):
                # 别名切换已提交;清理阶段的 DELETE 在这里失败。
                self.send_response(500)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, format, *args):
                return

        server, thread = _make_server(Handler)
        try:
            with TemporaryDirectory() as tmp:
                points_path = Path(tmp) / "qdrant_points.jsonl"
                _write_points(points_path, count=1)
                report = migrate_collection(
                    url=f"http://127.0.0.1:{server.server_port}",
                    alias=ALIAS,
                    points_path=points_path,
                    vector_size=128,
                    model_id="bge-m3",
                    suffix="20260622t0900",
                    execute=True,
                    keep_old=2,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        # 迁移已生效(别名已切换);清理失败不致命。
        self.assertEqual(report["status"], "migrated")
        self.assertIn("cleanup_error", report)


class RollbackTests(unittest.TestCase):
    def test_rollback_dry_run_plans_swap_back(self):
        report = rollback_alias(
            url="http://qdrant.invalid:6333",
            alias=ALIAS,
            target_collection=OLD,
            execute=False,
        )
        self.assertEqual(report["status"], "planned")
        self.assertEqual(report["alias_actions"][1]["create_alias"]["collection_name"], OLD)

    def test_rollback_execute_repoints_alias_to_old_collection(self):
        factory = _MigrateHandlerFactory()
        server, thread = _make_server(factory.handler())
        try:
            report = rollback_alias(
                url=f"http://127.0.0.1:{server.server_port}",
                api_key="secret",
                alias=ALIAS,
                target_collection=OLD,
                execute=True,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.assertEqual(report["status"], "rolled_back")
        puts = [(path, body) for method, path, body in factory.requests if method == "PUT"]
        self.assertEqual(len(puts), 1)
        path, body = puts[0]
        self.assertEqual(path, "/collections/aliases")
        self.assertEqual(body["actions"][1]["create_alias"]["collection_name"], OLD)
        self.assertEqual(body["actions"][0]["delete_alias"]["alias_name"], ALIAS)


if __name__ == "__main__":
    unittest.main()
