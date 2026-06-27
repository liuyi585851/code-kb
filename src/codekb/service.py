from __future__ import annotations

import os
from dataclasses import replace
from typing import Sequence
from uuid import uuid4

from .aliases import load_aliases
from .answer import answer_from_retrieval
from .diagnosis import DiagnosticResult, build_diagnostic_result
from .diagnosis_context import DiagnosticContext
from .embedding import Embedder
from .embedding_config import resolve_embedder
from .evaluator import build_fixture_store
from .governance import GovernanceItem
from .llm import LlmClient
from .llm_anthropic import AnthropicLlmClient
from .llm_openai_compat import OpenAICompatLlmClient
from .local_index import SQLiteAtomStore
from .models import AnswerResult
from .postgres import PostgresAtomStore
from .rerank import resolve_reranker
from .retrieval import Bm25LiteRetriever, HybridLiteRetriever, QdrantHybridLiteRetriever, QdrantLiteRetriever
from .trace import JsonlTraceLogger, TraceContext


def _llm_client_from_env() -> LlmClient | None:
    """按 ``CODEKB_LLM_PROVIDER`` 选定生成用的 LLM 客户端。

    默认 ``openai_compat`` 走规划中的 OpenAI 兼容 / 团队模型代理路径(兼容 OpenAI 的
    chat/completions);``anthropic`` 则选 Claude 适配器。两者的 ``from_env`` 在未配置时
    都返回 ``None``,调用方据此降级到确定性的抽取式路径,而不是直接失败。
    """
    provider = os.getenv("CODEKB_LLM_PROVIDER", "openai_compat").strip().lower()
    if provider == "anthropic":
        return AnthropicLlmClient.from_env()
    return OpenAICompatLlmClient.from_env()


