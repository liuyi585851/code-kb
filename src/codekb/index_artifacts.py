from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .embedding import Embedder
from .pipeline import ingest_raw_document
from .postgres import atom_upsert, source_document_upsert
from .source import SourceBundle, load_combined_source_bundle
from .store import InMemoryAtomStore


def export_index_artifacts(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    include_paths: tuple[str | Path, ...] | list[str | Path] = (),
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    bundle = load_combined_source_bundle((source_path, *tuple(include_paths)))
    return export_bundle_artifacts(bundle, output_dir, embedder=embedder)


def export_bundle_artifacts(
    bundle: SourceBundle,
    output_dir: str | Path,
    *,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    if embedder is None:
        from .embedding_config import resolve_embedder

        embedder = resolve_embedder()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    store = InMemoryAtomStore()
    records = []
    for raw in bundle.documents:
        records.extend(ingest_raw_document(raw, sub_kb_id=bundle.sub_kbs[raw.docid], store=store))

    _write_jsonl(output / "source_documents.jsonl", [_source_payload(raw, bundle.sub_kbs[raw.docid]) for raw in bundle.documents])
    _write_jsonl(output / "knowledge_atoms.jsonl", [_atom_payload(record) for record in records])
    _write_jsonl(output / "postgres_upserts.jsonl", _postgres_payloads(bundle, records))
    _write_jsonl(output / "opensearch_documents.jsonl", [_opensearch_payload(record) for record in records])
    _write_jsonl(output / "qdrant_points.jsonl", [_qdrant_payload(record, embedder) for record in records])
    summary = {
        "source_documents": len(bundle.documents),
        "knowledge_atoms": len(records),
        "postgres_upserts": len(bundle.documents) + len(records),
        "opensearch_documents": len(records),
        "qdrant_points": len(records),
        "embedding_model": embedder.model_id,
        "embedding_dim": embedder.dimensions,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def _source_payload(raw, sub_kb_id: str) -> dict[str, Any]:
    return {
        "docid": raw.docid,
        "sub_kb_id": sub_kb_id,
        "title": raw.title,
        "content_type": raw.content_type,
        "url": raw.url,
        "metadata": raw.metadata,
    }


def _atom_payload(record) -> dict[str, Any]:
    draft = record.draft
    return {
        "atom_id": record.atom_id,
        "sub_kb_id": draft.sub_kb_id,
        "source_docid": draft.source_docid,
        "source_title": draft.source_title,
        "source_anchor": draft.source_anchor,
        "section_path": list(draft.section_path),
        "text": draft.text,
        "contextual_prefix": draft.contextual_prefix,
        "layer": draft.layer.value,
        "status": draft.status.value,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _postgres_payloads(bundle: SourceBundle, records) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw in bundle.documents:
        stmt = source_document_upsert(raw)
        payloads.append({"target": "source_documents", "sql": stmt.sql, "params": list(stmt.params)})
    for record in records:
        stmt = atom_upsert(record)
        payloads.append({"target": "knowledge_atoms", "sql": stmt.sql, "params": list(stmt.params)})
    return payloads


def _opensearch_payload(record) -> dict[str, Any]:
    atom = _atom_payload(record)
    return {
        "_id": record.atom_id,
        "_index": "codekb_atoms",
        "_source": {
            **atom,
            "search_text": "\n".join([record.draft.contextual_prefix, record.draft.text, record.draft.source_title]),
        },
    }


def _qdrant_payload(record, embedder: Embedder) -> dict[str, Any]:
    text = record.draft.contextual_prefix + "\n" + record.draft.text
    return {
        "id": record.atom_id,
        "vector": embedder.embed_documents([text])[0],
        "payload": {
            "sub_kb_id": record.sub_kb_id,
            "source_docid": record.source_docid,
            "source_title": record.draft.source_title,
            "source_anchor": record.draft.source_anchor,
            "section_path": list(record.draft.section_path),
            "layer": record.draft.layer.value,
            "status": record.draft.status.value,
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n")


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return str(value)
