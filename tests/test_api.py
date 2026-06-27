import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.api import (
    _parse_bool,
    _parse_min_confidence,
    _parse_sub_kbs,
    _parse_top_k,
    _resolve_qdrant_vector_size,
    _run_diagnosis,
    _verify_diagnose_webhook_token,
    answer_result_to_dict,
    create_app,
    registry_to_dict,
)
from codekb.audit_page import render_audit_page
from codekb.hub_page import render_hub_page
from codekb.qdrant_page import render_qdrant_page
from codekb.local_index import build_local_index
from codekb.models import AnswerResult, CitationPack
from codekb.registry import load_registry
from codekb.service import OfflineKbService
from codekb.user_auth import JsonUserTokenStore


class ApiHelperTests(unittest.TestCase):
    def test_resolve_qdrant_vector_size_defaults_to_embedding_dim(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEKB_QDRANT_VECTOR_SIZE", None)
            os.environ.pop("CODEKB_EMBEDDING_DIM", None)
            self.assertEqual(_resolve_qdrant_vector_size(), 64)

    def test_resolve_qdrant_vector_size_follows_embedding_dim_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEKB_QDRANT_VECTOR_SIZE", None)
            os.environ["CODEKB_EMBEDDING_DIM"] = "128"
            self.assertEqual(_resolve_qdrant_vector_size(), 128)

    def test_resolve_qdrant_vector_size_explicit_override_wins(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ["CODEKB_EMBEDDING_DIM"] = "128"
            os.environ["CODEKB_QDRANT_VECTOR_SIZE"] = "256"
            self.assertEqual(_resolve_qdrant_vector_size(), 256)

    def test_render_hub_page_contains_core_entrypoints(self):
        html = render_hub_page()

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("工作台", html)
        self.assertIn("ask-box", html)  # 内嵌的提问组件(主操作)
        self.assertIn("智能问答", html)
        self.assertIn("知识运营", html)
        self.assertIn("集成与诊断", html)
        self.assertIn("确认与设置", html)
        self.assertIn("主链路", html)
        self.assertIn("/console", html)
        self.assertIn("/ask", html)  # 提问组件向这里提交
        self.assertIn("/audit/page", html)
        self.assertIn("/demo/current-user", html)
        self.assertIn("/demo/webhook", html)
        self.assertIn("/diagnose/external-inputs/page", html)
        self.assertIn("/diagnose/final-verification/page", html)
        self.assertIn("/auth/im/mcp/setup", html)
        self.assertIn("/auth/im/confirmations/page", html)
        self.assertIn("/storage/qdrant/page", html)
        self.assertIn("/healthz", html)

    def test_render_ask_page_contains_interactive_widget(self):
        from codekb.ask_page import render_ask_page

        html = render_ask_page()
        self.assertIn('data-ui-version="3"', html)
        self.assertIn("ask-box", html)
        self.assertIn('id="ask-form"', html)
        self.assertIn('id="ask-out"', html)
        self.assertIn("/ask", html)  # 组件把问题提交到这里
        self.assertIn("/feedback", html)  # 内联的 👍/👎 反馈
        self.assertIn("智能问答", html)
        self.assertIn('aria-current="page"', html)

    def test_render_qdrant_page_contains_dashboard_contract(self):
        html = render_qdrant_page()

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("向量库状态", html)
        self.assertIn("codekb_atoms", html)
        self.assertIn("/storage/qdrant/status", html)
        self.assertIn("Points", html)
        self.assertIn("Collection", html)
        self.assertNotIn("6333", html)

    def test_render_audit_page_contains_core_workbench_contract(self):
        html = render_audit_page()

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn("Code-KB Audit Queue", html)
        self.assertIn("/audit/queue", html)
        self.assertIn("/audit/", html)
        self.assertIn("/ingest/candidates/", html)
        self.assertIn("/revision", html)
        self.assertIn("/index/status", html)
        self.assertIn("rebuild_index", html)
        self.assertIn("pending_review", html)
        self.assertIn("needs_revision", html)

    def test_answer_result_to_dict(self):
        answer = AnswerResult(
            query="DEVICE_SEQ 是什么？",
            answer="可追溯摘要",
            refused=False,
            citations=(
                CitationPack(
                    atom_id="atom-1",
                    docid="1000000014",
                    title="示例UDT自动化测试使用说明",
                    anchor="3-udt相关预设参数",
                    section_path=("3. UDT相关预设参数",),
                    quote="DEVICE_SEQ 是平台内置环境变量。",
                    score=12.5,
                ),
            ),
        )

        payload = answer_result_to_dict(answer)

        self.assertEqual(payload["answer_id"], "")
        self.assertEqual(payload["trace_id"], "")
        self.assertEqual(payload["query"], "DEVICE_SEQ 是什么？")
        self.assertFalse(payload["refused"])
        self.assertEqual(payload["confidence"], 0.0)
        self.assertEqual(payload["citations"][0]["docid"], "1000000014")
        self.assertEqual(payload["citations"][0]["section_path"], ["3. UDT相关预设参数"])
        # 生成式元数据字段始终存在(抽取式走默认值)。
        self.assertEqual(payload["generation_mode"], "extractive")
        self.assertEqual(payload["model"], "")
        self.assertEqual(payload["latency_ms"], 0.0)
        self.assertEqual(payload["fallback_reason"], "")

    def test_answer_result_to_dict_generative_fields(self):
        answer = AnswerResult(
            query="DEVICE_SEQ 是什么？",
            answer="DEVICE_SEQ 是设备序号 [1]。",
            refused=False,
            citations=(),
            generation_mode="generative",
            model_id="claude-opus-4-8",
            latency_ms=42.0,
            fallback_reason="",
        )

        payload = answer_result_to_dict(answer)

        self.assertEqual(payload["generation_mode"], "generative")
        self.assertEqual(payload["model"], "claude-opus-4-8")
        self.assertEqual(payload["latency_ms"], 42.0)

    def test_parse_sub_kbs(self):
        self.assertEqual(_parse_sub_kbs("testing, incident"), {"testing", "incident"})
        self.assertEqual(_parse_sub_kbs(["testing", " incident "]), {"testing", "incident"})
        self.assertIsNone(_parse_sub_kbs(""))

    def test_parse_top_k(self):
        self.assertEqual(_parse_top_k(None), 4)
        self.assertEqual(_parse_top_k("8"), 8)
        with self.assertRaises(ValueError):
            _parse_top_k(0)
        with self.assertRaises(ValueError):
            _parse_top_k(21)

    def test_parse_diagnose_options(self):
        self.assertTrue(_parse_bool("true"))
        self.assertFalse(_parse_bool("false", default=True))
        self.assertEqual(_parse_min_confidence(None), 0.35)
        self.assertEqual(_parse_min_confidence("0.7"), 0.7)
        with self.assertRaises(ValueError):
            _parse_bool("maybe")
        with self.assertRaises(ValueError):
            _parse_min_confidence("1.2")

    def test_registry_to_dict(self):
        registry = load_registry(Path(__file__).resolve().parents[1] / "docs" / "kb-registry.draft.yaml")

        payload = registry_to_dict(registry)

        self.assertEqual(payload["version"], "0.1")
        self.assertEqual(payload["defaults"]["rerank_top_k"], 4)
        self.assertIn("source_docs", payload["sub_kbs"][0])

    def test_run_diagnosis_accepts_context_without_query(self):
        root = Path(__file__).resolve().parents[1]
        service = OfflineKbService(
            fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
            aliases_path=str(root / "data" / "entity_aliases.yaml"),
        )
        with TemporaryDirectory() as tmp:
            result = _run_diagnosis(
                {
                    "context": {
                        "surface": "code_review",
                        "repo": "ym/app",
                        "error_code": "DEVICE_SEQ",
                        "error_text": "DEVICE_SEQ 构建失败，需要排查 UDT 参数",
                    },
                    "sub_kbs": ["testing"],
                    "include_governance": False,
                },
                service=service,
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                governance_policy_path=str(root / "docs" / "governance-policy.draft.yaml"),
                feedback_log_path=str(Path(tmp) / "feedback.jsonl"),
                candidate_store_path=str(Path(tmp) / "candidates.json"),
                pending_docs_dir=str(Path(tmp) / "pending"),
            )

        self.assertIn("DEVICE_SEQ 构建失败", result.query)
        self.assertEqual(result.context.surface, "code_review")
        self.assertEqual(result.context.repo, "ym/app")

    def test_verify_diagnose_webhook_token(self):
        original = os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_TOKEN")
        try:
            # 失败即拒:没配置令牌时一律拒绝请求,而不是放行所有请求。
            os.environ.pop("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", None)
            with self.assertRaises(PermissionError):
                _verify_diagnose_webhook_token("")
            with self.assertRaises(PermissionError):
                _verify_diagnose_webhook_token("anything")

            # 配置成空串或纯空白,等同于没配置。
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"] = "   "
            with self.assertRaises(PermissionError):
                _verify_diagnose_webhook_token("anything")

            os.environ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"] = "expected"
            _verify_diagnose_webhook_token("expected")
            # 传入令牌两边的空白会被容忍。
            _verify_diagnose_webhook_token("  expected  ")
            with self.assertRaises(PermissionError):
                _verify_diagnose_webhook_token("wrong")
        finally:
            if original is None:
                os.environ.pop("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", None)
            else:
                os.environ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"] = original

    def test_maybe_build_wiki_publish_client_gating(self):
        import codekb.api as api_module

        sentinel = object()
        original = api_module._wiki_publish_client_factory
        try:
            api_module._wiki_publish_client_factory = lambda: sentinel
            # 写开关关闭 -> 即便是 execute 请求也绝不创建客户端。
            self.assertIsNone(api_module._maybe_build_wiki_publish_client({"execute": True}, False))
            # 写开关打开但不是 execute 请求 -> 仍返回 None(试运行/拦截行为)。
            self.assertIsNone(api_module._maybe_build_wiki_publish_client({"execute": False}, True))
            self.assertIsNone(api_module._maybe_build_wiki_publish_client({}, True))
            # 写开关打开且 execute -> 调用工厂创建客户端。
            self.assertIs(api_module._maybe_build_wiki_publish_client({"execute": True}, True), sentinel)
            self.assertIs(api_module._maybe_build_wiki_publish_client({"execute": "true"}, True), sentinel)
        finally:
            api_module._wiki_publish_client_factory = original

    def test_api_diagnose_webhook_rejects_when_token_unconfigured(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_TOKEN")
        try:
            os.environ.pop("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", None)
            client = TestClient(
                create_app(
                    fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                )
            )
            response = client.post(
                "/diagnose/webhook/code_review",
                json={
                    "repository": {"path": "ym/app", "owner": "legacy-owner"},
                    "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
                    "sub_kbs": ["testing"],
                },
            )
        finally:
            if original is None:
                os.environ.pop("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", None)
            else:
                os.environ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"] = original

        # 失败即拒:没配置令牌时 webhook 必须拒绝(401),而不是放行请求。
        self.assertEqual(response.status_code, 401)
        self.assertIn("not configured", response.json()["detail"])

    def test_api_code_nav_structural_routes_and_usage(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as tmp:
            os.environ["CODEKB_USAGE_LOG"] = str(Path(tmp) / "usage.jsonl")
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    )
                )
                # find_files:在已索引的路径/docid 上做子串匹配
                files = client.post("/code/files", json={"pattern": "1000004"})
                self.assertEqual(files.status_code, 200)
                body = files.json()
                self.assertIn("files", body)
                self.assertGreaterEqual(body["count"], 1)
                self.assertIn("1000004", body["files"])
                # find_files 必须传 pattern
                self.assertEqual(client.post("/code/files", json={"pattern": ""}).status_code, 400)
                # list_dir:按目录结构浏览
                d = client.post("/code/dir", json={"prefix": ""})
                self.assertEqual(d.status_code, 200)
                self.assertIn("dirs", d.json())
                self.assertIn("files", d.json())
                # 用量汇总能反映刚才发起的调用(遥测已通过环境变量开启)
                usage = client.get("/usage/summary")
                self.assertEqual(usage.status_code, 200)
                summary = usage.json()
                self.assertTrue(summary["configured"])
                self.assertGreaterEqual(summary["total"], 1)
                tools = {t["tool"] for t in summary["by_tool"]}
                self.assertIn("find_files", tools)
            finally:
                os.environ.pop("CODEKB_USAGE_LOG", None)

    def test_api_diagnose_webhook_sample_suite_serves_summary(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/diagnose/webhook/sample-suite")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["total"], 6)
        self.assertNotIn("code_review-token-secret", response.text)

    def test_api_storage_readiness_serves_external_store_status(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "POSTGRES_DSN": os.environ.get("POSTGRES_DSN"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "POSTGRES_HOST": os.environ.get("POSTGRES_HOST"),
            "QDRANT_URL": os.environ.get("QDRANT_URL"),
            "QDRANT_API_KEY": os.environ.get("QDRANT_API_KEY"),
        }
        for key in original:
            os.environ.pop(key, None)
        try:
            client = TestClient(
                create_app(
                    fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                )
            )
            response = client.get("/storage/readiness")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        payload = response.json()
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "deferred")
        self.assertEqual(checks["postgres"]["status"], "deferred")
        self.assertEqual(checks["qdrant"]["status"], "deferred")
        self.assertFalse(payload["secret_values_written"])

    def test_create_app_passes_atom_store_env_to_service(self):
        try:
            import fastapi  # noqa: F401
        except ModuleNotFoundError as exc:
            self.skipTest(f"FastAPI is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_ATOM_STORE": os.environ.get("CODEKB_ATOM_STORE"),
            "POSTGRES_DSN": os.environ.get("POSTGRES_DSN"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }
        try:
            os.environ["CODEKB_ATOM_STORE"] = "postgres"
            os.environ["POSTGRES_DSN"] = "postgresql://example"
            os.environ.pop("DATABASE_URL", None)
            with patch("codekb.api.OfflineKbService") as service_cls:
                create_app(
                    fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                    aliases_path=str(root / "data" / "entity_aliases.yaml"),
                    registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                )

            kwargs = service_cls.call_args.kwargs
            self.assertEqual(kwargs["atom_store_mode"], "postgres")
            self.assertEqual(kwargs["postgres_dsn"], "postgresql://example")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_api_qdrant_page_serves_no_store_dashboard(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/storage/qdrant/page")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("向量库状态", response.text)
        self.assertIn("/storage/qdrant/status", response.text)



    def test_api_diagnose_external_inputs_serves_safe_task_plan(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
            "CODEKB_P5_IM_TEMPLATE": os.environ.get("CODEKB_P5_IM_TEMPLATE"),
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"),
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_IM_APP_SECRET": os.environ.get("CODEKB_IM_APP_SECRET"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_ENV_FILE"] = str(tmp_path / "p5.env")
            os.environ["CODEKB_P5_IM_TEMPLATE"] = str(tmp_path / "im-config.todo.env")
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"] = str(tmp_path / "real.yaml")
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(tmp_path / "tokens.json")
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret-value"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/diagnose/external-inputs")
                payload = response.json()
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        raw = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertIn("tasks", payload)
        self.assertEqual(payload["external_inputs_markdown_url"].endswith("/diagnose/external-inputs.md"), True)
        self.assertEqual(payload["external_inputs_page_url"].endswith("/diagnose/external-inputs/page"), True)
        self.assertEqual(payload["final_verification_url"].endswith("/diagnose/final-verification"), True)
        self.assertEqual(payload["final_verification_page_url"].endswith("/diagnose/final-verification/page"), True)
        self.assertEqual(payload["token_binding_page_url"].endswith("/auth/im/token-bindings/page"), True)
        self.assertTrue(payload["mcp_auth_strategy"]["current_user_auth_required"])
        self.assertFalse(payload["mcp_auth_strategy"]["interface_person_lookup_enabled"])
        self.assertIn("im_template", {task["check_id"] for task in payload["tasks"]})
        self.assertIn("external_state", payload)
        self.assertIn("/auth/im/mcp/setup", raw)
        self.assertIn("diagnose-current-user-smoke", raw)
        self.assertIn("/auth/im/confirmations/request", raw)
        self.assertNotIn("app-secret-value", raw)

    def test_api_diagnose_external_inputs_page_serves_safe_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
            "CODEKB_P5_IM_TEMPLATE": os.environ.get("CODEKB_P5_IM_TEMPLATE"),
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"),
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_IM_APP_SECRET": os.environ.get("CODEKB_IM_APP_SECRET"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_ENV_FILE"] = str(tmp_path / "p5.env")
            os.environ["CODEKB_P5_IM_TEMPLATE"] = str(tmp_path / "im-config.todo.env")
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"] = str(tmp_path / "real.yaml")
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(tmp_path / "tokens.json")
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret-value"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/diagnose/external-inputs/page")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Current User Auth Strategy", response.text)
        self.assertIn("External state", response.text)
        self.assertIn("im_template", response.text)
        self.assertIn("current_authenticated_user", response.text)
        self.assertIn("/diagnose/external-inputs.md", response.text)
        self.assertIn("/diagnose/final-verification/page", response.text)
        self.assertIn("/auth/im/token-bindings/page", response.text)
        self.assertIn("diagnose-current-user-smoke", response.text)
        self.assertIn("/auth/im/confirmations/request", response.text)
        self.assertNotIn("app-secret-value", response.text)

    def test_api_diagnose_external_inputs_markdown_serves_safe_markdown(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
            "CODEKB_P5_IM_TEMPLATE": os.environ.get("CODEKB_P5_IM_TEMPLATE"),
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"),
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_IM_APP_SECRET": os.environ.get("CODEKB_IM_APP_SECRET"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_ENV_FILE"] = str(tmp_path / "p5.env")
            os.environ["CODEKB_P5_IM_TEMPLATE"] = str(tmp_path / "im-config.todo.env")
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"] = str(tmp_path / "real.yaml")
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(tmp_path / "tokens.json")
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret-value"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/diagnose/external-inputs.md")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn("# Code-KB P5 External Input Plan", response.text)
        self.assertIn("External state pending checks", response.text)
        self.assertIn("im_template", response.text)
        self.assertIn("/diagnose/external-inputs.md", response.text)
        self.assertIn("/diagnose/final-verification/page", response.text)
        self.assertIn("/auth/im/token-bindings/page", response.text)
        self.assertIn("current user must authorize first", response.text)
        self.assertIn("/auth/im/confirmations/request", response.text)
        self.assertNotIn("app-secret-value", response.text)

    def test_api_index_rebuild_and_audit_rebuild_make_pending_docs_searchable(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_INDEX_DB": os.environ.get("CODEKB_INDEX_DB"),
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "kb.sqlite3"
            fixture_path = root / "data" / "fixtures" / "sample_corpus.jsonl"
            build_local_index(fixture_path, db_path)
            os.environ["CODEKB_INDEX_DB"] = str(db_path)
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(fixture_path),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post("/index/rebuild", json={"include_pending_docs": True})
                rebuild = client.post(
                    "/index/rebuild",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"include_pending_docs": True},
                )
                submission = client.post(
                    "/ingest",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={
                        "sub_kb_id": "testing",
                        "title": "PENDING_API_REBUILD_RULE",
                        "content": "PENDING_API_REBUILD_RULE 表示审核通过后进入在线索引。",
                    },
                ).json()
                audit = client.post(
                    f"/audit/{submission['candidate_id']}",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"action": "approve", "reviewer_hash": "reviewer", "rebuild_index": True},
                )
                answer = client.post(
                    "/ask",
                    json={"query": "PENDING_API_REBUILD_RULE 是什么？", "sub_kbs": ["testing"], "top_k": 1},
                )
                status = client.get("/index/status")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        audit_payload = audit.json()
        answer_payload = answer.json()
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(rebuild.status_code, 200)
        self.assertEqual(rebuild.json()["status"], "rebuilt")
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit_payload["status"], "approved")
        self.assertEqual(audit_payload["index_rebuild"]["status"], "rebuilt")
        self.assertEqual(answer.status_code, 200)
        self.assertFalse(answer_payload["refused"])
        self.assertEqual(answer_payload["citations"][0]["docid"], submission["candidate_id"])
        self.assertEqual(status.json()["status"], "ok")

    def test_api_audit_can_dry_run_external_storage_sync_after_rebuild(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_INDEX_DB": os.environ.get("CODEKB_INDEX_DB"),
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_INDEX_ARTIFACTS_DIR": os.environ.get("CODEKB_INDEX_ARTIFACTS_DIR"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "kb.sqlite3"
            artifacts = tmp_path / "artifacts"
            fixture_path = root / "data" / "fixtures" / "sample_corpus.jsonl"
            build_local_index(fixture_path, db_path)
            os.environ["CODEKB_INDEX_DB"] = str(db_path)
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_INDEX_ARTIFACTS_DIR"] = str(artifacts)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(fixture_path),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                submission = client.post(
                    "/ingest",
                    json={
                        "sub_kb_id": "testing",
                        "title": "PENDING_API_EXTERNAL_SYNC_RULE",
                        "content": "PENDING_API_EXTERNAL_SYNC_RULE 表示审核通过后同步外部存储。",
                    },
                ).json()
                with patch("codekb.api.sync_external_index_artifacts") as sync_external:
                    sync_external.return_value = {
                        "status": "planned",
                        "execute": False,
                        "summary": {"source_documents": 16, "knowledge_atoms": 90},
                        "postgres": {"status": "planned", "upserts": 106},
                        "qdrant": {"status": "planned", "points": 90},
                    }
                    audit = client.post(
                        f"/audit/{submission['candidate_id']}",
                        json={
                            "action": "approve",
                            "reviewer_hash": "reviewer",
                            "rebuild_index": True,
                            "sync_external_storage": True,
                        },
                    )
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = audit.json()
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(payload["status"], "approved")
        self.assertEqual(payload["index_rebuild"]["status"], "rebuilt")
        self.assertEqual(payload["storage_sync"]["status"], "planned")
        sync_external.assert_called_once()
        kwargs = sync_external.call_args.kwargs
        self.assertEqual(Path(kwargs["output_dir"]), artifacts)
        self.assertFalse(kwargs["execute"])

    def test_api_candidate_revision_flow_returns_to_audit_queue(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                submission = client.post(
                    "/ingest",
                    json={"sub_kb_id": "testing", "title": "修订测试", "content": "初稿"},
                ).json()
                request_revision = client.post(
                    f"/audit/{submission['candidate_id']}",
                    json={"action": "request_revision", "reviewer_hash": "reviewer", "comment": "补充内容"},
                )
                revision = client.post(
                    f"/ingest/candidates/{submission['candidate_id']}/revision",
                    json={"content": "修订后的正文", "submitted_by_hash": "author", "metadata": {"revision": 1}},
                )
                detail = client.get(f"/ingest/candidates/{submission['candidate_id']}")
                queue = client.get("/audit/queue?status=pending_review&limit=20")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        revision_payload = revision.json()
        detail_payload = detail.json()
        self.assertEqual(request_revision.status_code, 200)
        self.assertEqual(request_revision.json()["status"], "needs_revision")
        self.assertEqual(revision.status_code, 200)
        self.assertEqual(revision_payload["status"], "pending_review")
        self.assertEqual(revision_payload["audit"]["action"], "revise")
        self.assertEqual(revision_payload["candidate"]["metadata"]["revision"], 1)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual({audit["action"] for audit in detail_payload["audits"]}, {"request_revision", "revise"})
        self.assertEqual(queue.status_code, 200)
        self.assertIn(submission["candidate_id"], {item["candidate_id"] for item in queue.json()["candidates"]})

    def test_api_audit_write_endpoints_require_admin_token_when_configured(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
            "CODEKB_AUDIT_WRITE_REQUIRE_ADMIN": os.environ.get("CODEKB_AUDIT_WRITE_REQUIRE_ADMIN"),
        }
        admin = {"X-CodeKB-Admin-Token": "admin-token"}
        ingest_body = {
            "sub_kb_id": "testing",
            "title": "PENDING_API_ADMIN_GATE_RULE",
            "content": "PENDING_API_ADMIN_GATE_RULE 表示写端点受管理员令牌闸门保护。",
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            os.environ.pop("CODEKB_AUDIT_WRITE_REQUIRE_ADMIN", None)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                # 令牌缺失/错误 -> 所有写端点都返回 401。
                ingest_missing = client.post("/ingest", json=ingest_body)
                ingest_wrong = client.post(
                    "/ingest", headers={"X-CodeKB-Admin-Token": "nope"}, json=ingest_body
                )
                audit_missing = client.post(
                    "/audit/does-not-matter", json={"action": "approve", "reviewer_hash": "r"}
                )
                revision_missing = client.post(
                    "/ingest/candidates/does-not-matter/revision",
                    json={"reviewer_hash": "r", "note": "x"},
                )
                # 令牌正确 -> 走正常成功流程。
                submission = client.post("/ingest", headers=admin, json=ingest_body).json()
                request_revision = client.post(
                    f"/audit/{submission['candidate_id']}",
                    headers=admin,
                    json={"action": "request_revision", "reviewer_hash": "reviewer", "comment": "请补充"},
                )
                revision = client.post(
                    f"/ingest/candidates/{submission['candidate_id']}/revision",
                    headers=admin,
                    json={
                        "title": ingest_body["title"],
                        "content": "PENDING_API_ADMIN_GATE_RULE 已按要求补充修订正文。",
                        "submitted_by_hash": "reviewer",
                    },
                )
                approve = client.post(
                    f"/audit/{submission['candidate_id']}",
                    headers=admin,
                    json={"action": "approve", "reviewer_hash": "reviewer"},
                )
                # 即便闸门开启,读端点依然放行。
                read_candidates = client.get("/ingest/candidates")
                read_queue = client.get("/audit/queue")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(ingest_missing.status_code, 401)
        self.assertEqual(ingest_wrong.status_code, 401)
        self.assertEqual(audit_missing.status_code, 401)
        self.assertEqual(revision_missing.status_code, 401)
        self.assertEqual(submission["status"], "accepted")
        self.assertEqual(request_revision.status_code, 200)
        self.assertEqual(revision.status_code, 200)
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["status"], "approved")
        self.assertEqual(read_candidates.status_code, 200)
        self.assertEqual(read_queue.status_code, 200)

    def test_api_audit_write_endpoints_open_when_admin_token_unconfigured(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            # CI 默认:没配置管理员令牌 -> 闸门保持关闭。
            os.environ.pop("CODEKB_AUTH_ADMIN_TOKEN", None)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                submission = client.post(
                    "/ingest",
                    json={
                        "sub_kb_id": "testing",
                        "title": "PENDING_API_NO_GATE_RULE",
                        "content": "PENDING_API_NO_GATE_RULE 表示未配置管理员令牌时写端点保持原行为。",
                    },
                )
                submission_payload = submission.json()
                approve = client.post(
                    f"/audit/{submission_payload['candidate_id']}",
                    json={"action": "approve", "reviewer_hash": "reviewer"},
                )
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        # 没配置管理员令牌 -> 不带任何请求头也能写入成功。
        self.assertEqual(submission.status_code, 200)
        self.assertEqual(submission_payload["status"], "accepted")
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["status"], "approved")

    def test_api_audit_page_serves_no_store_workbench(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/audit/page")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Code-KB Audit Queue", response.text)

    def test_api_hub_page_serves_root_and_hub_no_store(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        root_response = client.get("/")
        hub_response = client.get("/hub")
        css_response = client.get("/static/app.css")
        js_response = client.get("/static/app.js")

        # 根路径现在返回 SPA 控制台;旧版 hub 仍保留在 /hub。
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(root_response.headers["cache-control"], "no-store")
        self.assertIn("Code-KB", root_response.text)
        self.assertIn("/static/app.js", root_response.text)
        self.assertEqual(hub_response.status_code, 200)
        self.assertIn("工作台", hub_response.text)
        self.assertEqual(css_response.status_code, 200)
        self.assertEqual(js_response.status_code, 200)

    def test_api_publish_outbox_plan_and_process_are_admin_controlled(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_PUBLISH_OUTBOX": os.environ.get("CODEKB_PUBLISH_OUTBOX"),
            "CODEKB_PUBLISH_REPORT": os.environ.get("CODEKB_PUBLISH_REPORT"),
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending-docs" / "testing"
            pending.mkdir(parents=True)
            (pending / "candidate-http-api.md").write_text(
                "\n".join(
                    [
                        "---",
                        'candidate_id: "candidate-http-api"',
                        'sub_kb_id: "testing"',
                        'source_type: "manual"',
                        'source_ref: "api-test"',
                        'dedupe_key: "dedupe"',
                        'approved_at: "2026-06-16T08:00:00Z"',
                        "---",
                        "",
                        "# API 发布计划测试",
                        "",
                        "API_PUBLISH_RULE 表示 HTTP 发布 outbox 测试规则。",
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_PUBLISH_OUTBOX"] = str(tmp_path / "outbox" / "publish.jsonl")
            os.environ["CODEKB_PUBLISH_REPORT"] = str(tmp_path / "logs" / "publish-report.json")
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post("/publish/outbox/plan", json={"mode": "manual"})
                readiness = client.get(
                    "/publish/readiness",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                )
                planned = client.post(
                    "/publish/outbox/plan",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"mode": "index_page", "index_docid": "401", "limit": 20},
                )
                processed = client.post(
                    "/publish/outbox/process",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"execute": False, "limit": 20},
                )
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.json()["status"], "ready_for_outbox")
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["written"], 1)
        self.assertEqual(planned.json()["plans"][0]["candidate_id"], "candidate-http-api")
        self.assertEqual(processed.status_code, 200)
        self.assertEqual(processed.json()["status"], "validated")
        self.assertEqual(processed.json()["processed"], 1)

    def test_api_publish_configure_applies_defaults_for_readiness_and_plan(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_PUBLISH_OUTBOX": os.environ.get("CODEKB_PUBLISH_OUTBOX"),
            "CODEKB_PUBLISH_REPORT": os.environ.get("CODEKB_PUBLISH_REPORT"),
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
            "CODEKB_PUBLISH_MODE": os.environ.get("CODEKB_PUBLISH_MODE"),
            "CODEKB_PUBLISH_INDEX_DOCID": os.environ.get("CODEKB_PUBLISH_INDEX_DOCID"),
            "CODEKB_PUBLISH_TEMPLATE_DOCID": os.environ.get("CODEKB_PUBLISH_TEMPLATE_DOCID"),
            "CODEKB_PUBLISH_TARGET_PARENTID": os.environ.get("CODEKB_PUBLISH_TARGET_PARENTID"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pending = tmp_path / "pending-docs" / "testing"
            pending.mkdir(parents=True)
            (pending / "candidate-publish-config.md").write_text(
                "\n".join(
                    [
                        "---",
                        'candidate_id: "candidate-publish-config"',
                        'sub_kb_id: "testing"',
                        'source_type: "manual"',
                        'source_ref: "api-config-test"',
                        'dedupe_key: "dedupe"',
                        'approved_at: "2026-06-16T08:00:00Z"',
                        "---",
                        "",
                        "# API 发布配置测试",
                        "",
                        "API_PUBLISH_CONFIG_RULE 表示 HTTP 发布配置测试规则。",
                    ]
                ),
                encoding="utf-8",
            )
            env_file = tmp_path / "p5.env"
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending-docs")
            os.environ["CODEKB_PUBLISH_OUTBOX"] = str(tmp_path / "outbox" / "publish.jsonl")
            os.environ["CODEKB_PUBLISH_REPORT"] = str(tmp_path / "logs" / "publish-report.json")
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            os.environ["CODEKB_ENV_FILE"] = str(env_file)
            for key in (
                "CODEKB_PUBLISH_MODE",
                "CODEKB_PUBLISH_INDEX_DOCID",
                "CODEKB_PUBLISH_TEMPLATE_DOCID",
                "CODEKB_PUBLISH_TARGET_PARENTID",
            ):
                os.environ.pop(key, None)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post("/publish/configure", json={"mode": "index_page", "index_docid": "401"})
                applied = client.post(
                    "/publish/configure",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"mode": "index_page", "index_docid": "401", "apply": True},
                )
                readiness = client.get("/publish/readiness", headers={"X-CodeKB-Admin-Token": "admin-token"})
                planned = client.post(
                    "/publish/outbox/plan",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json={"limit": 20},
                )
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            raw_env = env_file.read_text(encoding="utf-8")
        raw_config_response = json.dumps(applied.json(), ensure_ascii=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()["status"], "applied")
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.json()["status"], "ready_for_outbox")
        self.assertEqual(readiness.json()["resolved"]["index_docid"], "401")
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["mode"], "index_page")
        self.assertEqual(planned.json()["plans"][0]["operations"][0]["params"]["id"], 401)
        self.assertIn("CODEKB_PUBLISH_MODE=index_page", raw_env)
        self.assertIn("CODEKB_PUBLISH_INDEX_DOCID=401", raw_env)
        self.assertNotIn("401", raw_config_response)

    def test_api_diagnose_final_verification_serves_safe_guide(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_IM_APP_SECRET": os.environ.get("CODEKB_IM_APP_SECRET"),
            "CODEKB_IM_OAUTH_STATE_SECRET": os.environ.get("CODEKB_IM_OAUTH_STATE_SECRET"),
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / "p5.env"
            env_file.write_text(
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n",
                encoding="utf-8",
            )
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(tmp_path / "tokens.json")
            os.environ["CODEKB_IM_APP_SECRET"] = "app-secret-value"
            os.environ["CODEKB_IM_OAUTH_STATE_SECRET"] = "state-secret-value"
            os.environ["CODEKB_ENV_FILE"] = str(env_file)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/diagnose/final-verification")
                page = client.get("/diagnose/final-verification/page")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = response.json()
        raw = json.dumps(payload, ensure_ascii=False) + page.text
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertFalse(payload["accepted"])
        self.assertIn("im_oauth", {phase["id"] for phase in payload["phases"]})
        self.assertIn("current_user_auth", {phase["id"] for phase in payload["phases"]})
        self.assertIn("http://testserver/diagnose/final-verification/page", raw)
        self.assertIn("http://testserver/auth/im/token-bindings/page", raw)
        self.assertIn("diagnose-p5-final-verify", raw)
        self.assertIn("current_authenticated_user", raw)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("Code-KB P5 Final Verification", page.text)
        self.assertNotIn("app-secret-value", raw)
        self.assertNotIn("state-secret-value", raw)

    def test_api_im_configure_requires_admin_and_sanitizes(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
        }
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "p5.env"
            env_file.write_text("CODEKB_IM_OAUTH_STATE_SECRET=state-secret\n", encoding="utf-8")
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-secret"
            os.environ["CODEKB_ENV_FILE"] = str(env_file)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                payload = {
                    "corp_id": "corp-id",
                    "agent_id": "100001",
                    "app_secret": "app-secret",
                    "redirect_uri": "https://kb.example/auth/im/oauth/callback",
                }
                denied = client.post("/auth/im/configure", json=payload)
                planned = client.post(
                    "/auth/im/configure",
                    headers={"X-CodeKB-Admin-Token": "admin-secret"},
                    json=payload,
                )
                applied = client.post(
                    "/auth/im/configure",
                    headers={"X-CodeKB-Admin-Token": "admin-secret"},
                    json={**payload, "apply": True},
                )
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            raw_env = env_file.read_text(encoding="utf-8")
            mode = env_file.stat().st_mode & 0o777

        planned_payload = planned.json()
        applied_payload = applied.json()
        raw_response = json.dumps([planned_payload, applied_payload], ensure_ascii=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned_payload["status"], "ready_to_apply")
        self.assertFalse(planned_payload["applied"])
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied_payload["status"], "applied")
        self.assertTrue(applied_payload["applied"])
        self.assertTrue(applied_payload["restart_required"])
        self.assertEqual(mode, 0o600)
        self.assertIn("CODEKB_IM_CORP_ID=corp-id", raw_env)
        self.assertIn("CODEKB_IM_APP_SECRET=app-secret", raw_env)
        self.assertIn("CODEKB_IM_OAUTH_STATE_SECRET=state-secret", raw_env)
        self.assertNotIn("corp-id", raw_response)
        self.assertNotIn("app-secret", raw_response)
        self.assertNotIn("state-secret", raw_response)
        self.assertNotIn("admin-secret", raw_response)

    def test_api_im_configure_page_serves_tool_html(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/auth/im/configure/page")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Code-KB IM Config", response.text)
        self.assertIn("admin-token", response.text)
        self.assertIn("/auth/im/configure", response.text)
        self.assertIn("/auth/im/mcp/setup/status", response.text)

    def test_api_diagnose_external_state_serves_safe_snapshot(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_ENV_FILE": os.environ.get("CODEKB_ENV_FILE"),
            "CODEKB_P5_IM_TEMPLATE": os.environ.get("CODEKB_P5_IM_TEMPLATE"),
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / "p5.env"
            template = tmp_path / "im-config.todo.env"
            env_file.write_text(
                "CODEKB_IM_APP_SECRET=app-secret-value\n"
                "CODEKB_IM_OAUTH_STATE_SECRET=state-secret-value\n",
                encoding="utf-8",
            )
            template.write_text("CODEKB_IM_CORP_ID=corp\n", encoding="utf-8")
            os.environ["CODEKB_ENV_FILE"] = str(env_file)
            os.environ["CODEKB_P5_IM_TEMPLATE"] = str(template)
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(tmp_path / "tokens.json")
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"] = str(tmp_path / "samples.real.yaml")
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                response = client.get("/diagnose/external-state")
                payload = response.json()
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        raw = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "pending_external_inputs")
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["secret_values_written"])
        self.assertIn("im_template", {check["id"] for check in payload["checks"]})
        self.assertIn("mcp_auth", payload["pending_checks"])
        self.assertIn("external_platform_samples", payload["pending_checks"])
        self.assertIn(str(env_file), raw)
        self.assertNotIn("app-secret-value", raw)
        self.assertNotIn("state-secret-value", raw)

    def test_api_diagnose_confirmation_policy_writes_current_user_outbox_without_leaking(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_USER_CONFIRMATION_OUTBOX": os.environ.get("CODEKB_USER_CONFIRMATION_OUTBOX"),
            "CODEKB_USER_CONFIRMATION_RESPONSES": os.environ.get("CODEKB_USER_CONFIRMATION_RESPONSES"),
            "CODEKB_FEEDBACK_LOG": os.environ.get("CODEKB_FEEDBACK_LOG"),
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_TRACE_LOG": os.environ.get("CODEKB_TRACE_LOG"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            token_store = tmp_path / "tokens.json"
            outbox = tmp_path / "confirmations.jsonl"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(token_store)
            os.environ["CODEKB_USER_CONFIRMATION_OUTBOX"] = str(outbox)
            os.environ["CODEKB_USER_CONFIRMATION_RESPONSES"] = str(tmp_path / "responses.jsonl")
            os.environ["CODEKB_FEEDBACK_LOG"] = str(tmp_path / "feedback.jsonl")
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending")
            os.environ["CODEKB_TRACE_LOG"] = str(tmp_path / "trace.jsonl")
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            legacy_route = JsonUserTokenStore(token_store).issue(
                user_id_hash="legacy_interface_hash",
                display_name="Legacy Interface",
                scopes=["diagnose"],
                metadata={"im_userid": "legacy-interface-user"},
            )
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post(
                    "/diagnose",
                    json={
                        "auth_token": "bad-token",
                        "query": "DEVICE_SEQ 是什么？",
                        "sub_kbs": ["testing"],
                        "confirmation_policy": "always",
                    },
                )
                response = client.post(
                    "/diagnose",
                    json={
                        "auth_token": issued["token"],
                        "query": "DEVICE_SEQ 是什么？",
                        "sub_kbs": ["testing"],
                        "include_governance": False,
                        "owner": "legacy-owner",
                        "interface_person": "legacy-interface-user",
                        "confirmation_policy": "always",
                        "confirmation_reason": "interaction_complete",
                        "confirmation_message": "请确认本次 AI 交互是否完成",
                        "confirmation_payload": {"surface": "http_api"},
                    },
                )
                pending = client.post(
                    "/auth/im/confirmations/pending",
                    json={"auth_token": issued["token"]},
                )
                raw_outbox = outbox.read_text(encoding="utf-8")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        current_prefix = hashlib.sha256(issued["token"].encode("utf-8")).hexdigest()[:12]
        legacy_prefix = hashlib.sha256(legacy_route["token"].encode("utf-8")).hexdigest()[:12]
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertIn("confirmation", payload)
        self.assertEqual(payload["confirmation"]["reason"], "interaction_complete")
        self.assertEqual(payload["confirmation"]["target_user_token_hash_prefix"], current_prefix)
        self.assertNotEqual(payload["confirmation"]["target_user_token_hash_prefix"], legacy_prefix)
        self.assertEqual(payload["confirmation"]["payload"]["surface"], "http_api")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["total"], 1)
        self.assertNotIn(issued["token"], raw_response + raw_outbox)
        self.assertNotIn(legacy_route["token"], raw_response + raw_outbox)
        self.assertNotIn("ww-user", raw_response + raw_outbox)
        self.assertNotIn("legacy-interface-user", raw_response + raw_outbox)
        self.assertNotIn('"target_user_token_hash"', raw_response)

    def test_api_diagnose_webhook_confirmation_policy_writes_current_user_outbox_without_leaking(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_DIAGNOSE_WEBHOOK_TOKEN": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_TOKEN"),
            "CODEKB_DIAGNOSE_WEBHOOK_LOG": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_LOG"),
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_USER_CONFIRMATION_OUTBOX": os.environ.get("CODEKB_USER_CONFIRMATION_OUTBOX"),
            "CODEKB_USER_CONFIRMATION_RESPONSES": os.environ.get("CODEKB_USER_CONFIRMATION_RESPONSES"),
            "CODEKB_FEEDBACK_LOG": os.environ.get("CODEKB_FEEDBACK_LOG"),
            "CODEKB_CANDIDATE_STORE": os.environ.get("CODEKB_CANDIDATE_STORE"),
            "CODEKB_PENDING_DOCS_DIR": os.environ.get("CODEKB_PENDING_DOCS_DIR"),
            "CODEKB_TRACE_LOG": os.environ.get("CODEKB_TRACE_LOG"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            token_store = tmp_path / "tokens.json"
            outbox = tmp_path / "confirmations.jsonl"
            webhook_log = tmp_path / "webhook.jsonl"
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_TOKEN"] = "webhook-token"
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_LOG"] = str(webhook_log)
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(token_store)
            os.environ["CODEKB_USER_CONFIRMATION_OUTBOX"] = str(outbox)
            os.environ["CODEKB_USER_CONFIRMATION_RESPONSES"] = str(tmp_path / "responses.jsonl")
            os.environ["CODEKB_FEEDBACK_LOG"] = str(tmp_path / "feedback.jsonl")
            os.environ["CODEKB_CANDIDATE_STORE"] = str(tmp_path / "candidates.json")
            os.environ["CODEKB_PENDING_DOCS_DIR"] = str(tmp_path / "pending")
            os.environ["CODEKB_TRACE_LOG"] = str(tmp_path / "trace.jsonl")
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            legacy_route = JsonUserTokenStore(token_store).issue(
                user_id_hash="legacy_interface_hash",
                display_name="Legacy Interface",
                scopes=["diagnose"],
                metadata={"im_userid": "legacy-interface-user"},
            )
            base_payload = {
                "repository": {"path": "ym/app", "owner": "legacy-owner"},
                "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ 构建失败"},
                "sub_kbs": ["testing"],
                "owner": "legacy-owner",
                "interface_person": "legacy-interface-user",
                "confirmation_policy": "always",
                "confirmation_reason": "problem_solved",
                "confirmation_message": "请确认本次问题是否已解决",
                "confirmation_payload": {"surface": "webhook_api"},
            }
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post(
                    "/diagnose/webhook/code_review",
                    headers={"X-CodeKB-Token": "webhook-token"},
                    json={**base_payload, "auth_token": "bad-token"},
                )
                response = client.post(
                    "/diagnose/webhook/code_review",
                    headers={"X-CodeKB-Token": "webhook-token"},
                    json={**base_payload, "auth_token": issued["token"]},
                )
                raw_outbox = outbox.read_text(encoding="utf-8")
                raw_webhook_log = webhook_log.read_text(encoding="utf-8")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        combined = raw_response + raw_outbox + raw_webhook_log
        current_prefix = hashlib.sha256(issued["token"].encode("utf-8")).hexdigest()[:12]
        legacy_prefix = hashlib.sha256(legacy_route["token"].encode("utf-8")).hexdigest()[:12]
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "diagnosed")
        self.assertIn("confirmation", payload)
        self.assertEqual(payload["confirmation"]["reason"], "problem_solved")
        self.assertEqual(payload["confirmation"]["target_user_token_hash_prefix"], current_prefix)
        self.assertNotEqual(payload["confirmation"]["target_user_token_hash_prefix"], legacy_prefix)
        self.assertEqual(payload["confirmation"]["payload"]["surface"], "webhook_api")
        self.assertNotIn(issued["token"], combined)
        self.assertNotIn(legacy_route["token"], combined)
        self.assertNotIn("bad-token", combined)
        self.assertNotIn("ww-user", combined)
        self.assertNotIn("legacy-interface-user", combined)
        self.assertNotIn('"target_user_token_hash"', raw_response)
        self.assertNotIn("auth_token", raw_webhook_log)

    def test_api_confirmation_request_queues_current_user_confirmation_without_leaking(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_USER_TOKEN_STORE": os.environ.get("CODEKB_USER_TOKEN_STORE"),
            "CODEKB_USER_CONFIRMATION_OUTBOX": os.environ.get("CODEKB_USER_CONFIRMATION_OUTBOX"),
            "CODEKB_USER_CONFIRMATION_RESPONSES": os.environ.get("CODEKB_USER_CONFIRMATION_RESPONSES"),
        }
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            token_store = tmp_path / "tokens.json"
            outbox = tmp_path / "confirmations.jsonl"
            os.environ["CODEKB_USER_TOKEN_STORE"] = str(token_store)
            os.environ["CODEKB_USER_CONFIRMATION_OUTBOX"] = str(outbox)
            os.environ["CODEKB_USER_CONFIRMATION_RESPONSES"] = str(tmp_path / "responses.jsonl")
            issued = JsonUserTokenStore(token_store).issue(
                user_id_hash="u_hash",
                scopes=["diagnose"],
                metadata={"im_userid": "ww-user"},
            )
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                denied = client.post(
                    "/auth/im/confirmations/request",
                    json={"auth_token": "bad-token", "reason": "problem_solved", "message": "done"},
                )
                response = client.post(
                    "/auth/im/confirmations/request",
                    json={
                        "auth_token": issued["token"],
                        "reason": "problem_solved",
                        "message": "请确认本次问题是否已解决",
                        "payload": {"diagnosis_id": "diag-1"},
                    },
                )
                pending = client.post(
                    "/auth/im/confirmations/pending",
                    json={"auth_token": issued["token"]},
                )
                raw_outbox = outbox.read_text(encoding="utf-8")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = response.json()
        raw_response = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["confirmation"]["reason"], "problem_solved")
        self.assertEqual(payload["confirmation"]["payload"]["diagnosis_id"], "diag-1")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["total"], 1)
        self.assertNotIn(issued["token"], raw_response + raw_outbox)
        self.assertNotIn("ww-user", raw_response + raw_outbox)
        self.assertNotIn('"target_user_token_hash"', raw_response)

    def test_api_diagnose_integrations_serves_artifacts(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                aliases_path=str(root / "data" / "entity_aliases.yaml"),
                registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
            )
        )

        response = client.get("/diagnose/integrations")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mcp_tools"], 4)
        self.assertIn("current_user_auth.md", payload["files"])
        self.assertIn("external_handoff.md", payload["files"])
        self.assertIn("/auth/im/mcp/setup", payload["artifacts"]["current_user_auth.md"])
        self.assertIn("POST http://testserver/diagnose/webhook/{source}", payload["artifacts"]["current_user_auth.md"])
        self.assertIn("X-CodeKB-Token", payload["artifacts"]["external_handoff.md"])
        self.assertIn(
            "Confirmation target is always the current authenticated user bound to `auth_token`",
            payload["artifacts"]["external_handoff.md"],
        )
        self.assertIn(
            "interface-person fields are not used for P5 routing",
            payload["artifacts"]["external_handoff.md"],
        )
        self.assertIn("CODEKB_IM_APP_SECRET", payload["artifacts"]["external_handoff.md"])
        self.assertIn("auth_token", payload["artifacts"]["mcp_tools.json"][0]["inputSchema"]["required"])
        self.assertNotIn("CODEKB_IM_APP_SECRET=", response.text)

    def test_api_diagnose_webhook_sample_import_requires_admin_and_sanitizes(self):
        try:
            from fastapi.testclient import TestClient
        except (ModuleNotFoundError, RuntimeError) as exc:
            self.skipTest(f"FastAPI TestClient is not available: {exc}")
        root = Path(__file__).resolve().parents[1]
        original = {
            "CODEKB_AUTH_ADMIN_TOKEN": os.environ.get("CODEKB_AUTH_ADMIN_TOKEN"),
            "CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES": os.environ.get("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"),
        }
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "samples.real.yaml"
            os.environ["CODEKB_AUTH_ADMIN_TOKEN"] = "admin-token"
            os.environ["CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES"] = str(output)
            try:
                client = TestClient(
                    create_app(
                        fixture_path=str(root / "data" / "fixtures" / "sample_corpus.jsonl"),
                        aliases_path=str(root / "data" / "entity_aliases.yaml"),
                        registry_path=str(root / "docs" / "kb-registry.draft.yaml"),
                    )
                )
                request_payload = {
                    "name": "http_imported_code_review",
                    "payload": {
                        "repository": {"path": "ym/app"},
                        "pipeline": {"url": "https://example.invalid/build?token=http-token-secret"},
                        "error": {"code": "DEVICE_SEQ", "message": "DEVICE_SEQ password=http-password-secret"},
                        "sub_kbs": ["testing"],
                    },
                }
                denied = client.post("/diagnose/webhook/code_review/sample-import", json=request_payload)
                response = client.post(
                    "/diagnose/webhook/code_review/sample-import",
                    headers={"X-CodeKB-Admin-Token": "admin-token"},
                    json=request_payload,
                )
                raw_output = output.read_text(encoding="utf-8")
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        payload = response.json()
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "imported")
        self.assertEqual(payload["validation"]["status"], "passed")
        self.assertFalse(payload["raw_sensitive_values_leaked"])
        self.assertFalse(payload["output_is_active"])
        self.assertIn("CODEKB_DIAGNOSE_WEBHOOK_SAMPLES", " ".join(payload["next_steps"]))
        self.assertNotIn("http-token-secret", raw_output)
        self.assertNotIn("http-password-secret", raw_output)
        self.assertNotIn("http-token-secret", response.text)
        self.assertIn("[REDACTED]", raw_output)


if __name__ == "__main__":
    unittest.main()
