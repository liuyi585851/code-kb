from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    from fastapi import Request as FastAPIRequest
except ModuleNotFoundError:
    FastAPIRequest = Any

from .candidate import JsonCandidateStore, parse_audit_payload, parse_ingest_payload, parse_revision_payload
from .audit_page import render_audit_page
from .current_user_demo_page import render_current_user_demo_page
from .diagnosis import submit_diagnostic_gap
from .diagnosis_confirmation import (
    confirmation_policy,
    maybe_append_diagnosis_confirmation,
    public_confirmation_request,
)
from .diagnosis_acceptance import (
    build_p5_acceptance_report,
    build_p5_external_input_plan,
    render_p5_external_input_plan_markdown,
)
from .diagnosis_context import build_diagnostic_query, parse_diagnostic_context
from .diagnosis_gaps import summarize_diagnostic_gaps
from .usage import record_event, summarize_usage, usage_log_path
from .diagnosis_integrations import diagnose_integration_artifacts
from .diagnosis_readiness import build_p5_readiness_report
from .diagnosis_webhook import (
    DEFAULT_WEBHOOK_MAPPING_PATH,
    DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    JsonlDiagnosticWebhookStore,
    effective_diagnostic_webhook_mapping,
    import_diagnostic_webhook_sample,
    normalize_diagnostic_webhook,
    preview_diagnostic_webhook,
    validate_diagnostic_webhook,
    validate_diagnostic_webhook_sample_suite,
)
from .diagnosis_webhook_signing import verify_webhook_signature
from .feedback import JsonlFeedbackStore, parse_feedback_payload, summarize_feedback
from .external_index_sync import sync_external_index_artifacts
from .governance import (
    build_curator_weekly_report,
    build_governance_report,
    build_governance_ticket_plans,
    load_governance_policy,
    summarize_governance_state,
)
from .ask_page import render_ask_page
from .code_nav import file_outline, find_files, get_symbol, list_dir, read_file_range, search_code
from .hub_page import render_hub_page
from .index_rebuild import rebuild_search_index
from .local_index import local_index_stats
from .models import AnswerResult
from .p5_external_state import (
    DEFAULT_P5_ENV_FILE,
    DEFAULT_REAL_SAMPLES,
    DEFAULT_IM_TEMPLATE,
    build_p5_external_state,
)
from .p5_external_input_page import render_p5_external_input_page
from .p5_final_verification_guide import build_p5_final_verification_guide, render_p5_final_verification_page
from .publish import build_publish_plans
from .wiki_publish_client import HttpWikiPublishClient
from .publish_api import build_publish_readiness, plan_publish_outbox, process_publish_outbox_report
from .publish_config import configure_publish_env
from .embedding_config import load_embedding_config
from .qdrant_page import render_qdrant_page
from .registry import load_registry
from .service import OfflineKbService
from .storage_integrations import build_qdrant_status, build_storage_readiness
from .user_auth import (
    DEFAULT_IM_API_BASE,
    DEFAULT_IM_OAUTH_AUTHORIZE_BASE,
    DEFAULT_IM_OAUTH_SCOPE,
    JsonUserTokenStore,
    IMOAuthClient,
    issue_im_oauth_token,
    make_im_oauth_state,
    public_token_metadata,
    verify_im_oauth_state,
)
from .user_auth_page import render_im_mcp_setup_page, render_im_oauth_success_page
from .im_config import configure_im_env
from .im_config_page import render_im_config_page
from .webhook_demo_page import render_webhook_demo_page
from .user_self_binding_page import render_user_self_binding_page
from .user_token_binding_page import render_user_token_binding_page
from .user_confirmation import (
    JsonlUserConfirmationOutbox,
    JsonlUserConfirmationResponseStore,
    get_user_confirmation_detail,
    list_user_confirmations,
    public_confirmation_response,
)
from .user_confirmation_page import render_user_confirmation_page


