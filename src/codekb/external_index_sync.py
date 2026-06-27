from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .embedding_config import load_embedding_config
from .index_artifacts import export_index_artifacts
from .storage_sync import (
    sync_opensearch_documents_from_env,
    sync_postgres_upserts_from_env,
    sync_qdrant_points_from_env,
)


def sync_external_index_artifacts(
    *,
    fixture_path: str | Path,
    output_dir: str | Path,
    include_paths: Iterable[str | Path] = (),
    env_file: str | Path | None = None,
    execute: bool = False,
    qdrant_collection: str = "codekb_atoms",
    qdrant_vector_size: int | None = None,
    recreate: bool = False,
) -> dict[str, Any]:
    # 向量维度只认一个来源:调用方没指定 vector size 时,用配置里的 embedding
    # 维度兜底(默认 64)。
    if qdrant_vector_size is None:
        qdrant_vector_size = load_embedding_config(env_file=env_file).dimensions
    output = Path(output_dir)
    summary = export_index_artifacts(fixture_path, output, include_paths=tuple(include_paths))
    postgres = sync_postgres_upserts_from_env(
        upserts_path=output / "postgres_upserts.jsonl",
        env_file=env_file,
        execute=execute,
    )
    qdrant = sync_qdrant_points_from_env(
        points_path=output / "qdrant_points.jsonl",
        env_file=env_file,
        collection=qdrant_collection,
        vector_size=qdrant_vector_size,
        execute=execute,
        recreate=recreate,
    )
    opensearch = sync_opensearch_documents_from_env(
        documents_path=output / "opensearch_documents.jsonl",
        env_file=env_file,
        execute=execute,
    )
    return {
        "status": _combined_status(
            postgres["status"], qdrant["status"], opensearch["status"], execute=execute
        ),
        "execute": execute,
        "output_dir": str(output),
        "summary": summary,
        "postgres": postgres,
        "qdrant": qdrant,
        "opensearch": opensearch,
    }


def _combined_status(*statuses: str, execute: bool) -> str:
    # "skipped"(比如 OpenSearch 没配端点)不影响整体状态。
    effective = {status for status in statuses if status != "skipped"}
    if not effective:
        return "synced" if execute else "planned"
    if "failed" in effective:
        return "failed"
    if "dimension_mismatch" in effective:
        # 维度不符被拒写(向量维度 != collection 维度)是硬中断,不是"部分成功",
        # 标成 blocked,免得被误当成基本同步完成。
        return "blocked"
    if execute:
        return "synced" if effective == {"synced"} else "partial"
    return "planned" if effective == {"planned"} else "partial"
