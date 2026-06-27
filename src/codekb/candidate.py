from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4


CANDIDATE_SCHEMA_VERSION = 1
PENDING_STATUS = "pending_review"
APPROVED_STATUS = "approved"
REJECTED_STATUS = "rejected"
NEEDS_REVISION_STATUS = "needs_revision"
TERMINAL_STATUSES = {APPROVED_STATUS, REJECTED_STATUS}
REVISION_ACTION = "revise"
AUDIT_ACTIONS = {
    "approve": APPROVED_STATUS,
    "reject": REJECTED_STATUS,
    "request_revision": NEEDS_REVISION_STATUS,
}


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    sub_kb_id: str
    title: str
    content: str
    source_type: str
    source_ref: str
    submitted_by_hash: str
    status: str
    dedupe_key: str
    conflict_candidate_id: str
    metadata: dict[str, Any]
    approved_doc_path: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "sub_kb_id": self.sub_kb_id,
            "title": self.title,
            "content": self.content,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "submitted_by_hash": self.submitted_by_hash,
            "status": self.status,
            "dedupe_key": self.dedupe_key,
            "conflict_candidate_id": self.conflict_candidate_id,
            "metadata": dict(self.metadata),
            "approved_doc_path": self.approved_doc_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    candidate_id: str
    action: str
    reviewer_hash: str
    comment: str
    previous_status: str
    new_status: str
    output_path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "candidate_id": self.candidate_id,
            "action": self.action,
            "reviewer_hash": self.reviewer_hash,
            "comment": self.comment,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "output_path": self.output_path,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CandidateSubmission:
    candidate: CandidateRecord
    duplicate: bool
    existing_candidate_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "duplicate": self.duplicate,
            "existing_candidate_id": self.existing_candidate_id,
        }


@dataclass(frozen=True)
class AuditResult:
    candidate: CandidateRecord
    audit: AuditRecord

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "audit": self.audit.to_dict(),
        }


