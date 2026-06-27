from __future__ import annotations

from .chunker import Chunker
from .models import AtomRecord, RawDocument
from .normalizer import DocumentNormalizer
from .store import InMemoryAtomStore


def ingest_raw_document(
    raw: RawDocument,
    *,
    sub_kb_id: str,
    store: InMemoryAtomStore,
    normalizer: DocumentNormalizer | None = None,
    chunker: Chunker | None = None,
) -> list[AtomRecord]:
    normalizer = normalizer or DocumentNormalizer()
    chunker = chunker or Chunker()

    normalized = normalizer.normalize(raw)
    drafts = chunker.chunk(normalized, sub_kb_id)
    return store.upsert_many(drafts)