class OfflineKbService:
    def __init__(
        self,
        *,
        fixture_path: str = "data/fixtures/sample_corpus.jsonl",
        aliases_path: str = "data/entity_aliases.yaml",
        trace_log_path: str | None = None,
        retriever_mode: str = "bm25-lite",
        index_db_path: str | None = None,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        qdrant_collection: str = "codekb_atoms",
        qdrant_timeout_seconds: int = 3,
        atom_store_mode: str | None = None,
        postgres_dsn: str | None = None,
        answer_mode: str | None = None,
        llm_client: LlmClient | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.store = self._build_store(
            fixture_path=fixture_path,
            index_db_path=index_db_path,
            atom_store_mode=atom_store_mode,
            postgres_dsn=postgres_dsn,
        )
        self.aliases = load_aliases(aliases_path) if aliases_path else {}
        # 统一的 embedder 来源。默认完全复刻原有行为:一个绑定别名的哈希 embedder,
        # 与 qdrant 检索器内部默认的那个完全一致(零偏差)。
        self.embedder = embedder if embedder is not None else resolve_embedder(aliases=self.aliases)
        if retriever_mode == "hybrid-lite":
            self.retriever = HybridLiteRetriever(self.store, aliases=self.aliases)
        elif retriever_mode in {"qdrant-lite", "qdrant-hybrid-lite"}:
            qdrant_kwargs = {
                "url": (
                    qdrant_url
                    or os.getenv("QDRANT_URL")
                    or os.getenv("CODEKB_QDRANT_URL")
                    or ""
                ),
                "api_key": (
                    qdrant_api_key
                    if qdrant_api_key is not None
                    else os.getenv("QDRANT_API_KEY", os.getenv("CODEKB_QDRANT_API_KEY", ""))
                ),
                "collection": qdrant_collection or os.getenv("CODEKB_QDRANT_COLLECTION", "codekb_atoms"),
                "timeout_seconds": qdrant_timeout_seconds,
                "aliases": self.aliases,
                "embedder": self.embedder,
                "reranker": resolve_reranker(),
            }
            retriever_cls = QdrantHybridLiteRetriever if retriever_mode == "qdrant-hybrid-lite" else QdrantLiteRetriever
            self.retriever = retriever_cls(
                self.store,
                **qdrant_kwargs,
            )
        else:
            self.retriever = Bm25LiteRetriever(self.store, aliases=self.aliases)
        self.trace_logger = JsonlTraceLogger(trace_log_path) if trace_log_path else None
        self.answer_mode, self.llm_client, self.answer_mode_downgrade_reason = (
            self._resolve_answer_mode(answer_mode, llm_client)
        )
        # 检索前用 LLM 做查询扩展(自然语言/中文概念 -> 代码标识符)。需手动开启
        # (默认关闭,不影响测试/离线场景),且依赖 LLM。
        self.expand_queries = os.getenv("CODEKB_QUERY_EXPANSION", "0").strip().lower() not in {"0", "false", "no", "off", ""}

    @staticmethod
    def _resolve_answer_mode(
        answer_mode: str | None, llm_client: LlmClient | None
    ) -> tuple[str, LlmClient | None, str]:
        mode = (
            answer_mode
            if answer_mode is not None
            else os.getenv("CODEKB_ANSWER_MODE", "extractive")
        ).strip().lower()

        if mode != "generative":
            # 抽取式(默认):完全不碰 LLM 这一层。
            return "extractive", None, ""

        client = llm_client if llm_client is not None else _llm_client_from_env()
        if client is None:
            # 没有可用的 LLM 客户端(比如没配 ANTHROPIC_API_KEY):降级到确定性的
            # 抽取式路径,而不是抛错。
            return "extractive", None, "no_llm_client"
        return "generative", client, ""

    def _build_store(
        self,
        *,
        fixture_path: str,
        index_db_path: str | None,
        atom_store_mode: str | None,
        postgres_dsn: str | None,
    ):
        mode = (atom_store_mode or os.getenv("CODEKB_ATOM_STORE", "auto")).strip().lower()
        if mode == "postgres":
            dsn = postgres_dsn or os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL") or ""
            if not dsn:
                raise ValueError("postgres_dsn is required when atom_store_mode is postgres")
            return PostgresAtomStore(dsn)
        if mode == "sqlite":
            if not index_db_path:
                raise ValueError("index_db_path is required when atom_store_mode is sqlite")
            return SQLiteAtomStore(index_db_path)
        if mode == "fixture":
            return build_fixture_store(fixture_path)
        if mode != "auto":
            raise ValueError(f"unknown atom_store_mode: {atom_store_mode}")
        return SQLiteAtomStore(index_db_path) if index_db_path else build_fixture_store(fixture_path)

    def ask(self, query: str, *, sub_kbs: set[str] | None = None, top_k: int = 4) -> AnswerResult:
        search_query = query
        if self.expand_queries and self.llm_client is not None:
            from .query_expand import expand_query

            terms = expand_query(query, self.llm_client)
            if terms:
                search_query = f"{query} {' '.join(terms)}"
        retrieval = self.retriever.retrieve(search_query, sub_kbs=sub_kbs, top_k=top_k)
        answer = answer_from_retrieval(
            query,
            retrieval,
            llm_client=self.llm_client,
            mode=self.answer_mode,
        )
        answer = replace(answer, answer_id=str(uuid4()), trace_id=str(uuid4()))

        if self.trace_logger is not None:
            context = TraceContext(
                answer_id=answer.answer_id,
                trace_id=answer.trace_id,
                query=query,
                sub_kbs=tuple(sorted(sub_kbs or ())),
                top_k=top_k,
            )
            self.trace_logger.write(context=context, retrieval=retrieval, answer=answer)

        return answer

    def diagnose(
        self,
        query: str,
        *,
        sub_kbs: set[str] | None = None,
        top_k: int = 4,
        governance_items: Sequence[GovernanceItem] = (),
        owner_groups: dict[str, str] | None = None,
        min_confidence: float = 0.35,
        context: DiagnosticContext | None = None,
    ) -> DiagnosticResult:
        answer = self.ask(query, sub_kbs=sub_kbs, top_k=top_k)
        return build_diagnostic_result(
            answer,
            sub_kbs=sub_kbs,
            governance_items=governance_items,
            owner_groups=owner_groups,
            min_confidence=min_confidence,
            context=context,
        )