class JsonCandidateStore:
    def __init__(self, path: str | Path, *, pending_docs_dir: str | Path) -> None:
        self.path = Path(path)
        self.pending_docs_dir = Path(pending_docs_dir)

    def submit(
        self,
        *,
        sub_kb_id: str,
        title: str,
        content: str,
        source_type: str = "manual",
        source_ref: str = "",
        submitted_by_hash: str = "",
        metadata: dict[str, Any] | None = None,
        allow_duplicate: bool = False,
    ) -> CandidateSubmission:
        sub_kb_id = _require_token(sub_kb_id, "sub_kb_id")
        title = _require_text(title, "title", max_length=200)
        content = _require_text(content, "content", max_length=20000)
        source_type = _normalize_token(source_type or "manual")
        source_ref = str(source_ref or "").strip()
        submitted_by_hash = str(submitted_by_hash or "").strip()
        metadata = dict(metadata or {})
        dedupe_key = candidate_dedupe_key(sub_kb_id=sub_kb_id, title=title, content=content)

        state = self._read_state()
        existing = _find_by_dedupe(state["candidates"], dedupe_key)
        if existing is not None and not allow_duplicate:
            return CandidateSubmission(
                candidate=existing,
                duplicate=True,
                existing_candidate_id=existing.candidate_id,
            )

        now = _now()
        conflict = _find_title_conflict(state["candidates"], sub_kb_id=sub_kb_id, title=title, dedupe_key=dedupe_key)
        candidate = CandidateRecord(
            candidate_id=str(uuid4()),
            sub_kb_id=sub_kb_id,
            title=title,
            content=content,
            source_type=source_type,
            source_ref=source_ref,
            submitted_by_hash=submitted_by_hash,
            status=PENDING_STATUS,
            dedupe_key=dedupe_key,
            conflict_candidate_id=conflict.candidate_id if conflict else "",
            metadata=metadata,
            approved_doc_path="",
            created_at=now,
            updated_at=now,
        )
        state["candidates"].append(candidate)
        self._write_state(state)
        return CandidateSubmission(candidate=candidate, duplicate=False)

    def audit(
        self,
        candidate_id: str,
        *,
        action: str,
        reviewer_hash: str = "",
        comment: str = "",
    ) -> AuditResult:
        action = _normalize_action(action)
        reviewer_hash = str(reviewer_hash or "").strip()
        comment = str(comment or "").strip()
        state = self._read_state()
        index, current = _get_candidate(state["candidates"], candidate_id)
        if current.status in TERMINAL_STATUSES:
            raise ValueError(f"candidate already {current.status}: {candidate_id}")

        new_status = AUDIT_ACTIONS[action]
        now = _now()
        output_path = ""
        if new_status == APPROVED_STATUS:
            output_path = self._write_pending_doc(current, approved_at=now)

        updated = CandidateRecord(
            candidate_id=current.candidate_id,
            sub_kb_id=current.sub_kb_id,
            title=current.title,
            content=current.content,
            source_type=current.source_type,
            source_ref=current.source_ref,
            submitted_by_hash=current.submitted_by_hash,
            status=new_status,
            dedupe_key=current.dedupe_key,
            conflict_candidate_id=current.conflict_candidate_id,
            metadata=current.metadata,
            approved_doc_path=output_path or current.approved_doc_path,
            created_at=current.created_at,
            updated_at=now,
        )
        audit = AuditRecord(
            audit_id=str(uuid4()),
            candidate_id=current.candidate_id,
            action=action,
            reviewer_hash=reviewer_hash,
            comment=comment,
            previous_status=current.status,
            new_status=new_status,
            output_path=output_path,
            created_at=now,
        )
        state["candidates"][index] = updated
        state["audits"].append(audit)
        self._write_state(state)
        return AuditResult(candidate=updated, audit=audit)

    def revise(
        self,
        candidate_id: str,
        *,
        title: str = "",
        content: str = "",
        metadata: dict[str, Any] | None = None,
        submitted_by_hash: str = "",
        comment: str = "",
    ) -> AuditResult:
        state = self._read_state()
        index, current = _get_candidate(state["candidates"], candidate_id)
        if current.status != NEEDS_REVISION_STATUS:
            raise ValueError(f"candidate must be needs_revision before revise: {candidate_id}")
        title = _require_text(title or current.title, "title", max_length=200)
        content = _require_text(content or current.content, "content", max_length=20000)
        next_metadata = {**current.metadata, **dict(metadata or {})}
        submitted_by_hash = str(submitted_by_hash or "").strip()
        comment = str(comment or "").strip()
        dedupe_key = candidate_dedupe_key(sub_kb_id=current.sub_kb_id, title=title, content=content)
        existing = _find_by_dedupe_excluding(state["candidates"], dedupe_key, exclude_candidate_id=current.candidate_id)
        if existing is not None:
            raise ValueError(f"revision duplicates existing candidate: {existing.candidate_id}")
        conflict = _find_title_conflict_excluding(
            state["candidates"],
            sub_kb_id=current.sub_kb_id,
            title=title,
            dedupe_key=dedupe_key,
            exclude_candidate_id=current.candidate_id,
        )
        now = _now()
        updated = CandidateRecord(
            candidate_id=current.candidate_id,
            sub_kb_id=current.sub_kb_id,
            title=title,
            content=content,
            source_type=current.source_type,
            source_ref=current.source_ref,
            submitted_by_hash=submitted_by_hash or current.submitted_by_hash,
            status=PENDING_STATUS,
            dedupe_key=dedupe_key,
            conflict_candidate_id=conflict.candidate_id if conflict else "",
            metadata=next_metadata,
            approved_doc_path="",
            created_at=current.created_at,
            updated_at=now,
        )
        audit = AuditRecord(
            audit_id=str(uuid4()),
            candidate_id=current.candidate_id,
            action=REVISION_ACTION,
            reviewer_hash=submitted_by_hash,
            comment=comment,
            previous_status=current.status,
            new_status=PENDING_STATUS,
            output_path="",
            created_at=now,
        )
        state["candidates"][index] = updated
        state["audits"].append(audit)
        self._write_state(state)
        return AuditResult(candidate=updated, audit=audit)

    def get(self, candidate_id: str) -> CandidateRecord:
        _, candidate = _get_candidate(self._read_state()["candidates"], candidate_id)
        return candidate

    def list(self, *, status: str | None = None, limit: int = 50) -> tuple[CandidateRecord, ...]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        candidates = self._read_state()["candidates"]
        if status:
            candidates = [item for item in candidates if item.status == status]
        candidates = sorted(candidates, key=lambda item: item.updated_at, reverse=True)
        return tuple(candidates[:limit])

    def audits(self, *, candidate_id: str | None = None, limit: int = 50) -> tuple[AuditRecord, ...]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        audits = self._read_state()["audits"]
        if candidate_id:
            audits = [item for item in audits if item.candidate_id == candidate_id]
        audits = sorted(audits, key=lambda item: item.created_at, reverse=True)
        return tuple(audits[:limit])

    def summary(self) -> dict[str, Any]:
        candidates = self._read_state()["candidates"]
        counts = {
            PENDING_STATUS: 0,
            APPROVED_STATUS: 0,
            REJECTED_STATUS: 0,
            NEEDS_REVISION_STATUS: 0,
        }
        for candidate in candidates:
            counts[candidate.status] = counts.get(candidate.status, 0) + 1
        conflicts = sum(1 for candidate in candidates if candidate.conflict_candidate_id)
        return {
            "total": len(candidates),
            "pending_review": counts.get(PENDING_STATUS, 0),
            "approved": counts.get(APPROVED_STATUS, 0),
            "rejected": counts.get(REJECTED_STATUS, 0),
            "needs_revision": counts.get(NEEDS_REVISION_STATUS, 0),
            "conflicts": conflicts,
            "latest_updated_at": max((candidate.updated_at for candidate in candidates), default=""),
        }

    def purge(self, *, status: str, dry_run: bool = True) -> dict[str, Any]:
        """清除状态为 ``status`` 的候选(连同其审核记录)。

        默认先 dry-run:``dry_run=True``(默认)时不写盘,只返回一份确定性的清除计划和
        计数。``dry_run=False`` 时才真正从 store 里删掉匹配的候选及其审核记录,并删除对应
        的待发布文档文件(文件不存在则安全跳过)。
        """

        status = str(status or "").strip()
        if not status:
            raise ValueError("status is required for purge")

        state = self._read_state()
        matched = [item for item in state["candidates"] if item.status == status]
        matched_ids = {item.candidate_id for item in matched}
        matched_audits = [item for item in state["audits"] if item.candidate_id in matched_ids]

        doc_paths = []
        for candidate in matched:
            path = self._pending_doc_path(candidate)
            if path is not None and path.exists():
                doc_paths.append(path)

        plan: dict[str, Any] = {
            "status": status,
            "dry_run": dry_run,
            "matched_candidates": len(matched),
            "matched_audits": len(matched_audits),
            "pending_docs": len(doc_paths),
            "remaining_candidates": len(state["candidates"]) - len(matched),
            "candidate_ids": sorted(matched_ids),
            "removed_pending_docs": [],
        }

        if dry_run:
            return plan

        removed_docs = []
        pending_root = self.pending_docs_dir.resolve()
        for path in doc_paths:
            try:
                # 绝不删除待发布目录以外的文件,哪怕某个候选存下来的 approved_doc_path
                # 指向了别处。
                if not path.resolve().is_relative_to(pending_root):
                    continue
                path.unlink()
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                continue
            removed_docs.append(str(path))

        remaining_candidates = [item for item in state["candidates"] if item.candidate_id not in matched_ids]
        remaining_audits = [item for item in state["audits"] if item.candidate_id not in matched_ids]
        self._write_state({"candidates": remaining_candidates, "audits": remaining_audits})
        plan["removed_pending_docs"] = sorted(removed_docs)
        return plan

    def _pending_doc_path(self, candidate: CandidateRecord) -> Path | None:
        if candidate.approved_doc_path:
            return Path(candidate.approved_doc_path)
        return self.pending_docs_dir / candidate.sub_kb_id / f"{candidate.candidate_id}.md"

    def _read_state(self) -> dict[str, list[CandidateRecord] | list[AuditRecord]]:
        if not self.path.exists():
            return {"candidates": [], "audits": []}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        candidates = tuple(_candidate_from_dict(item) for item in payload.get("candidates", []))
        audits = tuple(_audit_from_dict(item) for item in payload.get("audits", []))
        return {"candidates": list(candidates), "audits": list(audits)}

    def _write_state(self, state: dict[str, list[CandidateRecord] | list[AuditRecord]]) -> None:
        payload = {
            "version": CANDIDATE_SCHEMA_VERSION,
            "candidates": [item.to_dict() for item in state["candidates"]],
            "audits": [item.to_dict() for item in state["audits"]],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            tmp_path = Path(file.name)
        tmp_path.replace(self.path)

    def _write_pending_doc(self, candidate: CandidateRecord, *, approved_at: str) -> str:
        target_dir = self.pending_docs_dir / candidate.sub_kb_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{candidate.candidate_id}.md"
        target.write_text(render_pending_document(candidate, approved_at=approved_at), encoding="utf-8")
        return str(target)


def parse_ingest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    source = payload.get("source", {})
    if source is None:
        source = {}
    if source and not isinstance(source, dict):
        raise ValueError("source must be an object")
    return {
        "sub_kb_id": str(payload.get("sub_kb_id") or payload.get("sub_kb") or "").strip(),
        "title": str(payload.get("title", "")).strip(),
        "content": str(payload.get("content", "")).strip(),
        "source_type": str(payload.get("source_type") or source.get("type") or "manual").strip(),
        "source_ref": str(payload.get("source_ref") or source.get("ref") or "").strip(),
        "submitted_by_hash": str(payload.get("submitted_by_hash") or payload.get("user_id_hash") or "").strip(),
        "metadata": metadata,
        "allow_duplicate": bool(payload.get("allow_duplicate", False)),
    }


def parse_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": _normalize_action(str(payload.get("action", "")).strip()),
        "reviewer_hash": str(payload.get("reviewer_hash") or payload.get("user_id_hash") or "").strip(),
        "comment": str(payload.get("comment", "")).strip(),
    }


