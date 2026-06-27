from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AtomDraft, AtomRecord, AtomStatus, Layer, RawDocument
from .pipeline import ingest_raw_document
from .source import load_combined_source_bundle


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LocalIndexSummary:
    db_path: str
    source_documents: int
    knowledge_atoms: int


def build_local_index(
    source_path: str | Path,
    db_path: str | Path,
    *,
    include_paths: tuple[str | Path, ...] | list[str | Path] = (),
) -> LocalIndexSummary:
    bundle = load_combined_source_bundle((source_path, *tuple(include_paths)))
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        _init_schema(conn)
        conn.execute("DELETE FROM source_documents")
        conn.execute("DELETE FROM knowledge_atoms")

        records: list[AtomRecord] = []
        from .store import InMemoryAtomStore

        store = InMemoryAtomStore()
        for raw in bundle.documents:
            sub_kb_id = bundle.sub_kbs[raw.docid]
            records.extend(ingest_raw_document(raw, sub_kb_id=sub_kb_id, store=store))
            _insert_source(conn, raw, sub_kb_id)
        for record in records:
            _insert_atom(conn, record)
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()

    return LocalIndexSummary(
        db_path=str(db_path),
        source_documents=len(bundle.documents),
        knowledge_atoms=len(records),
    )


class SQLiteAtomStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def list_atoms(
        self,
        *,
        sub_kbs: set[str] | None = None,
        source_docids: set[str] | None = None,
    ) -> list[AtomRecord]:
        where: list[str] = []
        params: list[Any] = []
        if sub_kbs:
            where.append(f"sub_kb_id IN ({','.join('?' for _ in sub_kbs)})")
            params.extend(sorted(sub_kbs))
        if source_docids:
            where.append(f"source_docid IN ({','.join('?' for _ in source_docids)})")
            params.extend(sorted(source_docids))
        sql = "SELECT * FROM knowledge_atoms"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY atom_id"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            return [_record_from_row(row) for row in conn.execute(sql, params)]

    def get(self, atom_id: str) -> AtomRecord:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM knowledge_atoms WHERE atom_id = ?", (atom_id,)).fetchone()
        if row is None:
            raise KeyError(atom_id)
        return _record_from_row(row)

    def __len__(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM knowledge_atoms").fetchone()
        return int(row[0])


def local_index_stats(db_path: str | Path) -> dict[str, int | str]:
    db_path = Path(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        sources = int(conn.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0])
        atoms = int(conn.execute("SELECT COUNT(*) FROM knowledge_atoms").fetchone()[0])
        version_row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    return {
        "db_path": str(db_path),
        "source_documents": sources,
        "knowledge_atoms": atoms,
        "schema_version": version_row[0] if version_row else "",
    }


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    docid TEXT PRIMARY KEY,
    sub_kb_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content_type TEXT NOT NULL,
    url TEXT NOT NULL,
    owner TEXT,
    last_modified TEXT,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_atoms (
    atom_id TEXT PRIMARY KEY,
    sub_kb_id TEXT NOT NULL,
    source_docid TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_anchor TEXT NOT NULL,
    section_path_json TEXT NOT NULL,
    text TEXT NOT NULL,
    contextual_prefix TEXT NOT NULL,
    layer TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_atoms_sub_kb ON knowledge_atoms(sub_kb_id);
CREATE INDEX IF NOT EXISTS idx_atoms_source_docid ON knowledge_atoms(source_docid);
"""
    )


def _insert_source(conn: sqlite3.Connection, raw: RawDocument, sub_kb_id: str) -> None:
    metadata = raw.metadata
    conn.execute(
        """
INSERT INTO source_documents (
    docid, sub_kb_id, title, content_type, url, owner, last_modified, metadata_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            raw.docid,
            sub_kb_id,
            raw.title,
            raw.content_type,
            raw.url,
            metadata.get("owner"),
            metadata.get("last_modified") or metadata.get("page_updated_at"),
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )


def _insert_atom(conn: sqlite3.Connection, record: AtomRecord) -> None:
    draft = record.draft
    conn.execute(
        """
INSERT INTO knowledge_atoms (
    atom_id, sub_kb_id, source_docid, source_title, source_anchor,
    section_path_json, text, contextual_prefix, layer, status, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
        (
            record.atom_id,
            draft.sub_kb_id,
            draft.source_docid,
            draft.source_title,
            draft.source_anchor,
            json.dumps(list(draft.section_path), ensure_ascii=False),
            draft.text,
            draft.contextual_prefix,
            draft.layer.value,
            draft.status.value,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        ),
    )


def _record_from_row(row: sqlite3.Row) -> AtomRecord:
    draft = AtomDraft(
        sub_kb_id=str(row["sub_kb_id"]),
        source_docid=str(row["source_docid"]),
        source_title=str(row["source_title"]),
        source_anchor=str(row["source_anchor"]),
        section_path=tuple(json.loads(row["section_path_json"])),
        text=str(row["text"]),
        contextual_prefix=str(row["contextual_prefix"]),
        layer=Layer(str(row["layer"])),
        status=AtomStatus(str(row["status"])),
    )
    return AtomRecord(
        atom_id=str(row["atom_id"]),
        draft=draft,
        created_at=_parse_dt(str(row["created_at"])),
        updated_at=_parse_dt(str(row["updated_at"])),
    )


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
