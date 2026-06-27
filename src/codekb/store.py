from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from .models import AtomDraft, AtomRecord


class InMemoryAtomStore:
    def __init__(self) -> None:
        self._atoms: dict[str, AtomRecord] = {}

    def upsert_draft(self, draft: AtomDraft) -> AtomRecord:
        atom_id = _stable_atom_id(draft)
        now = datetime.now(UTC)
        existing = self._atoms.get(atom_id)
        record = AtomRecord(
            atom_id=atom_id,
            draft=draft,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._atoms[atom_id] = record
        return record

    def upsert_many(self, drafts: list[AtomDraft]) -> list[AtomRecord]:
        return [self.upsert_draft(draft) for draft in drafts]

    def get(self, atom_id: str) -> AtomRecord:
        return self._atoms[atom_id]

    def list_atoms(
        self,
        *,
        sub_kbs: set[str] | None = None,
        source_docids: set[str] | None = None,
    ) -> list[AtomRecord]:
        atoms = list(self._atoms.values())
        if sub_kbs is not None:
            atoms = [atom for atom in atoms if atom.sub_kb_id in sub_kbs]
        if source_docids is not None:
            atoms = [atom for atom in atoms if atom.source_docid in source_docids]
        return atoms

    def __len__(self) -> int:
        return len(self._atoms)


def _stable_atom_id(draft: AtomDraft) -> str:
    key = "|".join(
        [
            draft.sub_kb_id,
            draft.source_docid,
            draft.source_anchor,
            "/".join(draft.section_path),
            draft.text[:200],
        ]
    )
    return str(uuid5(NAMESPACE_URL, key))