def parse_revision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return {
        "title": str(payload.get("title", "")).strip(),
        "content": str(payload.get("content", "")).strip(),
        "metadata": metadata,
        "submitted_by_hash": str(payload.get("submitted_by_hash") or payload.get("user_id_hash") or "").strip(),
        "comment": str(payload.get("comment", "")).strip(),
    }


def candidate_dedupe_key(*, sub_kb_id: str, title: str, content: str) -> str:
    normalized = "\n".join(
        [
            _normalize_token(sub_kb_id),
            _normalize_text(title),
            _normalize_text(content),
        ]
    )
    return sha256(normalized.encode("utf-8")).hexdigest()


def render_pending_document(candidate: CandidateRecord, *, approved_at: str) -> str:
    metadata = {
        "candidate_id": candidate.candidate_id,
        "sub_kb_id": candidate.sub_kb_id,
        "source_type": candidate.source_type,
        "source_ref": candidate.source_ref,
        "submitted_by_hash": candidate.submitted_by_hash,
        "dedupe_key": candidate.dedupe_key,
        "conflict_candidate_id": candidate.conflict_candidate_id,
        "approved_at": approved_at,
    }
    front_matter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    return f"---\n{front_matter}\n---\n\n# {candidate.title}\n\n{candidate.content}\n"


