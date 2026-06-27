"""向量化抽象层,带一个确定性的哈希兜底实现。

本模块定义 :class:`Embedder` 协议和默认实现 :class:`HashedLexicalEmbedder`。
哈希向量化器复用 :func:`codekb.retrieval.hashed_lexical_vector`,因此输出和
export-index、Qdrant 检索器现用的确定性词法向量逐字节一致。

PR-07 只引入抽象和配置;``retrieval.py`` / ``index_artifacts.py`` 里的现有调用方
留到后续 PR 再迁移。
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, runtime_checkable

from .retrieval import hashed_lexical_vector

__all__ = ["Embedder", "HashedLexicalEmbedder"]


@runtime_checkable
class Embedder(Protocol):
    """所有向量化后端都要实现的协议。"""

    dimensions: int
    model_id: str

    def embed_query(self, text: str) -> list[float]:
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class HashedLexicalEmbedder:
    """离线、确定性的向量化器,底层是 ``hashed_lexical_vector``。

    :meth:`embed_query` 的输出和 ``hashed_lexical_vector(text, dimensions=...,
    aliases=...)`` 逐元素相等,因为它就是带同样的参数直接转调那个函数。
    """

    def __init__(
        self,
        *,
        dimensions: int = 64,
        aliases: Mapping[str, tuple[str, ...]] | None = None,
        model_id: str = "hashed-lexical-v1",
    ) -> None:
        self.dimensions = dimensions
        self.model_id = model_id
        # 统一转成普通 dict,这样不管传进来的是哪种 Mapping 子类,底层函数拿到的
        # 都是它预期的映射。
        self._aliases: dict[str, tuple[str, ...]] | None = dict(aliases) if aliases else None

    def embed_query(self, text: str) -> list[float]:
        return hashed_lexical_vector(text, dimensions=self.dimensions, aliases=self._aliases)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]
