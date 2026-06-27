"""cross-encoder 重排抽象 + 自托管的 BGE 重排器。

按 docs/p0-dependencies.md,重排器选用 BGE Reranker v2-m3。检索默认的重排步骤是
基于词元重叠的词法启发式(retrieval._rerank_ids);配上 :class:`Reranker` 后,就换成
真正的相关性模型。

``LocalBgeReranker`` 通过 sentence-transformers 的 ``CrossEncoder`` 在本地跑
(惰性导入 —— 导入本模块不需要 torch)。未配置时 ``resolve_reranker`` 返回 ``None``,
检索沿用其确定性默认值(行为零漂移)。
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


@runtime_checkable
class Reranker(Protocol):
    model_id: str

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """返回 ``(original_index, score)`` 对,按分数从高到低排序。"""
        ...


class LocalBgeReranker:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_RERANK_MODEL,
        model: Any | None = None,
        max_length: int = 512,
        quantize: bool = True,
    ) -> None:
        self.model_id = model_name
        self._model = model
        self._max_length = max_length
        self._quantize = quantize

    def _ensure(self) -> Any:
        if self._model is None:
            from sentence_transformers import CrossEncoder  # lazy

            model = CrossEncoder(self.model_id, max_length=self._max_length)
            if self._quantize:
                # 对 transformer 的 Linear 层做动态 int8 量化 —— CPU 推理快约 2 倍,
                # 质量损失可忽略,且不引入新依赖。
                try:
                    import torch

                    model.model = torch.ao.quantization.quantize_dynamic(
                        model.model, {torch.nn.Linear}, dtype=torch.qint8
                    )
                except Exception:  # noqa: BLE001 - 量化失败就退回未量化的模型
                    pass
            self._model = model
        return self._model

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        docs = list(documents)
        if not docs:
            return []
        model = self._ensure()
        scores = model.predict([(query, doc) for doc in docs])
        order = sorted(range(len(docs)), key=lambda i: float(scores[i]), reverse=True)
        if top_n is not None:
            order = order[:top_n]
        return [(i, float(scores[i])) for i in order]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalBgeReranker":
        env = os.environ if env is None else env
        model = (env.get("CODEKB_RERANK_MODEL") or DEFAULT_RERANK_MODEL).strip()
        max_length = (env.get("CODEKB_RERANK_MAX_LENGTH") or "").strip()
        quantize = (env.get("CODEKB_RERANK_QUANTIZE") or "1").strip().lower() not in {"0", "false", "no", "off"}
        kwargs: dict[str, Any] = {"model_name": model, "quantize": quantize}
        if max_length:
            kwargs["max_length"] = int(max_length)
        return cls(**kwargs)


DEFAULT_GATEWAY_RERANK_MODEL = "qwen3-reranker-8b"


class GatewayReranker:
    """走 OpenAI/FTP-AI 兼容 ``/rerank`` 网关的 cross-encoder 重排器。

    请求:  {model, query, documents, top_n?}
    响应: {results: [{index, relevance_score}]}(也兼容 {data:[...]}/score)
    ``post_json`` 可注入便于测试;网络或解析失败会抛 RuntimeError,由
    retrieval._rerank_candidates 捕获后退回词法兜底。需要令牌:得配好 endpoint 和 key
    (如 ftp-ai FAT)。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        model: str = DEFAULT_GATEWAY_RERANK_MODEL,
        timeout: int = 20,
        post_json: Any | None = None,
    ) -> None:
        self.model_id = model
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout
        self._post_json = post_json or self._default_post

    def _rerank_url(self) -> str:
        endpoint = (self._endpoint or "").rstrip("/")
        return endpoint if endpoint.endswith("/rerank") else endpoint + "/rerank"

    def _default_post(self, url: str, body: dict) -> dict:
        import urllib.request

        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        docs = list(documents)
        if not docs:
            return []
        if not self._endpoint:
            raise RuntimeError("gateway reranker requires an endpoint")
        body: dict[str, Any] = {"model": self.model_id, "query": query, "documents": docs}
        if top_n is not None:
            body["top_n"] = top_n
        try:
            resp = self._post_json(self._rerank_url(), body)
        except Exception as exc:  # noqa: BLE001 - 包装成 RuntimeError,便于安全兜底
            raise RuntimeError("gateway rerank failed") from exc
        results = resp.get("results") or resp.get("data") or []
        out: list[tuple[int, float]] = []
        for item in results:
            idx = item.get("index")
            score = item.get("relevance_score", item.get("score", 0.0))
            if isinstance(idx, int) and 0 <= idx < len(docs):
                out.append((idx, float(score)))
        out.sort(key=lambda pair: pair[1], reverse=True)
        return out

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewayReranker":
        env = os.environ if env is None else env
        return cls(
            endpoint=(env.get("CODEKB_RERANK_ENDPOINT") or "").strip(),
            api_key=(env.get("CODEKB_RERANK_API_KEY") or "").strip(),
            model=(env.get("CODEKB_RERANK_MODEL") or DEFAULT_GATEWAY_RERANK_MODEL).strip(),
        )


def resolve_reranker(env: Mapping[str, str] | None = None) -> Reranker | None:
    """按 ``CODEKB_RERANK_PROVIDER`` 选出重排器。

    默认/空/'none' -> ``None``(检索沿用词法默认)。
    'local'   -> 自托管的 BGE cross-encoder。
    'gateway' -> OpenAI/FTP-AI 兼容的 /rerank(需要 endpoint+key)。
    """
    env = os.environ if env is None else env
    provider = (env.get("CODEKB_RERANK_PROVIDER") or "").strip().lower()
    if provider in {"", "none", "off", "disabled"}:
        return None
    if provider == "local":
        return LocalBgeReranker.from_env(env)
    if provider in {"gateway", "remote", "http"}:
        reranker = GatewayReranker.from_env(env)
        return reranker if reranker._endpoint else None
    return None
