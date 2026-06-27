"""远程 HTTP 向量化 provider(B9)。

一个轻量、零依赖的客户端,对接 OpenAI 兼容的 ``/embeddings`` 接口。它默认从不启用:
只有当 ``provider='remote'`` 且 endpoint 和 model id 都配好时,``resolve_embedder``
才会返回它;否则用确定性的哈希兜底,保证离线、无凭据的路径零漂移。

网络访问走 ``urllib``,opener 可选注入(沿用 storage_sync 的风格),这样不用真实凭据
也能对着本地 mock server 跑。真实连通性放在 B10 单独验证。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Sequence

__all__ = ["RemoteHttpEmbedder"]


class RemoteHttpEmbedder:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        model_id: str,
        dimensions: int,
        timeout_seconds: int = 20,
        batch_size: int = 64,
        max_retries: int = 2,
        opener: Any | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint is required for remote embedder")
        if not model_id:
            raise ValueError("model_id is required for remote embedder")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_id = model_id
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._opener = opener
        # 这是实打实的 provider,没发生兜底。
        self.fallback_reason = ""

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        vectors: list[list[float]] = []
        for batch in _batches(items, self.batch_size):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        payload = {"model": self.model_id, "input": batch}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST", headers=headers
        )

        last_exc: Exception | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                data = self._open(request)
                return self._parse(data, len(batch))
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                last_exc = exc
                # 客户端错误(4xx,429 限流除外)不是临时故障:重试 400/401/404 纯属
                # 浪费时间,直接快速失败。
                if 400 <= code < 500 and code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_exc = exc
        raise RuntimeError("remote embedding request failed") from last_exc

    def _open(self, request: urllib.request.Request) -> Any:
        if self._opener is not None:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        else:
            response = urllib.request.urlopen(request, timeout=self.timeout_seconds)
        with response:
            return json.loads(response.read().decode("utf-8"))

    def _parse(self, data: Any, expected_count: int) -> list[list[float]]:
        if not isinstance(data, dict):
            raise ValueError("remote embedding response is not an object")
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != expected_count:
            raise ValueError("remote embedding response has unexpected item count")
        ordered = sorted(
            rows,
            key=lambda row: row.get("index", 0) if isinstance(row, dict) else 0,
        )
        vectors: list[list[float]] = []
        for row in ordered:
            if not isinstance(row, dict):
                raise ValueError("remote embedding row is not an object")
            embedding = row.get("embedding")
            if not isinstance(embedding, list) or len(embedding) != self.dimensions:
                raise ValueError("remote embedding has unexpected dimension")
            vectors.append([float(value) for value in embedding])
        return vectors


def _batches(items: list[str], batch_size: int):
    for offset in range(0, len(items), batch_size):
        yield items[offset : offset + batch_size]
