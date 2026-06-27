from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import RawDocument
from .pipeline import ingest_raw_document
from .source import SourceBundle, load_source_bundle
from .store import InMemoryAtomStore


STATE_VERSION = 1


@dataclass(frozen=True)
class DocumentSyncResult:
    docid: str
    title: str
    sub_kb_id: str
    status: str
    reason: str
    atom_count: int
    body_sha256: str
    metadata_sha256: str
    source_modified_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "docid": self.docid,
            "title": self.title,
            "sub_kb_id": self.sub_kb_id,
            "status": self.status,
            "reason": self.reason,
            "atom_count": self.atom_count,
            "body_sha256": self.body_sha256,
            "metadata_sha256": self.metadata_sha256,
            "source_modified_at": self.source_modified_at,
        }


@dataclass(frozen=True)
class SyncReport:
    source_path: str
    started_at: str
    finished_at: str
    total: int
    indexed: int
    skipped: int
    failed: int
    atom_count: int
    missing_docids: tuple[str, ...]
    results: tuple[DocumentSyncResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "failed": self.failed,
            "atom_count": self.atom_count,
            "missing_docids": list(self.missing_docids),
            "results": [item.to_dict() for item in self.results],
        }


def sync_source_path(
    source_path: str | Path,
    *,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    force: bool = False,
) -> SyncReport:
    source_path = Path(source_path)
    bundle = load_source_bundle(source_path)
    return sync_source_bundle(
        bundle,
        source_path=source_path,
        state_path=state_path,
        report_path=report_path,
        force=force,
    )


def sync_source_bundle(
    bundle: SourceBundle,
    *,
    source_path: str | Path,
    state_path: str | Path | None = None,
    report_path: str | Path | None = None,
    force: bool = False,
) -> SyncReport:
    started_at = _utc_now()
    previous_state = _read_state(state_path)
    previous_docs = dict(previous_state.get("documents", {}))
    current_docids = {raw.docid for raw in bundle.documents}
    missing_docids = tuple(sorted(set(previous_docs) - current_docids))

    store = InMemoryAtomStore()
    next_docs: dict[str, dict[str, Any]] = {}
    results: list[DocumentSyncResult] = []

    for raw in bundle.documents:
        result, state_entry = _sync_document(
            raw,
            sub_kb_id=bundle.sub_kbs.get(raw.docid, ""),
            previous_entry=previous_docs.get(raw.docid),
            store=store,
            synced_at=started_at,
            force=force,
        )
        results.append(result)
        if state_entry is not None:
            next_docs[raw.docid] = state_entry
        elif raw.docid in previous_docs:
            next_docs[raw.docid] = previous_docs[raw.docid]

    finished_at = _utc_now()
    report = SyncReport(
        source_path=str(source_path),
        started_at=started_at,
        finished_at=finished_at,
        total=len(results),
        indexed=sum(1 for item in results if item.status == "indexed"),
        skipped=sum(1 for item in results if item.status == "skipped"),
        failed=sum(1 for item in results if item.status == "failed"),
        atom_count=sum(item.atom_count for item in results if item.status == "indexed"),
        missing_docids=missing_docids,
        results=tuple(results),
    )

    if state_path is not None:
        _write_json_atomic(
            Path(state_path),
            {
                "version": STATE_VERSION,
                "source_path": str(source_path),
                "updated_at": finished_at,
                "documents": next_docs,
                "missing_docids": list(missing_docids),
            },
        )
    if report_path is not None:
        _write_json_atomic(Path(report_path), report.to_dict())

    return report


def _sync_document(
    raw: RawDocument,
    *,
    sub_kb_id: str,
    previous_entry: dict[str, Any] | None,
    store: InMemoryAtomStore,
    synced_at: str,
    force: bool,
) -> tuple[DocumentSyncResult, dict[str, Any] | None]:
    fingerprint = _fingerprint(raw, sub_kb_id)
    source_modified_at = str(raw.metadata.get("last_modified") or raw.metadata.get("page_updated_at") or "")
    base_result = {
        "docid": raw.docid,
        "title": raw.title,
        "sub_kb_id": sub_kb_id,
        "body_sha256": fingerprint["body_sha256"],
        "metadata_sha256": fingerprint["metadata_sha256"],
        "source_modified_at": source_modified_at,
    }

    if not sub_kb_id:
        return (
            DocumentSyncResult(
                **base_result,
                status="failed",
                reason="missing_sub_kb",
                atom_count=0,
            ),
            None,
        )

    if not force and previous_entry and _same_fingerprint(previous_entry, fingerprint):
        state_entry = dict(previous_entry)
        state_entry["last_seen_at"] = synced_at
        return (
            DocumentSyncResult(
                **base_result,
                status="skipped",
                reason="unchanged",
                atom_count=int(previous_entry.get("atom_count", 0)),
            ),
            state_entry,
        )

    try:
        records = ingest_raw_document(raw, sub_kb_id=sub_kb_id, store=store)
    except Exception as exc:  # pragma: no cover - 对接真实连接器时的防御性边界
        return (
            DocumentSyncResult(
                **base_result,
                status="failed",
                reason=f"ingest_error:{exc.__class__.__name__}",
                atom_count=0,
            ),
            None,
        )

    if not records:
        return (
            DocumentSyncResult(
                **base_result,
                status="failed",
                reason="empty_atom_set",
                atom_count=0,
            ),
            None,
        )

    reason = "forced" if force else _change_reason(previous_entry, fingerprint)
    state_entry = {
        "docid": raw.docid,
        "title": raw.title,
        "sub_kb_id": sub_kb_id,
        "content_type": raw.content_type,
        "body_sha256": fingerprint["body_sha256"],
        "metadata_sha256": fingerprint["metadata_sha256"],
        "source_modified_at": source_modified_at,
        "atom_count": len(records),
        "last_success_at": synced_at,
        "last_seen_at": synced_at,
    }
    return (
        DocumentSyncResult(
            **base_result,
            status="indexed",
            reason=reason,
            atom_count=len(records),
        ),
        state_entry,
    )


def _fingerprint(raw: RawDocument, sub_kb_id: str) -> dict[str, str]:
    metadata = {
        "docid": raw.docid,
        "title": raw.title,
        "content_type": raw.content_type,
        "sub_kb_id": sub_kb_id,
        "metadata": raw.metadata,
    }
    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "body_sha256": sha256(raw.body.encode("utf-8")).hexdigest(),
        "metadata_sha256": sha256(metadata_json.encode("utf-8")).hexdigest(),
    }


def _same_fingerprint(previous_entry: dict[str, Any], fingerprint: dict[str, str]) -> bool:
    return (
        previous_entry.get("body_sha256") == fingerprint["body_sha256"]
        and previous_entry.get("metadata_sha256") == fingerprint["metadata_sha256"]
    )


def _change_reason(previous_entry: dict[str, Any] | None, fingerprint: dict[str, str]) -> str:
    if previous_entry is None:
        return "new"
    body_changed = previous_entry.get("body_sha256") != fingerprint["body_sha256"]
    metadata_changed = previous_entry.get("metadata_sha256") != fingerprint["metadata_sha256"]
    if body_changed and metadata_changed:
        return "body_and_metadata_changed"
    if body_changed:
        return "body_changed"
    if metadata_changed:
        return "metadata_changed"
    return "changed"


def _read_state(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    state_path = Path(path)
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
