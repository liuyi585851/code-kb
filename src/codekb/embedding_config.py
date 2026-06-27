"""向量化 provider / 向量维度配置的唯一来源。

在此模块之前,向量维度(vector size)散落硬编码在好几处(``hashed_lexical_vector``
的默认值、``api.py`` 里读 ``CODEKB_QDRANT_VECTOR_SIZE`` 的地方等等)。PR-07 把它们
收拢成一个从环境加载的配置对象,后续 PR 就能把维度一路传到 index/query/sync,
不必各处重新推导。

默认值严格保持当前线上行为:``provider=hashed``、``dimensions=64``。这里不碰网络、
不需要凭据;remote provider 在后续 PR 才接上,目前会退回哈希兜底(此时
``fallback_reason`` 非空)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .embedding import Embedder, HashedLexicalEmbedder

__all__ = [
    "EmbeddingConfig",
    "load_embedding_config",
    "resolve_embedder",
]

DEFAULT_PROVIDER = "hashed"
DEFAULT_DIMENSIONS = 64

# 规范的环境变量名(唯一来源)。
ENV_PROVIDER = "CODEKB_EMBEDDING_PROVIDER"
ENV_DIM = "CODEKB_EMBEDDING_DIM"
ENV_MODEL = "CODEKB_EMBEDDING_MODEL"
ENV_ENDPOINT = "CODEKB_EMBEDDING_ENDPOINT"
ENV_API_KEY = "CODEKB_EMBEDDING_API_KEY"
# 兼容 PR-07 之前向量维度配置的旧别名。
ENV_LEGACY_VECTOR_SIZE = "CODEKB_QDRANT_VECTOR_SIZE"

# ``resolve_embedder`` 能直接处理、无需兜底的 provider。
_SUPPORTED_PROVIDERS = {"hashed"}


@dataclass
class EmbeddingConfig:
    provider: str = "hashed"
    dimensions: int = 64
    model_id: str = ""
    endpoint: str = ""
    api_key: str = ""


def _coerce_dimensions(raw: str | None, fallback: int) -> int:
    if raw is None:
        return fallback
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    if value <= 0:
        return fallback
    return value


def load_embedding_config(
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
) -> EmbeddingConfig:
    """从环境变量(以及可选的 env 文件)构建 :class:`EmbeddingConfig`。

    ``env`` 默认取 ``os.environ``。``env_file`` 若给了且存在,会按和 CLI 相同的
    KEY=VALUE 语义解析,其值覆盖 ``env`` 里的同名项。
    """

    effective: dict[str, str] = dict(os.environ if env is None else env)
    if env_file:
        effective.update(_parse_env_file(env_file))

    provider = (effective.get(ENV_PROVIDER) or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER

    # 优先用规范的 DIM 变量,其次用旧的向量维度变量,最后才用默认值。
    # 取值非法时退回默认值。
    dim_raw = effective.get(ENV_DIM)
    if dim_raw is None:
        dim_raw = effective.get(ENV_LEGACY_VECTOR_SIZE)
    dimensions = _coerce_dimensions(dim_raw, DEFAULT_DIMENSIONS)

    return EmbeddingConfig(
        provider=provider,
        dimensions=dimensions,
        model_id=(effective.get(ENV_MODEL) or "").strip(),
        endpoint=(effective.get(ENV_ENDPOINT) or "").strip(),
        api_key=(effective.get(ENV_API_KEY) or "").strip(),
    )


def resolve_embedder(
    config: EmbeddingConfig | None = None,
    *,
    aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> Embedder:
    """根据配置解析出一个 :class:`Embedder`。

    未知或尚未实现的 provider(比如 ``remote``)会退回到确定性的
    :class:`HashedLexicalEmbedder`,返回的实例上 ``fallback_reason`` 非空,说明
    退回原因。这样离线、无凭据的路径行为不会有任何漂移。
    """

    config = config or EmbeddingConfig()
    provider = (config.provider or DEFAULT_PROVIDER).strip().lower()

    fallback_reason = ""
    if provider in {"remote", "http"}:
        endpoint = (config.endpoint or "").strip()
        model_id = (config.model_id or "").strip()
        if endpoint and model_id:
            from .embedding_remote import RemoteHttpEmbedder

            return RemoteHttpEmbedder(
                endpoint=endpoint,
                api_key=config.api_key,
                model_id=model_id,
                dimensions=config.dimensions,
            )
        fallback_reason = (
            f"remote embedding provider '{config.provider}' is missing endpoint "
            "or model_id; using hashed-lexical fallback"
        )
    elif provider == "local":
        from .embedding_local import LocalSentenceTransformerEmbedder, DEFAULT_LOCAL_EMBED_MODEL

        # 用 sentence-transformers 自托管 BGE。维度默认由模型推断,除非环境里
        # 显式钉了一个非默认值。
        explicit_dim = config.dimensions if config.dimensions != DEFAULT_DIMENSIONS else None
        return LocalSentenceTransformerEmbedder(
            model_name=(config.model_id or "").strip() or DEFAULT_LOCAL_EMBED_MODEL,
            dimensions=explicit_dim,
        )
    elif provider != "hashed":
        fallback_reason = (
            f"unknown embedding provider '{config.provider}'; using "
            "hashed-lexical fallback"
        )

    model_id = config.model_id or "hashed-lexical-v1"
    embedder = HashedLexicalEmbedder(
        dimensions=config.dimensions,
        aliases=aliases,
        model_id=model_id,
    )
    # 把退回原因挂成一个普通属性;直接指定 hashed provider 时为空字符串。
    embedder.fallback_reason = fallback_reason  # type: ignore[attr-defined]
    return embedder


def _parse_env_file(path: str | Path) -> dict[str, str]:
    """解析 KEY=VALUE 格式的 env 文件(语义与 cli._parse_env_file 一致)。"""

    env_path = Path(path)
    if not env_path.exists():
        raise ValueError(f"env file not found: {env_path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"invalid env file line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        values[key] = _parse_env_value(value.strip())
    return values


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
