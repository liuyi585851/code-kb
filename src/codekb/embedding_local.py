"""用 sentence-transformers 自托管的 BGE 文本向量化器(不走云端网关)。

按 docs/p0-dependencies.md,向量化模型用 BGE-large-zh-v1.5(或 bge-m3),在本地
CPU/GPU 上跑。``sentence_transformers`` 惰性导入,所以导入本模块本身不需要 torch。
实现了 ``Embedder`` 那套接口(``dimensions``、``model_id``、``embed_query``、
``embed_documents``)。
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

DEFAULT_LOCAL_EMBED_MODEL = "BAAI/bge-large-zh-v1.5"


class LocalSentenceTransformerEmbedder:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_LOCAL_EMBED_MODEL,
        dimensions: int | None = None,
        model: Any | None = None,
        normalize: bool = True,
        batch_size: int = 32,
    ) -> None:
        self.model_id = model_name
        self._model = model
        self._normalize = normalize
        self._batch_size = batch_size
        self._dim = dimensions

    def _ensure(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # 惰性导入

            self._model = SentenceTransformer(self.model_id)
        if self._dim is None:
            self._dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dim is None:
            self._ensure()
        return int(self._dim)

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure()
        vec = model.encode([text], normalize_embeddings=self._normalize, batch_size=self._batch_size)[0]
        return [float(x) for x in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        model = self._ensure()
        rows = model.encode(items, normalize_embeddings=self._normalize, batch_size=self._batch_size)
        return [[float(x) for x in row] for row in rows]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LocalSentenceTransformerEmbedder":
        env = os.environ if env is None else env
        model = (env.get("CODEKB_EMBEDDING_MODEL") or DEFAULT_LOCAL_EMBED_MODEL).strip()
        dim = (env.get("CODEKB_EMBEDDING_DIM") or "").strip()
        batch = (env.get("CODEKB_EMBEDDING_BATCH_SIZE") or "").strip()
        kwargs: dict[str, Any] = {"model_name": model}
        if dim:
            kwargs["dimensions"] = int(dim)
        if batch:
            kwargs["batch_size"] = int(batch)
        return cls(**kwargs)