def _resolve_qdrant_vector_size() -> int:
    """单一来源解析 Qdrant 向量维度。

    设了正整数的 ``CODEKB_QDRANT_VECTOR_SIZE`` 就用它显式覆盖,否则回退到配置的
    embedding 维度(默认 64),保证维度只有一个来源。
    """

    raw = os.getenv("CODEKB_QDRANT_VECTOR_SIZE", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return load_embedding_config().dimensions


def answer_result_to_dict(answer: AnswerResult) -> dict:
    return {
        "answer_id": answer.answer_id,
        "trace_id": answer.trace_id,
        "query": answer.query,
        "answer": answer.answer,
        "refused": answer.refused,
        "refusal_reason": answer.refusal_reason,
        "confidence": answer.confidence,
        "generation_mode": answer.generation_mode,
        "model": answer.model_id,
        "latency_ms": answer.latency_ms,
        "fallback_reason": answer.fallback_reason,
        "citations": [
            {
                "atom_id": citation.atom_id,
                "docid": citation.docid,
                "title": citation.title,
                "anchor": citation.anchor,
                "section_path": list(citation.section_path),
                "quote": citation.quote,
                "score": citation.score,
                # 代码定位字段只对代码原子输出,供客户端模型定位到具体 file:line
                # 并拉取相关代码。
                **(
                    {
                        "file_path": citation.file_path,
                        "start_line": citation.start_line,
                        "end_line": citation.end_line,
                        "language": citation.language,
                        "repo_id": citation.repo_id,
                        "symbol": citation.qualified_symbol,
                    }
                    if getattr(citation, "start_line", 0)
                    else {}
                ),
            }
            for citation in answer.citations
        ],
    }


def _parse_sub_kbs(value: object) -> set[str] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    raise ValueError("sub_kbs must be a string or list")


def _parse_top_k(value: object) -> int:
    if value in (None, ""):
        return 4
    try:
        top_k = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("top_k must be an integer") from exc
    if top_k < 1 or top_k > 20:
        raise ValueError("top_k must be between 1 and 20")
    return top_k


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError("boolean value must be true or false")


def _parse_min_confidence(value: object) -> float:
    if value in (None, ""):
        return 0.35
    try:
        min_confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_confidence must be a number") from exc
    if min_confidence < 0 or min_confidence > 1:
        raise ValueError("min_confidence must be between 0 and 1")
    return min_confidence


def create_app(
    *,
    fixture_path: str | None = None,
    aliases_path: str | None = None,
    registry_path: str | None = None,
):
    try:
        from fastapi import FastAPI, Header, HTTPException, Query
        from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
    except ModuleNotFoundError as exc:
        raise RuntimeError("FastAPI is not installed. Install codekb[api] to enable the API.") from exc

    app = FastAPI(title="Code-KB", version="0.1.0")

    # ---- 用量埋点:记录每次 KB 工具调用(频次 / 延迟 / 命中率) ----
    _usage_tools = {
        "/code/search": "search_code",
        "/code/symbol": "get_symbol",
        "/code/files": "find_files",
        "/code/dir": "list_dir",
        "/code/read": "read_file_range",
        "/code/outline": "file_outline",
        "/ask": "ask",
    }

    def _usage_results(data: Any) -> tuple[int | None, bool | None]:
        if not isinstance(data, dict):
            return None, None
        if "hits" in data:
            return len(data.get("hits") or []), None
        if "matches" in data:
            return len(data.get("matches") or []), None
        if "citations" in data:
            return len(data.get("citations") or []), bool(data.get("refused"))
        if "files" in data and "count" in data:
            return data.get("count"), None
        if "dir_count" in data:
            return int(data.get("dir_count", 0)) + int(data.get("file_count", 0)), None
        if "found" in data:
            return data.get("found"), None
        if "symbols" in data:
            return data.get("count"), None
        return None, None

    @app.middleware("http")
    async def _usage_middleware(request, call_next):  # noqa: ANN001
        import time as _time

        tool = _usage_tools.get(request.url.path)
        if not tool or not usage_log_path():
            return await call_next(request)
        query = ""
        try:
            raw = await request.body()  # 已缓存在 request 上,后面的 handler 还能再读
            if raw:
                payload = json.loads(raw)
                query = str(payload.get("query") or payload.get("name") or payload.get("pattern") or payload.get("prefix") or "")
        except Exception:  # noqa: BLE001
            query = ""
        start = _time.perf_counter()
        response = await call_next(request)
        latency_ms = (_time.perf_counter() - start) * 1000
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        results = refused = None
        try:
            results, refused = _usage_results(json.loads(body))
        except Exception:  # noqa: BLE001
            pass
        record_event(
            tool,
            source=request.headers.get("x-codekb-source") or "http",
            query=query,
            results=results,
            refused=refused,
            latency_ms=latency_ms,
            ok=response.status_code < 400,
            extra={"status": response.status_code},
        )
        from starlette.responses import Response as _StarletteResponse

        return _StarletteResponse(content=body, status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)
    fixture_path = fixture_path or os.getenv("CODEKB_FIXTURES", "data/fixtures/sample_corpus.jsonl")
    aliases_path = aliases_path or os.getenv("CODEKB_ALIASES", "data/entity_aliases.yaml")
    registry_path = registry_path or os.getenv("CODEKB_REGISTRY", "docs/kb-registry.draft.yaml")
    index_db_path = os.getenv("CODEKB_INDEX_DB")
    feedback_log_path = os.getenv("CODEKB_FEEDBACK_LOG", "/data/codekb/logs/feedback.jsonl")
    candidate_store_path = os.getenv("CODEKB_CANDIDATE_STORE", "/data/codekb/state/candidates.json")
    pending_docs_dir = os.getenv("CODEKB_PENDING_DOCS_DIR", "/data/codekb/pending-docs")
    publish_outbox_path = os.getenv("CODEKB_PUBLISH_OUTBOX", "/data/codekb/outbox/wiki-publish-plan.jsonl")
    publish_report_path = os.getenv("CODEKB_PUBLISH_REPORT", "/data/codekb/logs/wiki-publish-report.json")
    publish_ledger_path = os.getenv("CODEKB_PUBLISH_LEDGER", "/data/codekb/state/wiki-publish-ledger.jsonl")
    governance_state_path = os.getenv("CODEKB_GOVERNANCE_STATE", "/data/codekb/state/governance-state.json")
    governance_policy_path = os.getenv("CODEKB_GOVERNANCE_POLICY", "docs/governance-policy.draft.yaml")
    index_artifacts_dir_path = os.getenv("CODEKB_INDEX_ARTIFACTS_DIR", "/data/codekb/state/index-artifacts")
    diagnose_webhook_log_path = os.getenv(
        "CODEKB_DIAGNOSE_WEBHOOK_LOG",
        "/data/codekb/logs/diagnose-webhook.jsonl",
    )
    diagnose_webhook_mapping_path = os.getenv("CODEKB_DIAGNOSE_WEBHOOK_MAPPING", DEFAULT_WEBHOOK_MAPPING_PATH)
    diagnose_webhook_samples_path = os.getenv(
        "CODEKB_DIAGNOSE_WEBHOOK_SAMPLES",
        DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH,
    )
    user_token_store_path = os.getenv("CODEKB_USER_TOKEN_STORE", "/data/codekb/state/user-tokens.json")
    user_confirmation_outbox_path = os.getenv(
        "CODEKB_USER_CONFIRMATION_OUTBOX",
        "/data/codekb/outbox/user-confirmation.jsonl",
    )
    user_confirmation_responses_path = os.getenv(
        "CODEKB_USER_CONFIRMATION_RESPONSES",
        "/data/codekb/state/user-confirmation-responses.jsonl",
    )
    user_confirmation_report_path = os.getenv(
        "CODEKB_USER_CONFIRMATION_REPORT",
        "/data/codekb/logs/user-confirmation-report.json",
    )
    user_confirmation_delivery_log_path = os.getenv(
        "CODEKB_USER_CONFIRMATION_DELIVERY_LOG",
        "/data/codekb/state/user-confirmation-delivery.jsonl",
    )
    p5_env_file_path = os.getenv("CODEKB_ENV_FILE", DEFAULT_P5_ENV_FILE)
    p5_im_template_path = os.getenv("CODEKB_P5_IM_TEMPLATE", DEFAULT_IM_TEMPLATE)
    p5_real_samples_path = os.getenv("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", DEFAULT_REAL_SAMPLES)
    feedback_store = JsonlFeedbackStore(feedback_log_path)
    candidate_store = JsonCandidateStore(candidate_store_path, pending_docs_dir=pending_docs_dir)
    diagnose_webhook_store = JsonlDiagnosticWebhookStore(diagnose_webhook_log_path)
    user_token_store = JsonUserTokenStore(user_token_store_path)
    user_confirmation_response_store = JsonlUserConfirmationResponseStore(user_confirmation_responses_path)
    service = OfflineKbService(
        fixture_path=fixture_path,
        aliases_path=aliases_path,
        trace_log_path=os.getenv("CODEKB_TRACE_LOG"),
        retriever_mode=os.getenv("CODEKB_RETRIEVER", "bm25-lite"),
        index_db_path=index_db_path,
        qdrant_url=os.getenv("QDRANT_URL") or os.getenv("CODEKB_QDRANT_URL"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY") or os.getenv("CODEKB_QDRANT_API_KEY"),
        qdrant_collection=os.getenv("CODEKB_QDRANT_COLLECTION", "codekb_atoms"),
        qdrant_timeout_seconds=int(os.getenv("CODEKB_QDRANT_TIMEOUT_SECONDS", "3")),
        atom_store_mode=os.getenv("CODEKB_ATOM_STORE", "auto"),
        postgres_dsn=os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL"),
    )

    # 在后台线程里给常用的 sub_kbs 切片预建 BM25-lite 倒排索引,免得重启后第一条真实
    # 查询被一次性的 O(corpus) 建索引拖住(约 19 万原子时要 ~80s)。非阻塞;设
    # CODEKB_PREWARM=0 可关闭。
    if os.getenv("CODEKB_PREWARM", "1").strip().lower() not in {"0", "false", "no", "off"}:
        import threading as _threading

        def _prewarm_indexes() -> None:
            slices = (
                None,
                {"code", "docs"},
                {"code", "docs", "testing", "release", "incident"},
            )
            for sub_kbs in slices:
                try:
                    service.retriever.retrieve("warm", sub_kbs=sub_kbs, top_k=1)
                except Exception:  # noqa: BLE001 - 预热尽力而为,失败忽略
                    pass

        _threading.Thread(target=_prewarm_indexes, name="bm25-prewarm", daemon=True).start()

    def _rebuild_api_index(payload: dict) -> dict[str, Any]:
        if not index_db_path:
            raise RuntimeError("CODEKB_INDEX_DB is not configured")
        include_paths = _index_rebuild_include_paths(payload)
        return rebuild_search_index(
            fixture_path=fixture_path,
            db_path=index_db_path,
            include_paths=include_paths,
            atomic=_parse_bool(payload.get("atomic"), default=True),
        )

    def _index_rebuild_include_paths(payload: dict) -> list[str]:
        include_paths = []
        if _parse_bool(payload.get("include_pending_docs"), default=True):
            include_paths.append(pending_docs_dir)
        include_paths.extend(_parse_optional_string_list(payload.get("include_sources")) or [])
        return include_paths

    def _maybe_rebuild_index_after_audit(result, payload: dict) -> dict[str, Any] | None:
        if result.candidate.status != "approved":
            return None
        rebuild_requested = _parse_bool(
            payload.get("rebuild_index"),
            default=os.getenv("CODEKB_REBUILD_INDEX_ON_AUDIT_APPROVE", "").strip() == "1",
        )
        if not rebuild_requested:
            return None
        try:
            return _rebuild_api_index(
                {
                    "include_pending_docs": True,
                    "include_sources": payload.get("include_sources") or [],
                    "atomic": payload.get("atomic", True),
                }
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "db_path": index_db_path or "",
            }

    def _maybe_sync_external_storage_after_audit(
        result,
        payload: dict,
        index_rebuild_report: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if result.candidate.status != "approved":
            return None
        sync_requested = _parse_external_storage_sync_requested(payload)
        if not sync_requested:
            return None
        if index_rebuild_report is not None and index_rebuild_report.get("status") != "rebuilt":
            return {
                "status": "skipped",
                "reason": "index_rebuild_not_ready",
                "index_rebuild_status": index_rebuild_report.get("status", ""),
            }
        try:
            return sync_external_index_artifacts(
                fixture_path=fixture_path,
                output_dir=payload.get("storage_sync_output_dir") or index_artifacts_dir_path,
                include_paths=_index_rebuild_include_paths(
                    {
                        "include_pending_docs": True,
                        "include_sources": payload.get("include_sources") or [],
                    }
                ),
                env_file=p5_env_file_path,
                execute=_parse_external_storage_sync_execute(payload),
                qdrant_collection=os.getenv("CODEKB_QDRANT_COLLECTION", "codekb_atoms"),
                qdrant_vector_size=_resolve_qdrant_vector_size(),
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }

    def _parse_external_storage_sync_requested(payload: dict) -> bool:
        if "sync_external_storage" in payload:
            return _parse_bool(payload.get("sync_external_storage"), default=False)
        mode = os.getenv("CODEKB_SYNC_EXTERNAL_ON_AUDIT_APPROVE", "").strip().lower()
        return mode in {"1", "true", "yes", "on", "planned", "dry-run", "dry_run", "execute"}

    def _parse_external_storage_sync_execute(payload: dict) -> bool:
        if "execute_external_storage_sync" in payload:
            return _parse_bool(payload.get("execute_external_storage_sync"), default=False)
        mode = os.getenv("CODEKB_SYNC_EXTERNAL_ON_AUDIT_APPROVE", "").strip().lower()
        return mode == "execute"

    _web_dir = Path(__file__).resolve().parent / "web"

    @app.get("/")
    def console_spa():
        return FileResponse(_web_dir / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/static/app.css")
    def console_css():
        return FileResponse(_web_dir / "app.css", media_type="text/css", headers={"Cache-Control": "no-store"})

    @app.get("/static/app.js")
    def console_js():
        return FileResponse(_web_dir / "app.js", media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/hub", response_class=HTMLResponse)
    def hub_page():
        return HTMLResponse(render_hub_page(), headers={"Cache-Control": "no-store"})

    @app.get("/console", response_class=HTMLResponse)
    def ask_console_page():
        return HTMLResponse(render_ask_page(), headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def healthz():
        fixture_exists = Path(fixture_path).exists()
        aliases_exists = Path(aliases_path).exists() if aliases_path else True
        registry_exists = Path(registry_path).exists()
        governance_policy_exists = Path(governance_policy_path).exists() if governance_policy_path else False
        index_exists = Path(index_db_path).exists() if index_db_path else False
        components = {
            "api": "ok",
            "fixture_source": "ok" if fixture_exists else "missing",
            "aliases": "ok" if aliases_exists else "missing",
            "registry": "ok" if registry_exists else "missing",
            "governance_policy": "ok" if governance_policy_exists else "default",
            "local_index": "ok" if index_exists else "deferred",
            "postgres": "configured" if os.getenv("POSTGRES_DSN") or os.getenv("POSTGRES_HOST") else "deferred",
            "opensearch": "configured" if os.getenv("OPENSEARCH_URL") or os.getenv("ES_URL") else "deferred",
            "qdrant": "configured" if os.getenv("QDRANT_URL") else "deferred",
            "retriever": os.getenv("CODEKB_RETRIEVER", "bm25-lite"),
            "atom_store": os.getenv("CODEKB_ATOM_STORE", "auto"),
            "im_oauth": "configured" if _im_oauth_env_configured() else "deferred",
        }
        status = "ok" if all(value != "missing" for value in components.values()) else "degraded"
        payload = {"status": status, "components": components}
        if index_exists:
            payload["local_index"] = local_index_stats(index_db_path)
        try:
            payload["candidates"] = candidate_store.summary()
        except Exception as exc:
            payload["candidates"] = {"status": "error", "error": str(exc)}
        return payload

    @app.get("/index/status")
    def index_status():
        if not index_db_path:
            return {
                "status": "deferred",
                "configured": False,
                "db_path": "",
                "message": "CODEKB_INDEX_DB is not configured",
            }
        if not Path(index_db_path).exists():
            return {
                "status": "missing",
                "configured": True,
                "db_path": index_db_path,
                "message": "index DB does not exist",
            }
        try:
            return {"status": "ok", "configured": True, **local_index_stats(index_db_path)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"index status failed: {exc}") from exc

    @app.get("/storage/readiness")
    def storage_readiness(timeout_seconds: int = 3):
        if timeout_seconds < 1 or timeout_seconds > 10:
            raise HTTPException(status_code=400, detail="timeout_seconds must be between 1 and 10")
        return build_storage_readiness(timeout_seconds=timeout_seconds)

    @app.get("/storage/qdrant/status")
    def storage_qdrant_status(collection: str = "codekb_atoms", timeout_seconds: int = 3):
        if timeout_seconds < 1 or timeout_seconds > 10:
            raise HTTPException(status_code=400, detail="timeout_seconds must be between 1 and 10")
        collection = collection.strip() or "codekb_atoms"
        return build_qdrant_status(collection=collection, timeout_seconds=timeout_seconds)

    @app.get("/admin/storage")
    def admin_storage(x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        from .storage_integrations import qdrant_admin_overview

        cfg = load_embedding_config()
        models = {
            "embedder": {"provider": cfg.provider, "model": cfg.model_id or os.getenv("CODEKB_EMBEDDING_MODEL", ""), "dim": cfg.dimensions},
            "reranker": {
                "provider": os.getenv("CODEKB_RERANK_PROVIDER", "none") or "none",
                "model": os.getenv("CODEKB_RERANK_MODEL", ""),
                "quantize": os.getenv("CODEKB_RERANK_QUANTIZE", "1") not in {"0", "false", "no", "off"},
                "candidates": os.getenv("CODEKB_RERANK_CANDIDATES", "12"),
            },
            "llm": {
                "provider": os.getenv("CODEKB_LLM_PROVIDER", "openai_compat"),
                "model": os.getenv("CODEKB_LLM_MODEL", ""),
                "answer_mode": os.getenv("CODEKB_ANSWER_MODE", "extractive"),
            },
            "retriever": os.getenv("CODEKB_RETRIEVER", "bm25-lite"),
        }
        qdrant = qdrant_admin_overview(os.environ)
        postgres: dict[str, Any] = {"configured": bool(os.getenv("POSTGRES_DSN") or os.getenv("POSTGRES_HOST"))}
        try:
            postgres["total"] = len(service.store)
            counts = getattr(service.store, "counts_by_sub_kb", None)
            if callable(counts):
                postgres["by_sub_kb"] = counts()
        except Exception as exc:  # noqa: BLE001
            postgres["error"] = str(exc)
        index: dict[str, Any] = {}
        if index_db_path and Path(index_db_path).exists():
            try:
                index = local_index_stats(index_db_path)
            except Exception:  # noqa: BLE001
                index = {}
        return {"models": models, "qdrant": qdrant, "postgres": postgres, "index": index}

    @app.get("/admin/qdrant/sample")
    def admin_qdrant_sample(collection: str = "codekb_atoms", limit: int = 10, sub_kb: str = "", x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if limit < 1 or limit > 50:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 50")
        from .storage_integrations import qdrant_sample
        from .code_location import parse_code_location

        res = qdrant_sample(collection.strip() or "codekb_atoms", env=os.environ, limit=limit, sub_kb=sub_kb.strip())
        # Qdrant 里的 payload 很精简(只有 sub_kb_id);从 store 取回真实原子内容补全每条
        # 命中,让管理员看到实际入库的东西。
        enriched = []
        for point in res.get("points", []):
            item = {"id": point.get("id"), "sub_kb_id": (point.get("payload") or {}).get("sub_kb_id", "")}
            try:
                draft = service.store.get(str(point.get("id"))).draft
                loc = parse_code_location(draft)
                item["source_docid"] = draft.source_docid
                if loc:
                    item["location"] = f"{loc.file_path}:L{loc.start_line}-{loc.end_line}"
                item["snippet"] = (draft.text or "")[:400]
            except Exception:  # noqa: BLE001 - 该 point 可能在 store 里查不到
                pass
            enriched.append(item)
        res["points"] = enriched
        return res

    # ---- 内嵌管理控制台:同源反向代理,用 admin cookie 鉴权 ----
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    from fastapi.responses import JSONResponse as _JSONResponse, Response as _Response
    from starlette.concurrency import run_in_threadpool as _to_thread

    _qdrant_base = (os.getenv("QDRANT_URL") or os.getenv("CODEKB_QDRANT_URL") or "http://127.0.0.1:6333").strip().rstrip("/")
    _pgweb_base = (os.getenv("CODEKB_PGWEB_URL") or "http://127.0.0.1:8081").strip().rstrip("/")
    _drop_req_headers = {"host", "cookie", "content-length", "connection", "accept-encoding"}
    _drop_resp_headers = {"x-frame-options", "content-security-policy", "content-encoding", "transfer-encoding", "connection", "content-length", "keep-alive"}
    _proxy_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

    def _dbproxy_token() -> str:
        return os.getenv("CODEKB_AUTH_ADMIN_TOKEN", "").strip()

    def _dbproxy_ok(request) -> bool:
        token = _dbproxy_token()
        return bool(token) and request.cookies.get("kb_dbproxy", "") == token

    def _blocking_proxy(method, url, headers, body):
        request = _urlreq.Request(url, data=body or None, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with _urlreq.urlopen(request, timeout=30) as response:
                return response.status, list(response.headers.items()), response.read()
        except _urlerr.HTTPError as exc:
            return exc.code, list(exc.headers.items()), exc.read()
        except (_urlerr.URLError, TimeoutError) as exc:
            return 502, [("Content-Type", "application/json")], f'{{"detail":"upstream unreachable: {exc}"}}'.encode()

    async def _proxy(request, base, path):
        if not _dbproxy_ok(request):
            return _JSONResponse({"detail": "admin auth required (open the Storage tab and load with your admin token)"}, status_code=401)
        url = base + path
        if request.url.query:
            url += "?" + request.url.query
        body = await request.body()
        forward = {k: v for k, v in request.headers.items() if k.lower() not in _drop_req_headers}
        status, resp_headers, content = await _to_thread(_blocking_proxy, request.method, url, forward, body)
        media_type = ""
        out_headers: dict[str, str] = {}
        for key, value in resp_headers:
            low = key.lower()
            if low in _drop_resp_headers:
                continue
            if low == "content-type":
                media_type = value
            out_headers[key] = value
        return _Response(content=content, status_code=status, headers=out_headers, media_type=media_type or None)

    @app.post("/dbproxy/auth")
    async def dbproxy_auth(payload: dict):
        token = _dbproxy_token()
        if not token or str(payload.get("token", "")).strip() != token:
            raise HTTPException(status_code=401, detail="bad admin token")
        response = _JSONResponse({"ok": True})
        response.set_cookie("kb_dbproxy", token, httponly=True, samesite="lax", max_age=43200, path="/")
        return response

    def _register_console_proxy(prefix: str, base: str):
        # 用 Starlette 层的路由:绕开 FastAPI 的签名校验(代理只要拿到原始 Request 即可),
        # 也避开了 `from __future__ import annotations` 的坑——create_app 内部定义的类型别名
        # FastAPI 在类型提示解析阶段是解不出来的。
        async def endpoint(request):
            sub = request.path_params.get("path", "")
            return await _proxy(request, base, "/" + prefix + (("/" + sub) if sub else ""))

        app.add_route("/" + prefix, endpoint, methods=_proxy_methods)
        app.add_route("/" + prefix + "/{path:path}", endpoint, methods=_proxy_methods)

    # Qdrant 的 dashboard SPA 会用到这些根路径。
    for _qprefix in ("dashboard", "collections", "telemetry", "aliases", "cluster", "snapshots", "locks", "issues"):
        _register_console_proxy(_qprefix, _qdrant_base)
    # pgweb 跑在 /pgweb 前缀下,这样它的静态资源也能在同一路径下解析到。
    _register_console_proxy("pgweb", _pgweb_base)

    @app.get("/storage/qdrant/page", response_class=HTMLResponse)
    def storage_qdrant_page():
        return HTMLResponse(render_qdrant_page(), headers={"Cache-Control": "no-store"})

    @app.post("/index/rebuild")
    def index_rebuild(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return _rebuild_api_index(payload)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"index rebuild failed: {exc}") from exc

    @app.get("/kb/registry")
    def kb_registry():
        try:
            registry = load_registry(registry_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"registry load failed: {exc}") from exc
        return registry_to_dict(registry)

    @app.get("/auth/im/oauth/login")
    def auth_im_oauth_login(request: FastAPIRequest, next_url: str = Query(default="", alias="next")):
        try:
            client = _build_im_oauth_client()
            redirect_uri = _im_oauth_redirect_uri(request)
            state = make_im_oauth_state(_im_oauth_state_secret(), next_url=next_url)
            authorize_url = client.authorize_url(
                redirect_uri=redirect_uri,
                state=state,
                scope=os.getenv("CODEKB_IM_OAUTH_SCOPE", DEFAULT_IM_OAUTH_SCOPE),
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return RedirectResponse(authorize_url)

    @app.get("/auth/im/oauth/callback", response_class=HTMLResponse)
    def auth_im_oauth_callback(code: str = "", state: str = ""):
        try:
            payload = verify_im_oauth_state(state, _im_oauth_state_secret())
            profile = _build_im_oauth_client().exchange_code(code)
            issued = issue_im_oauth_token(
                user_token_store,
                profile,
                scopes=["diagnose"],
                ttl_days=_im_oauth_ttl_days(),
            )
            binding = _public_token_binding(issued["binding"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            status_code = 503 if "required" in str(exc) or "configured" in str(exc) else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return HTMLResponse(
            render_im_oauth_success_page(
                token=issued["token"],
                token_id=str(binding.get("token_id", "")),
                expires_at=str(binding.get("expires_at", "")),
                next_url=str(payload.get("next", "")),
            ),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @app.get("/auth/im/mcp/setup", response_class=HTMLResponse)
    def auth_im_mcp_setup():
        return HTMLResponse(render_im_mcp_setup_page(), headers={"Cache-Control": "no-store"})

    @app.get("/auth/im/mcp/setup/status")
    def auth_im_mcp_setup_status(request: FastAPIRequest):
        try:
            base_url = _public_api_base_url(request)
            missing_oauth = _im_oauth_missing_env()
            self_binding_configured = _user_self_binding_configured()
            try:
                token_summary = user_token_store.summary()
                token_store_status = "ok"
                token_store_error = ""
            except Exception as exc:
                token_summary = {"total": 0, "active": 0, "revoked": 0, "expired": 0}
                token_store_status = "error"
                token_store_error = exc.__class__.__name__
            return {
                "status": "ready" if self_binding_configured or not missing_oauth else "pending_oauth_config",
                "api_base_url": base_url,
                "setup_url": f"{base_url}/auth/im/mcp/setup",
                "external_inputs_url": f"{base_url}/diagnose/external-inputs",
                "external_inputs_markdown_url": f"{base_url}/diagnose/external-inputs.md",
                "external_inputs_page_url": f"{base_url}/diagnose/external-inputs/page",
                "final_verification_url": f"{base_url}/diagnose/final-verification",
                "final_verification_page_url": f"{base_url}/diagnose/final-verification/page",
                "self_binding_page_url": f"{base_url}/auth/im/self-bindings/page",
                "token_binding_page_url": f"{base_url}/auth/im/token-bindings/page",
                "im_configure_url": f"{base_url}/auth/im/configure",
                "im_configure_page_url": f"{base_url}/auth/im/configure/page",
                "confirmations_url": f"{base_url}/auth/im/confirmations/page",
                "web_push_inbox_url": f"{base_url}/auth/im/confirmations/page",
                "current_user_demo_url": f"{base_url}/demo/current-user",
                "webhook_demo_url": f"{base_url}/demo/webhook",
                "current_user_smoke_url": f"{base_url}/auth/im/current-user/smoke",
                "oauth": {
                    "configured": not missing_oauth,
                    "missing_env": missing_oauth,
                    "login_url": f"{base_url}/auth/im/oauth/login?next=/auth/im/mcp/setup",
                    "callback_url": _im_oauth_redirect_uri_for_base(base_url),
                    "scope": os.getenv("CODEKB_IM_OAUTH_SCOPE", DEFAULT_IM_OAUTH_SCOPE),
                },
                "self_binding": {
                    "configured": self_binding_configured,
                    "page_url": f"{base_url}/auth/im/self-bindings/page",
                    "route_types": ["im_message", "im_robot", "im_userid", "manual"],
                },
                "mcp": {
                    "auth_token_argument": "auth_token",
                    "token_store_configured": bool(user_token_store_path),
                    "token_store_status": token_store_status,
                    "token_store_error": token_store_error,
                    "total_token_bindings": int(token_summary.get("total", 0) or 0),
                    "active_token_bindings": int(token_summary.get("active", 0) or 0),
                },
                "mcp_auth_strategy": {
                    "current_user_auth_required": True,
                    "setup_page_required": True,
                    "auth_token_argument": "auth_token",
                    "token_binding": "self_service_binding_or_im_oauth",
                    "confirmation_target": "current_authenticated_user",
                    "interface_person_lookup_enabled": False,
                    "static_mcp_token_production_allowed": False,
                },
                "secret_values_written": False,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"mcp setup status failed: {exc}") from exc

    @app.post("/auth/im/current-user/status")
    def auth_im_current_user_status(request: FastAPIRequest, payload: dict):
        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            binding = user_token_store.validate(auth_token)
            if binding is None:
                raise PermissionError("invalid auth token")
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"current user status failed: {exc}") from exc
        return {
            "status": "active",
            "binding": _public_token_binding(binding.to_dict()),
            "mcp": {
                "api_base_url": _public_api_base_url(request),
                "auth_token_argument": "auth_token",
                "token_store_configured": bool(user_token_store_path),
            },
        }

    @app.post("/auth/im/self-bindings")
    def auth_im_self_bindings(payload: dict):
        try:
            _verify_user_self_binding_code(str(payload.get("binding_code", "") or ""))
            metadata = _self_binding_metadata(payload)
            issued = user_token_store.issue(
                user_id_hash=_token_binding_user_hash(metadata),
                display_name=str(payload.get("display_name", "") or "").strip(),
                scopes=payload.get("scopes") or ["diagnose"],
                ttl_days=int(payload.get("ttl_days") or 30),
                metadata=metadata,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user self binding failed: {exc}") from exc
        return {
            "status": "issued",
            "token": issued["token"],
            "binding": _public_token_binding(issued["binding"]),
        }

    @app.get("/auth/im/self-bindings/page", response_class=HTMLResponse)
    def auth_im_self_bindings_page():
        return HTMLResponse(render_user_self_binding_page(), headers={"Cache-Control": "no-store"})

    @app.get("/demo/current-user", response_class=HTMLResponse)
    def current_user_demo_page():
        return HTMLResponse(render_current_user_demo_page(), headers={"Cache-Control": "no-store"})

    @app.get("/demo/webhook", response_class=HTMLResponse)
    def webhook_demo_page():
        return HTMLResponse(render_webhook_demo_page(), headers={"Cache-Control": "no-store"})

    @app.post("/auth/im/current-user/smoke")
    def auth_im_current_user_smoke(request: FastAPIRequest, payload: dict):
        from .current_user_smoke import (
            DEFAULT_CURRENT_USER_SMOKE_COMMENT,
            DEFAULT_CURRENT_USER_SMOKE_MESSAGE,
            DEFAULT_CURRENT_USER_SMOKE_QUERY,
            DEFAULT_CURRENT_USER_SMOKE_REASON,
            DEFAULT_CURRENT_USER_SMOKE_SUB_KBS,
            run_current_user_smoke,
        )

        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            sub_kbs = _parse_optional_string_list(payload.get("sub_kbs")) or list(DEFAULT_CURRENT_USER_SMOKE_SUB_KBS)
            report = run_current_user_smoke(
                auth_token=auth_token,
                token_store_path=user_token_store_path,
                confirmation_outbox_path=user_confirmation_outbox_path,
                confirmation_responses_path=user_confirmation_responses_path,
                query=str(payload.get("query", "") or "").strip() or DEFAULT_CURRENT_USER_SMOKE_QUERY,
                sub_kbs=sub_kbs,
                reason=str(payload.get("reason", "") or "").strip() or DEFAULT_CURRENT_USER_SMOKE_REASON,
                message=str(payload.get("message", "") or "").strip() or DEFAULT_CURRENT_USER_SMOKE_MESSAGE,
                respond=_parse_bool(payload.get("respond"), default=False),
                decision=str(payload.get("decision", "") or "").strip() or "confirmed",
                comment=str(payload.get("comment", "") or "").strip() or DEFAULT_CURRENT_USER_SMOKE_COMMENT,
                delivery_report_path=user_confirmation_report_path,
                delivery_log_path=user_confirmation_delivery_log_path,
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
                trace_log_path=os.getenv("CODEKB_TRACE_LOG", ""),
                retriever=os.getenv("CODEKB_RETRIEVER", "bm25-lite"),
                index_db_path=index_db_path or "",
                api_base_url=_public_api_base_url(request),
                top_k=_parse_top_k(payload.get("top_k")),
                include_governance=_parse_bool(payload.get("include_governance"), default=False),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"current user smoke failed: {exc}") from exc
        return report

    @app.post("/auth/im/token-bindings")
    def auth_im_token_bindings(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            metadata = dict(payload.get("metadata") or {})
            issued = user_token_store.issue(
                user_id_hash=str(payload.get("user_id_hash", "") or "").strip() or _token_binding_user_hash(metadata),
                display_name=str(payload.get("display_name", "") or "").strip(),
                scopes=payload.get("scopes") or [],
                ttl_days=int(payload.get("ttl_days") or 30),
                metadata=metadata,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user token binding failed: {exc}") from exc
        return {
            "status": "issued",
            "token": issued["token"],
            "binding": _public_token_binding(issued["binding"]),
        }

    @app.get("/auth/im/token-bindings/page", response_class=HTMLResponse)
    def auth_im_token_bindings_page():
        return HTMLResponse(render_user_token_binding_page(), headers={"Cache-Control": "no-store"})

    @app.get("/auth/im/token-bindings/summary")
    def auth_im_token_bindings_summary(x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return user_token_store.summary()
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user token summary failed: {exc}") from exc

    @app.post("/auth/im/token-bindings/{token_id}/revoke")
    def auth_im_token_revoke(token_id: str, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            revoked = user_token_store.revoke(token_id)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user token revoke failed: {exc}") from exc
        return {"status": "revoked", "binding": _public_token_binding(revoked.to_dict())}

    @app.post("/auth/im/configure")
    def auth_im_configure(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            report = configure_im_env(
                env_file=p5_env_file_path,
                env={},
                apply=_parse_bool(payload.get("apply"), default=False),
                confirm_real_send=_parse_bool(payload.get("confirm_real_send"), default=False),
                enable_send=_parse_bool(payload.get("enable_send"), default=False),
                values={
                    "corp_id": payload.get("corp_id", ""),
                    "agent_id": payload.get("agent_id", ""),
                    "app_secret": payload.get("app_secret", ""),
                    "oauth_state_secret": payload.get("oauth_state_secret", ""),
                    "redirect_uri": payload.get("redirect_uri", ""),
                    "confirm_url_base": payload.get("confirm_url_base", ""),
                    "CODEKB_IM_CORP_ID": payload.get("CODEKB_IM_CORP_ID", ""),
                    "CODEKB_IM_AGENT_ID": payload.get("CODEKB_IM_AGENT_ID", ""),
                    "CODEKB_IM_APP_SECRET": payload.get("CODEKB_IM_APP_SECRET", ""),
                    "CODEKB_IM_OAUTH_STATE_SECRET": payload.get(
                        "CODEKB_IM_OAUTH_STATE_SECRET",
                        "",
                    ),
                    "CODEKB_IM_OAUTH_REDIRECT_URI": payload.get(
                        "CODEKB_IM_OAUTH_REDIRECT_URI",
                        "",
                    ),
                    "CODEKB_IM_CONFIRM_URL_BASE": payload.get(
                        "CODEKB_IM_CONFIRM_URL_BASE",
                        "",
                    ),
                },
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"IM configuration failed: {exc}") from exc
        return report

    @app.get("/auth/im/configure/page", response_class=HTMLResponse)
    def auth_im_configure_page():
        return HTMLResponse(render_im_config_page(), headers={"Cache-Control": "no-store"})

    @app.get("/auth/im/confirmations/page", response_class=HTMLResponse)
    def auth_im_confirmation_page(confirmation_id: str = ""):
        return HTMLResponse(render_user_confirmation_page(confirmation_id=confirmation_id))

    @app.post("/auth/im/confirmations/pending")
    def auth_im_confirmations_pending(payload: dict):
        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            confirmations = list_user_confirmations(
                user_confirmation_outbox_path,
                responses_path=user_confirmation_responses_path,
                user_token=auth_token,
                limit=int(payload.get("limit") or 50),
                include_responded=_parse_bool(payload.get("include_responded"), default=False),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user confirmations pending failed: {exc}") from exc
        return {"status": "ok", "total": len(confirmations), "confirmations": list(confirmations)}

    @app.post("/auth/im/confirmations/request")
    def auth_im_confirmation_request(payload: dict):
        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            request = JsonlUserConfirmationOutbox(user_confirmation_outbox_path).append(
                user_token=auth_token,
                reason=str(payload.get("reason", "") or "").strip(),
                message=str(payload.get("message", "") or "").strip(),
                payload=dict(payload.get("payload") or {}),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user confirmation request failed: {exc}") from exc
        return {"status": "queued", "confirmation": public_confirmation_request(request)}

    @app.post("/auth/im/confirmations/{confirmation_id}/detail")
    def auth_im_confirmation_detail(confirmation_id: str, payload: dict):
        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            confirmation = get_user_confirmation_detail(
                user_confirmation_outbox_path,
                responses_path=user_confirmation_responses_path,
                user_token=auth_token,
                confirmation_id=confirmation_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user confirmation detail failed: {exc}") from exc
        return {"status": "ok", "confirmation": confirmation}

    @app.post("/auth/im/confirmations/{confirmation_id}/response")
    def auth_im_confirmation_response(confirmation_id: str, payload: dict):
        try:
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            response = user_confirmation_response_store.record(
                outbox_path=user_confirmation_outbox_path,
                user_token=auth_token,
                confirmation_id=confirmation_id,
                decision=str(payload.get("decision", "") or "").strip(),
                comment=str(payload.get("comment", "") or "").strip(),
                metadata=dict(payload.get("metadata") or {}),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user confirmation response failed: {exc}") from exc
        return {"status": "recorded", "response": public_confirmation_response(response)}

    @app.get("/auth/im/confirmations/responses/summary")
    def auth_im_confirmation_responses_summary(limit: int = 50, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return user_confirmation_response_store.summary(limit=limit)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"user confirmation responses summary failed: {exc}") from exc

    @app.get("/governance/policy")
    def governance_policy():
        try:
            return load_governance_policy(governance_policy_path).to_dict()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"governance policy failed: {exc}") from exc

    @app.post("/ask")
    def ask(payload: dict):
        query = str(payload.get("query", "")).strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        try:
            sub_kbs = _parse_sub_kbs(payload.get("sub_kbs"))
            top_k = _parse_top_k(payload.get("top_k"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return answer_result_to_dict(service.ask(query, sub_kbs=sub_kbs, top_k=top_k))

    def _code_int(payload: dict, key: str, default: int) -> int:
        raw = payload.get(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{key} must be an integer") from exc
        if value < 1:
            raise HTTPException(status_code=400, detail=f"{key} must be >= 1")
        return value

    @app.post("/code/search")
    def code_search(payload: dict):
        query = str(payload.get("query", "")).strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        try:
            sub_kbs = _parse_sub_kbs(payload.get("sub_kbs"))
            top_k = _parse_top_k(payload.get("top_k")) if payload.get("top_k") is not None else 6
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return search_code(service.retriever, query, sub_kbs=sub_kbs, top_k=top_k)

    @app.post("/code/symbol")
    def code_symbol(payload: dict):
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        try:
            top_k = _parse_top_k(payload.get("top_k")) if payload.get("top_k") is not None else 8
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_symbol(service.retriever, name, top_k=top_k)

    @app.post("/code/read")
    def code_read(payload: dict):
        path = str(payload.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        return read_file_range(service.store, path, _code_int(payload, "start_line", 1), _code_int(payload, "end_line", 1))

    @app.post("/code/outline")
    def code_outline(payload: dict):
        path = str(payload.get("path", "")).strip()
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        return file_outline(service.store, path)

    @app.post("/code/files")
    def code_files(payload: dict):
        pattern = str(payload.get("pattern", "")).strip()
        if not pattern:
            raise HTTPException(status_code=400, detail="pattern is required")
        try:
            sub_kbs = _parse_sub_kbs(payload.get("sub_kbs"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        limit = max(1, min(int(payload.get("limit") or 50), 200))
        return find_files(service.store, pattern, sub_kbs=sub_kbs, limit=limit)

    @app.post("/code/dir")
    def code_dir(payload: dict):
        prefix = str(payload.get("prefix", "") or "").strip()
        try:
            sub_kbs = _parse_sub_kbs(payload.get("sub_kbs"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return list_dir(service.store, prefix, sub_kbs=sub_kbs)

    @app.post("/diagnose")
    def diagnose(payload: dict):
        try:
            policy = confirmation_policy(payload)
            auth_token = str(payload.get("auth_token", "") or "").strip()
            if policy != "never" and not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            result = _run_diagnosis(
                payload,
                service=service,
                fixture_path=fixture_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
            )
            response_payload = result.to_dict()
            confirmation = maybe_append_diagnosis_confirmation(
                result,
                payload,
                confirmation_policy=policy,
                user_token=auth_token,
                confirmation_outbox_path=user_confirmation_outbox_path,
            )
            if confirmation is not None:
                response_payload["confirmation"] = public_confirmation_request(confirmation)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis failed: {exc}") from exc
        return response_payload

    @app.post("/diagnose/gap-candidate")
    def diagnose_gap_candidate(payload: dict):
        try:
            result = _run_diagnosis(
                payload,
                service=service,
                fixture_path=fixture_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
            )
            submission = submit_diagnostic_gap(
                result,
                candidate_store,
                submitted_by_hash=str(payload.get("submitted_by_hash") or payload.get("user_id_hash") or "").strip(),
                allow_duplicate=_parse_bool(payload.get("allow_duplicate"), default=False),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis gap submit failed: {exc}") from exc
        return {
            "status": "duplicate" if submission.duplicate else "accepted",
            "diagnosis": result.to_dict(),
            "submission": submission.to_dict(),
        }

    @app.get("/diagnose/gaps/summary")
    def diagnose_gaps_summary(status: str = "", limit: int = 20):
        if limit < 0 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 500")
        try:
            return summarize_diagnostic_gaps(
                candidate_store,
                status=status.strip() or None,
                limit=limit,
            ).to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis gap summary failed: {exc}") from exc

    @app.get("/diagnose/readiness")
    def diagnose_readiness(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            return build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis readiness failed: {exc}") from exc

    @app.get("/diagnose/acceptance")
    def diagnose_acceptance(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            return build_p5_acceptance_report(readiness, api_base_url=base_url)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis acceptance failed: {exc}") from exc

    @app.get("/diagnose/external-inputs")
    def diagnose_external_inputs(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            external_state = build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
            return build_p5_external_input_plan(
                readiness,
                api_base_url=base_url,
                env_file=p5_env_file_path,
                external_state_report=external_state,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis external inputs failed: {exc}") from exc

    @app.get("/diagnose/external-inputs/page", response_class=HTMLResponse)
    def diagnose_external_inputs_page(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            external_state = build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
            plan = build_p5_external_input_plan(
                readiness,
                api_base_url=base_url,
                env_file=p5_env_file_path,
                external_state_report=external_state,
            )
            return HTMLResponse(render_p5_external_input_page(plan), headers={"Cache-Control": "no-store"})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis external inputs page failed: {exc}") from exc

    @app.get("/diagnose/external-inputs.md", response_class=PlainTextResponse)
    def diagnose_external_inputs_markdown(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            external_state = build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
            plan = build_p5_external_input_plan(
                readiness,
                api_base_url=base_url,
                env_file=p5_env_file_path,
                external_state_report=external_state,
            )
            return PlainTextResponse(
                render_p5_external_input_plan_markdown(plan),
                media_type="text/markdown; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis external inputs markdown failed: {exc}") from exc

    @app.get("/diagnose/final-verification")
    def diagnose_final_verification(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            external_state = build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
            return build_p5_final_verification_guide(
                readiness,
                external_state,
                api_base_url=base_url,
                env_file=p5_env_file_path,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis final verification failed: {exc}") from exc

    @app.get("/diagnose/final-verification/page", response_class=HTMLResponse)
    def diagnose_final_verification_page(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            readiness = build_p5_readiness_report(
                fixture_path=fixture_path,
                aliases_path=aliases_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                index_db_path=index_db_path or "",
                diagnose_webhook_mapping_path=diagnose_webhook_mapping_path,
                diagnose_webhook_samples_path=diagnose_webhook_samples_path,
                diagnose_webhook_log_path=diagnose_webhook_log_path,
                user_token_store_path=user_token_store_path,
                user_confirmation_outbox_path=user_confirmation_outbox_path,
                user_confirmation_responses_path=user_confirmation_responses_path,
                api_base_url=base_url,
            )
            external_state = build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
            guide = build_p5_final_verification_guide(
                readiness,
                external_state,
                api_base_url=base_url,
                env_file=p5_env_file_path,
            )
            return HTMLResponse(render_p5_final_verification_page(guide), headers={"Cache-Control": "no-store"})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis final verification page failed: {exc}") from exc

    @app.get("/diagnose/external-state")
    def diagnose_external_state():
        try:
            return build_p5_external_state(
                env_file=p5_env_file_path,
                im_template=p5_im_template_path,
                token_store=user_token_store_path,
                real_samples=p5_real_samples_path,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis external state failed: {exc}") from exc

    @app.get("/diagnose/integrations")
    def diagnose_integrations(request: FastAPIRequest):
        try:
            base_url = os.getenv("CODEKB_API_BASE_URL", "").strip() or str(request.base_url).rstrip("/")
            return diagnose_integration_artifacts(api_base_url=base_url)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis integrations failed: {exc}") from exc

    @app.get("/diagnose/webhook/events")
    def diagnose_webhook_events(source: str = "", status: str = "", action: str = "", limit: int = 20):
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        try:
            return diagnose_webhook_store.summary(
                source=source.strip() or None,
                status=status.strip() or None,
                action=action.strip() or None,
                limit=limit,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook events failed: {exc}") from exc

    @app.get("/diagnose/webhook/sample-suite")
    def diagnose_webhook_sample_suite():
        try:
            return validate_diagnostic_webhook_sample_suite(
                diagnose_webhook_samples_path,
                mapping_path=diagnose_webhook_mapping_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook sample suite failed: {exc}") from exc

    @app.get("/diagnose/webhook/{source}/mapping")
    def diagnose_webhook_mapping(source: str):
        try:
            return effective_diagnostic_webhook_mapping(source, diagnose_webhook_mapping_path).to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook mapping failed: {exc}") from exc

    @app.post("/diagnose/webhook/{source}/sample-import")
    def diagnose_webhook_sample_import(source: str, payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            raw_payload = payload.get("payload")
            if not isinstance(raw_payload, dict):
                raise ValueError("payload must contain a webhook payload object")
            output_path = _diagnose_webhook_real_samples_path(diagnose_webhook_samples_path)
            result = import_diagnostic_webhook_sample(
                source=source,
                name=str(payload.get("name", "") or "").strip(),
                payload=raw_payload,
                output_path=output_path,
                mapping_path=diagnose_webhook_mapping_path,
                append=_parse_bool(payload.get("append"), default=True),
                expected_context=dict(payload.get("expected_context") or {}),
                expected_sub_kbs=_parse_optional_string_list(payload.get("expected_sub_kbs")),
                forbidden_values=_parse_optional_string_list(payload.get("forbidden_values")) or [],
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook sample import failed: {exc}") from exc
        output_is_active = _same_normalized_path(str(output_path), diagnose_webhook_samples_path)
        return {
            **result,
            "active_samples_path": diagnose_webhook_samples_path,
            "output_is_active": output_is_active,
            "next_steps": _diagnose_webhook_sample_import_next_steps(str(output_path), output_is_active),
        }

    @app.post("/diagnose/webhook/{source}/normalize")
    def diagnose_webhook_normalize(source: str, payload: dict, request: FastAPIRequest = None, x_codekb_token: str = Header(default="")):
        try:
            _verify_diagnose_webhook_token(x_codekb_token)
            _verify_diagnose_webhook_signature(source, request)
            preview = preview_diagnostic_webhook(source, payload, mapping_path=diagnose_webhook_mapping_path)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook normalize failed: {exc}") from exc
        return {"status": "normalized", **preview}

    @app.post("/diagnose/webhook/{source}/validate")
    def diagnose_webhook_validate(source: str, payload: dict, request: FastAPIRequest = None, x_codekb_token: str = Header(default="")):
        try:
            _verify_diagnose_webhook_token(x_codekb_token)
            _verify_diagnose_webhook_signature(source, request)
            report = validate_diagnostic_webhook(source, payload, mapping_path=diagnose_webhook_mapping_path)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"diagnosis webhook validate failed: {exc}") from exc
        return {"status": "validated", **report}

    @app.post("/diagnose/webhook/{source}")
    def diagnose_webhook(
        source: str,
        payload: dict,
        request: FastAPIRequest = None,
        x_codekb_token: str = Header(default=""),
    ):
        normalized = None
        try:
            _verify_diagnose_webhook_token(x_codekb_token)
            _verify_diagnose_webhook_signature(source, request)
            normalized = normalize_diagnostic_webhook(source, payload, mapping_path=diagnose_webhook_mapping_path)
            confirmation_args = _diagnose_webhook_confirmation_args(payload)
            policy = confirmation_policy(confirmation_args)
            auth_token = str(confirmation_args.get("auth_token", "") or "").strip()
            if policy != "never" and not user_token_store.validate(auth_token):
                raise PermissionError("invalid auth token")
            result = _run_diagnosis(
                normalized,
                service=service,
                fixture_path=fixture_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
            )
            event = diagnose_webhook_store.append(
                source=source,
                action="diagnose",
                status="diagnosed",
                diagnosis=result,
            )
            confirmation = maybe_append_diagnosis_confirmation(
                result,
                confirmation_args,
                confirmation_policy=policy,
                user_token=auth_token,
                confirmation_outbox_path=user_confirmation_outbox_path,
            )
        except PermissionError as exc:
            _append_diagnose_webhook_failure(diagnose_webhook_store, source, "diagnose", "unauthorized", exc, normalized)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            _append_diagnose_webhook_failure(diagnose_webhook_store, source, "diagnose", "bad_request", exc, normalized)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            _append_diagnose_webhook_failure(diagnose_webhook_store, source, "diagnose", "error", exc, normalized)
            raise HTTPException(status_code=500, detail=f"diagnosis webhook failed: {exc}") from exc
        response_payload = {
            "status": "diagnosed",
            "source": source,
            "diagnosis": result.to_dict(),
            "normalized": _diagnostic_webhook_response(result),
            "event": event.to_dict(),
        }
        if confirmation is not None:
            response_payload["confirmation"] = public_confirmation_request(confirmation)
        return response_payload

    @app.post("/diagnose/webhook/{source}/gap-candidate")
    def diagnose_webhook_gap_candidate(source: str, payload: dict, request: FastAPIRequest = None, x_codekb_token: str = Header(default="")):
        normalized = None
        try:
            _verify_diagnose_webhook_token(x_codekb_token)
            _verify_diagnose_webhook_signature(source, request)
            normalized = normalize_diagnostic_webhook(source, payload, mapping_path=diagnose_webhook_mapping_path)
            result = _run_diagnosis(
                normalized,
                service=service,
                fixture_path=fixture_path,
                registry_path=registry_path,
                governance_policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
            )
            submission = submit_diagnostic_gap(
                result,
                candidate_store,
                submitted_by_hash=str(normalized.get("submitted_by_hash", "") or "").strip(),
                allow_duplicate=_parse_bool(normalized.get("allow_duplicate"), default=False),
            )
            event = diagnose_webhook_store.append(
                source=source,
                action="gap_candidate",
                status="duplicate" if submission.duplicate else "accepted",
                diagnosis=result,
                submission=submission,
            )
        except PermissionError as exc:
            _append_diagnose_webhook_failure(
                diagnose_webhook_store,
                source,
                "gap_candidate",
                "unauthorized",
                exc,
                normalized,
            )
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            _append_diagnose_webhook_failure(
                diagnose_webhook_store,
                source,
                "gap_candidate",
                "bad_request",
                exc,
                normalized,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            _append_diagnose_webhook_failure(diagnose_webhook_store, source, "gap_candidate", "error", exc, normalized)
            raise HTTPException(status_code=500, detail=f"diagnosis webhook gap submit failed: {exc}") from exc
        return {
            "status": "duplicate" if submission.duplicate else "accepted",
            "source": source,
            "diagnosis": result.to_dict(),
            "normalized": _diagnostic_webhook_response(result),
            "submission": submission.to_dict(),
            "event": event.to_dict(),
        }

    @app.post("/feedback")
    def feedback(payload: dict):
        try:
            parsed = parse_feedback_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record = feedback_store.append(**parsed)
        return {"status": "accepted", "feedback_id": record.feedback_id}

    @app.get("/feedback/summary")
    def feedback_summary(limit: int = 20):
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        return summarize_feedback(feedback_log_path, badcase_limit=limit).to_dict()

    @app.get("/usage/summary")
    def usage_summary(limit: int = 50):
        if limit < 0 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 500")
        return summarize_usage(limit_recent=limit)

    @app.get("/feedback/badcases")
    def feedback_badcases(limit: int = 50):
        if limit < 0 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 500")
        return {"badcases": summarize_feedback(feedback_log_path, badcase_limit=limit).to_dict()["badcases"]}

    @app.get("/governance/report")
    def governance_report(stale_after_days: int = 180, candidate_sla_days: int = 3, limit: int = 100):
        if stale_after_days < 0 or stale_after_days > 3650:
            raise HTTPException(status_code=400, detail="stale_after_days must be between 0 and 3650")
        if candidate_sla_days < 0 or candidate_sla_days > 365:
            raise HTTPException(status_code=400, detail="candidate_sla_days must be between 0 and 365")
        if limit < 0 or limit > 500:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 500")
        try:
            report = build_governance_report(
                [fixture_path],
                registry_path=registry_path,
                policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
                stale_after_days=stale_after_days,
                candidate_sla_days=candidate_sla_days,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"governance report failed: {exc}") from exc
        return report.to_dict(item_limit=limit)

    @app.get("/governance/state")
    def governance_state():
        try:
            return summarize_governance_state(governance_state_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"governance state failed: {exc}") from exc

    @app.get("/governance/weekly-report")
    def governance_weekly_report(stale_after_days: int = 180, candidate_sla_days: int = 3, item_limit: int = 20):
        if stale_after_days < 0 or stale_after_days > 3650:
            raise HTTPException(status_code=400, detail="stale_after_days must be between 0 and 3650")
        if candidate_sla_days < 0 or candidate_sla_days > 365:
            raise HTTPException(status_code=400, detail="candidate_sla_days must be between 0 and 365")
        if item_limit < 0 or item_limit > 200:
            raise HTTPException(status_code=400, detail="item_limit must be between 0 and 200")
        try:
            report = build_governance_report(
                [fixture_path],
                registry_path=registry_path,
                policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
                stale_after_days=stale_after_days,
                candidate_sla_days=candidate_sla_days,
            )
            state_summary = summarize_governance_state(governance_state_path)
            weekly = build_curator_weekly_report(report, state_summary=state_summary, item_limit=item_limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"governance weekly report failed: {exc}") from exc
        return weekly.to_dict()

    @app.get("/governance/tickets/plan")
    def governance_tickets_plan(
        target: str = "manual",
        min_severity: str = "P1",
        stale_after_days: int = 180,
        candidate_sla_days: int = 3,
        limit: int = 50,
    ):
        if target not in {"manual", "issue_tracker", "git"}:
            raise HTTPException(status_code=400, detail="target must be manual, issue_tracker, or git")
        if stale_after_days < 0 or stale_after_days > 3650:
            raise HTTPException(status_code=400, detail="stale_after_days must be between 0 and 3650")
        if candidate_sla_days < 0 or candidate_sla_days > 365:
            raise HTTPException(status_code=400, detail="candidate_sla_days must be between 0 and 365")
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        try:
            report = build_governance_report(
                [fixture_path],
                registry_path=registry_path,
                policy_path=governance_policy_path,
                feedback_log_path=feedback_log_path,
                candidate_store_path=candidate_store_path,
                pending_docs_dir=pending_docs_dir,
                stale_after_days=stale_after_days,
                candidate_sla_days=candidate_sla_days,
            )
            plans = build_governance_ticket_plans(
                report,
                target=target,
                min_severity=min_severity,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"governance ticket plan failed: {exc}") from exc
        return {"plans": [plan.to_dict() for plan in plans], "dry_run": True}

    @app.post("/ingest")
    def ingest(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _maybe_require_audit_admin(x_codekb_admin_token)
            parsed = parse_ingest_payload(payload)
            submission = candidate_store.submit(**parsed)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "duplicate" if submission.duplicate else "accepted",
            "candidate_id": submission.candidate.candidate_id,
            "duplicate": submission.duplicate,
            "existing_candidate_id": submission.existing_candidate_id,
            "candidate": submission.candidate.to_dict(),
        }

    @app.get("/ingest/candidates")
    def ingest_candidates(status: str | None = None, limit: int = 50):
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        return {"candidates": [candidate.to_dict() for candidate in candidate_store.list(status=status, limit=limit)]}

    @app.get("/ingest/candidates/{candidate_id}")
    def ingest_candidate_detail(candidate_id: str):
        try:
            candidate = candidate_store.get(candidate_id)
            audits = candidate_store.audits(candidate_id=candidate_id, limit=100)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "candidate": candidate.to_dict(),
            "audits": [audit.to_dict() for audit in audits],
        }

    @app.post("/ingest/candidates/{candidate_id}/revision")
    def ingest_candidate_revision(candidate_id: str, payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _maybe_require_audit_admin(x_codekb_admin_token)
            parsed = parse_revision_payload(payload)
            result = candidate_store.revise(candidate_id, **parsed)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": result.candidate.status,
            "candidate_id": result.candidate.candidate_id,
            "candidate": result.candidate.to_dict(),
            "audit": result.audit.to_dict(),
        }

    @app.get("/audit/page", response_class=HTMLResponse)
    def audit_page():
        return HTMLResponse(render_audit_page(), headers={"Cache-Control": "no-store"})

    @app.get("/audit/queue")
    def audit_queue(status: str = "pending_review", limit: int = 50):
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        return {"candidates": [candidate.to_dict() for candidate in candidate_store.list(status=status, limit=limit)]}

    @app.post("/audit/{candidate_id}")
    def audit(candidate_id: str, payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _maybe_require_audit_admin(x_codekb_admin_token)
            parsed = parse_audit_payload(payload)
            result = candidate_store.audit(candidate_id, **parsed)
            index_rebuild_report = _maybe_rebuild_index_after_audit(result, payload)
            storage_sync_report = _maybe_sync_external_storage_after_audit(result, payload, index_rebuild_report)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": result.candidate.status,
            "candidate_id": result.candidate.candidate_id,
            "candidate": result.candidate.to_dict(),
            "audit": result.audit.to_dict(),
            "index_rebuild": index_rebuild_report,
            "storage_sync": storage_sync_report,
        }

    @app.get("/publish/plan")
    def publish_plan(
        mode: str = "manual",
        limit: int = 50,
        target_parentid: str = "",
        template_docid: str = "",
        index_docid: str = "",
    ):
        if limit < 0 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 0 and 200")
        try:
            plans = build_publish_plans(
                pending_docs_dir,
                mode=mode,
                target_parentid=target_parentid,
                template_docid=template_docid,
                index_docid=index_docid,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"plans": [plan.to_dict() for plan in plans], "dry_run": True}

    @app.get("/publish/readiness")
    def publish_readiness(x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return build_publish_readiness(
                pending_docs_dir,
                publish_outbox_path,
                publish_report_path,
                ledger_path=publish_ledger_path,
                env=dict(os.environ),
                client_configured=False,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"publish readiness failed: {exc}") from exc

    @app.post("/publish/configure")
    def publish_configure(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return configure_publish_env(
                env_file=p5_env_file_path,
                apply=_parse_bool(payload.get("apply"), default=False),
                env=os.environ,
                values={
                    "mode": payload.get("mode", ""),
                    "index_docid": payload.get("index_docid", ""),
                    "template_docid": payload.get("template_docid", ""),
                    "target_parentid": payload.get("target_parentid", ""),
                    "CODEKB_PUBLISH_MODE": payload.get("CODEKB_PUBLISH_MODE", ""),
                    "CODEKB_PUBLISH_INDEX_DOCID": payload.get("CODEKB_PUBLISH_INDEX_DOCID", ""),
                    "CODEKB_PUBLISH_TEMPLATE_DOCID": payload.get("CODEKB_PUBLISH_TEMPLATE_DOCID", ""),
                    "CODEKB_PUBLISH_TARGET_PARENTID": payload.get("CODEKB_PUBLISH_TARGET_PARENTID", ""),
                },
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"publish configuration failed: {exc}") from exc

    @app.post("/publish/outbox/plan")
    def publish_outbox_plan(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            return plan_publish_outbox(pending_docs_dir, publish_outbox_path, payload, env=dict(os.environ))
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"publish outbox planning failed: {exc}") from exc

    @app.post("/publish/outbox/process")
    def publish_outbox_process(payload: dict, x_codekb_admin_token: str = Header(default="")):
        try:
            _verify_required_auth_admin_token(x_codekb_admin_token)
            write_enabled = os.getenv("CODEKB_ENABLE_WIKI_WRITE", "").strip() == "1"
            publish_client = _maybe_build_wiki_publish_client(payload, write_enabled)
            return process_publish_outbox_report(
                publish_outbox_path,
                publish_report_path,
                payload,
                ledger_path=publish_ledger_path,
                write_enabled=write_enabled,
                client=publish_client,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"publish outbox processing failed: {exc}") from exc

    return app


def registry_to_dict(registry) -> dict:
    return {
        "version": registry.version,
        "updated_at": registry.updated_at,
        "status": registry.status,
        "defaults": {
            "dense_top_k": registry.defaults.dense_top_k,
            "sparse_top_k": registry.defaults.sparse_top_k,
            "rrf_top_k": registry.defaults.rrf_top_k,
            "rerank_top_k": registry.defaults.rerank_top_k,
            "max_atoms": registry.defaults.max_atoms,
            "max_atom_tokens": registry.defaults.max_atom_tokens,
            "contextual_prefix_tokens": registry.defaults.contextual_prefix_tokens,
            "citation_required": registry.defaults.citation_required,
            "refuse_without_citation": registry.defaults.refuse_without_citation,
            "layers_for_production_answer": list(registry.defaults.layers_for_production_answer),
        },
        "sub_kbs": [
            {
                "id": sub_kb.id,
                "name": sub_kb.name,
                "owner_group": sub_kb.owner_group,
                "status": sub_kb.status,
                "description": sub_kb.description,
                "source_docs": [
                    {
                        "system": source.system,
                        "docid": source.docid,
                        "title": source.title,
                        "mode": source.mode,
                        "priority": source.priority,
                    }
                    for source in sub_kb.source_docs
                ],
            }
            for sub_kb in registry.sub_kbs
        ],
    }


def _registry_owner_groups(registry_path: str) -> dict[str, str]:
    registry = load_registry(registry_path)
    return {sub_kb.id: sub_kb.owner_group for sub_kb in registry.sub_kbs}


def _verify_diagnose_webhook_token(provided_token: str) -> None:
    expected_token = os.getenv("CODEKB_DIAGNOSE_WEBHOOK_TOKEN", "").strip()
    if not expected_token:
        raise PermissionError("diagnose webhook token is not configured")
    if not hmac.compare_digest(str(provided_token or "").strip(), expected_token):
        raise PermissionError("invalid diagnose webhook token")


def _wiki_publish_client_factory():
    """构造真正的 Wiki 发布客户端;测试里可打桩替换。"""
    return HttpWikiPublishClient.from_env()


def _maybe_build_wiki_publish_client(payload: dict, write_enabled: bool):
    """仅在真正要执行写入时才构造 Wiki 发布客户端。

    写开关关闭、或请求不是 execute 时返回 None,这样发布流程默认仍保持原有的
    ``blocked_*`` 行为。
    """

    if not write_enabled:
        return None
    execute = _parse_bool(payload.get("execute"), default=False)
    if not execute:
        return None
    return _wiki_publish_client_factory()


def _verify_diagnose_webhook_signature(source: str, request) -> str:
    """校验 webhook 请求按来源配置的可选 HMAC 签名。

    没配签名密钥或缺签名头时返回 ``'unconfigured'``(等于不校验),这样可以逐步开启
    签名强制而不影响现有行为。配了密钥但签名对不上则抛出 ``PermissionError``。
    """

    if request is None:
        return "unconfigured"
    raw_body = getattr(request, "_body", b"")
    if not isinstance(raw_body, (bytes, bytearray)):
        raw_body = b""
    headers = getattr(request, "headers", None)
    return verify_webhook_signature(source, bytes(raw_body or b""), headers)


def _verify_required_auth_admin_token(provided_token: str) -> None:
    expected_token = os.getenv("CODEKB_AUTH_ADMIN_TOKEN", "").strip()
    if not expected_token:
        raise PermissionError("auth admin token is not configured")
    if not hmac.compare_digest(str(provided_token or "").strip(), expected_token):
        raise PermissionError("invalid auth admin token")


def _maybe_require_audit_admin(token: str) -> None:
    """两个条件同时满足时,审核类写接口才需要 admin token 鉴权。

    只有开关打开(默认 ``true``)且确实配置了 admin token 时才生效。这样没配 admin token
    (比如 CI 环境)时,写接口仍保持以前免鉴权的行为;运维只要配上
    ``CODEKB_AUTH_ADMIN_TOKEN`` 就能把这些接口锁起来。
    """

    switch_on = os.getenv("CODEKB_AUDIT_WRITE_REQUIRE_ADMIN", "true").strip().lower() in {"1", "true", "yes"}
    if switch_on and os.getenv("CODEKB_AUTH_ADMIN_TOKEN", "").strip():
        _verify_required_auth_admin_token(token)


def _user_self_binding_configured() -> bool:
    return bool(os.getenv("CODEKB_USER_BINDING_CODE", "").strip())


def _verify_user_self_binding_code(provided_code: str) -> None:
    expected_code = os.getenv("CODEKB_USER_BINDING_CODE", "").strip()
    if not expected_code:
        raise RuntimeError("CODEKB_USER_BINDING_CODE is not configured")
    if str(provided_code or "").strip() != expected_code:
        raise PermissionError("invalid user binding code")


def _diagnose_webhook_real_samples_path(active_samples_path: str) -> str:
    configured = os.getenv("CODEKB_DIAGNOSE_WEBHOOK_REAL_SAMPLES", "").strip()
    if configured:
        return configured
    normalized = str(active_samples_path or "").replace("\\", "/")
    if normalized and normalized != DEFAULT_WEBHOOK_SAMPLE_SUITE_PATH and not normalized.endswith(
        "/diagnose-webhook-samples.draft.yaml"
    ):
        return active_samples_path
    return "/data/codekb/state/diagnose-webhook-samples.real.yaml"


def _same_normalized_path(left: str, right: str) -> bool:
    return str(left or "").replace("\\", "/") == str(right or "").replace("\\", "/")


def _diagnose_webhook_sample_import_next_steps(output_path: str, output_is_active: bool) -> list[str]:
    steps = ["Run GET /diagnose/webhook/sample-suite to validate the active sample suite."]
    if not output_is_active:
        steps.insert(0, f"Set CODEKB_DIAGNOSE_WEBHOOK_SAMPLES={output_path} and restart the API.")
    steps.append("Run GET /diagnose/readiness and confirm external_platform_samples is no longer required.")
    return steps


def _parse_optional_string_list(value: object) -> list[str] | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("expected a string list")


def _build_im_oauth_client() -> IMOAuthClient:
    client = IMOAuthClient(
        corp_id=os.getenv("CODEKB_IM_CORP_ID", ""),
        app_secret=os.getenv("CODEKB_IM_APP_SECRET", ""),
        agent_id=os.getenv("CODEKB_IM_AGENT_ID", ""),
        api_base=os.getenv("CODEKB_IM_API_BASE", DEFAULT_IM_API_BASE),
        authorize_base=os.getenv("CODEKB_IM_OAUTH_AUTHORIZE_BASE", DEFAULT_IM_OAUTH_AUTHORIZE_BASE),
    )
    if not client.configured():
        raise RuntimeError("IM OAuth client is not configured")
    return client


def _im_oauth_redirect_uri(request) -> str:
    configured = os.getenv("CODEKB_IM_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    base_url = os.getenv("CODEKB_API_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return _im_oauth_redirect_uri_for_base(base_url)
    return str(request.url_for("auth_im_oauth_callback"))


def _im_oauth_redirect_uri_for_base(base_url: str) -> str:
    configured = os.getenv("CODEKB_IM_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return f"{str(base_url or '').rstrip('/')}/auth/im/oauth/callback"


def _public_api_base_url(request) -> str:
    return os.getenv("CODEKB_API_BASE_URL", "").strip().rstrip("/") or str(request.base_url).rstrip("/")


def _im_oauth_state_secret() -> str:
    secret = os.getenv("CODEKB_IM_OAUTH_STATE_SECRET", "").strip()
    if not secret:
        raise RuntimeError("CODEKB_IM_OAUTH_STATE_SECRET is required")
    return secret


def _im_oauth_ttl_days() -> int:
    raw = os.getenv("CODEKB_IM_OAUTH_TOKEN_TTL_DAYS", "30")
    try:
        ttl_days = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CODEKB_IM_OAUTH_TOKEN_TTL_DAYS must be an integer") from exc
    if ttl_days < 1 or ttl_days > 366:
        raise RuntimeError("CODEKB_IM_OAUTH_TOKEN_TTL_DAYS must be between 1 and 366")
    return ttl_days


def _im_oauth_env_configured() -> bool:
    return not _im_oauth_missing_env()


def _im_oauth_missing_env() -> list[str]:
    required = (
        "CODEKB_IM_CORP_ID",
        "CODEKB_IM_AGENT_ID",
        "CODEKB_IM_APP_SECRET",
        "CODEKB_IM_OAUTH_STATE_SECRET",
    )
    return [key for key in required if not os.getenv(key, "").strip()]


def _public_token_binding(binding: dict) -> dict:
    token_hash = str(binding.get("token_hash", "") or "")
    return {
        "token_id": binding.get("token_id", ""),
        "created_at": binding.get("created_at", ""),
        "expires_at": binding.get("expires_at", ""),
        "revoked_at": binding.get("revoked_at", ""),
        "user_id_hash": binding.get("user_id_hash", ""),
        "token_hash_prefix": token_hash[:12],
        "display_name": binding.get("display_name", ""),
        "scopes": list(binding.get("scopes", []) or []),
        "metadata": public_token_metadata(dict(binding.get("metadata", {}) or {})),
    }


def _self_binding_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    route_type = str(payload.get("route_type", "") or "im_message").strip()
    route_value = str(payload.get("route_value", "") or "").strip()
    if route_type not in {"im_message", "im_robot", "im_userid", "manual"}:
        raise ValueError("route_type must be im_message, im_robot, im_userid, or manual")
    if not route_value:
        raise ValueError("route_value is required")
    metadata: dict[str, Any] = {
        "source": "self_service_binding",
        "route_type": route_type,
        "route_value": route_value,
    }
    if route_type == "im_message":
        metadata["im_message_target"] = route_value
    elif route_type == "im_robot":
        metadata["im_robot_key"] = route_value
    elif route_type == "im_userid":
        metadata["im_userid"] = route_value
    else:
        metadata["contact_route"] = route_value
    route_label = str(payload.get("route_label", "") or "").strip()
    if route_label:
        metadata["route_label"] = route_label
    return metadata


def _token_binding_user_hash(metadata: dict[str, Any]) -> str:
    for key in (
        "im_userid",
        "im_user_id",
        "userid",
        "user_id",
        "open_userid",
        "im_message_target",
        "im_robot_key",
        "im_robot_webhook",
        "contact_route",
        "route_value",
    ):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            return sha256(value.encode("utf-8")).hexdigest()
    return ""


def _diagnostic_webhook_response(result) -> dict:
    return {
        "query": result.query,
        "sub_kbs": list(result.sub_kbs),
        "context": result.context.to_dict(),
    }


def _diagnose_webhook_confirmation_args(payload: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for field in (
        "auth_token",
        "confirmation_policy",
        "confirmation_reason",
        "confirmation_message",
        "confirmation_payload",
    ):
        if field in payload:
            args[field] = payload[field]
    return args


def _append_diagnose_webhook_failure(store, source: str, action: str, status: str, exc: Exception, normalized) -> None:
    try:
        store.append_failure(
            source=source,
            action=action,
            status=status,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            normalized=normalized,
        )
    except Exception:
        return


def _run_diagnosis(
    payload: dict,
    *,
    service: OfflineKbService,
    fixture_path: str,
    registry_path: str,
    governance_policy_path: str,
    feedback_log_path: str,
    candidate_store_path: str,
    pending_docs_dir: str,
):
    context = parse_diagnostic_context(payload.get("context"))
    query = build_diagnostic_query(payload.get("query"), context)
    sub_kbs = _parse_sub_kbs(payload.get("sub_kbs"))
    top_k = _parse_top_k(payload.get("top_k"))
    min_confidence = _parse_min_confidence(payload.get("min_confidence"))
    include_governance = _parse_bool(payload.get("include_governance"), default=True)
    owner_groups = _registry_owner_groups(registry_path)
    governance_items = ()
    if include_governance:
        report = build_governance_report(
            [fixture_path],
            registry_path=registry_path,
            policy_path=governance_policy_path,
            feedback_log_path=feedback_log_path,
            candidate_store_path=candidate_store_path,
            pending_docs_dir=pending_docs_dir,
        )
        governance_items = report.items
    return service.diagnose(
        query,
        sub_kbs=sub_kbs,
        top_k=top_k,
        governance_items=governance_items,
        owner_groups=owner_groups,
        min_confidence=min_confidence,
        context=context,
    )