def _candidate_from_dict(payload: dict[str, Any]) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=str(payload.get("candidate_id", "")).strip(),
        sub_kb_id=str(payload.get("sub_kb_id", "")).strip(),
        title=str(payload.get("title", "")).strip(),
        content=str(payload.get("content", "")).strip(),
        source_type=str(payload.get("source_type", "manual")).strip(),
        source_ref=str(payload.get("source_ref", "")).strip(),
        submitted_by_hash=str(payload.get("submitted_by_hash", "")).strip(),
        status=str(payload.get("status", PENDING_STATUS)).strip(),
        dedupe_key=str(payload.get("dedupe_key", "")).strip(),
        conflict_candidate_id=str(payload.get("conflict_candidate_id", "")).strip(),
        metadata=dict(payload.get("metadata", {})),
        approved_doc_path=str(payload.get("approved_doc_path", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
        updated_at=str(payload.get("updated_at", "")).strip(),
    )


def _audit_from_dict(payload: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        audit_id=str(payload.get("audit_id", "")).strip(),
        candidate_id=str(payload.get("candidate_id", "")).strip(),
        action=str(payload.get("action", "")).strip(),
        reviewer_hash=str(payload.get("reviewer_hash", "")).strip(),
        comment=str(payload.get("comment", "")).strip(),
        previous_status=str(payload.get("previous_status", "")).strip(),
        new_status=str(payload.get("new_status", "")).strip(),
        output_path=str(payload.get("output_path", "")).strip(),
        created_at=str(payload.get("created_at", "")).strip(),
    )


def _find_by_dedupe(candidates: list[CandidateRecord], dedupe_key: str) -> CandidateRecord | None:
    for candidate in candidates:
        if candidate.dedupe_key == dedupe_key and candidate.status != REJECTED_STATUS:
            return candidate
    return None


def _find_by_dedupe_excluding(
    candidates: list[CandidateRecord],
    dedupe_key: str,
    *,
    exclude_candidate_id: str,
) -> CandidateRecord | None:
    for candidate in candidates:
        if candidate.candidate_id == exclude_candidate_id:
            continue
        if candidate.dedupe_key == dedupe_key and candidate.status != REJECTED_STATUS:
            return candidate
    return None


def _find_title_conflict(
    candidates: list[CandidateRecord],
    *,
    sub_kb_id: str,
    title: str,
    dedupe_key: str,
) -> CandidateRecord | None:
    title_key = _normalize_text(title)
    for candidate in candidates:
        if (
            candidate.sub_kb_id == sub_kb_id
            and _normalize_text(candidate.title) == title_key
            and candidate.dedupe_key != dedupe_key
            and candidate.status != REJECTED_STATUS
        ):
            return candidate
    return None


def _find_title_conflict_excluding(
    candidates: list[CandidateRecord],
    *,
    sub_kb_id: str,
    title: str,
    dedupe_key: str,
    exclude_candidate_id: str,
) -> CandidateRecord | None:
    title_key = _normalize_text(title)
    for candidate in candidates:
        if candidate.candidate_id == exclude_candidate_id:
            continue
        if (
            candidate.sub_kb_id == sub_kb_id
            and _normalize_text(candidate.title) == title_key
            and candidate.dedupe_key != dedupe_key
            and candidate.status != REJECTED_STATUS
        ):
            return candidate
    return None


def _get_candidate(candidates: list[CandidateRecord], candidate_id: str) -> tuple[int, CandidateRecord]:
    candidate_id = str(candidate_id or "").strip()
    for index, candidate in enumerate(candidates):
        if candidate.candidate_id == candidate_id:
            return index, candidate
    raise KeyError(f"candidate not found: {candidate_id}")


def _normalize_action(action: str) -> str:
    action = action.strip()
    aliases = {"needs_revision": "request_revision"}
    action = aliases.get(action, action)
    if action not in AUDIT_ACTIONS:
        raise ValueError("action must be approve, reject, or request_revision")
    return action


def _require_text(value: str, name: str, *, max_length: int) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


def _require_token(value: str, name: str) -> str:
    value = _normalize_token(value)
    if not value:
        raise ValueError(f"{name} is required")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{2,64}", value):
        raise ValueError(f"{name} must be 2-64 chars of letters, digits, underscore, or dash")
    return value


def _normalize_token(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
