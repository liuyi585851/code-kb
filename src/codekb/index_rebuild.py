from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable

from .local_index import build_local_index, local_index_stats


def rebuild_search_index(
    *,
    fixture_path: str | Path,
    db_path: str | Path,
    include_paths: Iterable[str | Path] = (),
    atomic: bool = True,
) -> dict[str, Any]:
    target_db = Path(db_path)
    include_sources = tuple(str(Path(path)) for path in include_paths if str(path).strip())
    before = _safe_stats(target_db)
    if atomic:
        target_db.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(prefix=f".{target_db.name}.", suffix=".tmp", dir=target_db.parent, delete=False) as file:
            temp_db = Path(file.name)
        try:
            summary = build_local_index(fixture_path, temp_db, include_paths=include_sources)
            temp_db.replace(target_db)
        finally:
            if temp_db.exists():
                temp_db.unlink()
    else:
        summary = build_local_index(fixture_path, target_db, include_paths=include_sources)
    after = local_index_stats(target_db)
    return {
        "status": "rebuilt",
        "db_path": str(target_db),
        "fixture_path": str(fixture_path),
        "include_sources": list(include_sources),
        "atomic": atomic,
        "before": before,
        "after": after,
        "source_documents": summary.source_documents,
        "knowledge_atoms": summary.knowledge_atoms,
    }


def _safe_stats(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "db_path": str(db_path),
            "source_documents": 0,
            "knowledge_atoms": 0,
            "schema_version": "",
            "exists": False,
        }
    stats = dict(local_index_stats(db_path))
    stats["exists"] = True
    return stats
