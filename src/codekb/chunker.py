from __future__ import annotations

from .models import AtomDraft, NormalizedDocument


class Chunker:
    def __init__(self, min_chars: int = 200, max_chars: int = 800) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars

    def chunk(self, doc: NormalizedDocument, sub_kb_id: str) -> list[AtomDraft]:
        drafts: list[AtomDraft] = []
        for section in doc.sections:
            for part in _split_text(section.text, self.max_chars):
                text = part.strip()
                if not text:
                    continue
                prefix = _prefix(doc.title, section.path)
                drafts.append(
                    AtomDraft(
                        sub_kb_id=sub_kb_id,
                        source_docid=doc.docid,
                        source_title=doc.title,
                        source_anchor=section.anchor,
                        section_path=section.path,
                        text=text,
                        contextual_prefix=prefix,
                    )
                )
        return _merge_short_chunks(drafts, self.min_chars, self.max_chars)


def _split_text(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        if current and current_len + len(paragraph) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        if len(paragraph) > max_chars:
            chunks.extend(_split_long_paragraph(paragraph, max_chars))
            continue
        current.append(paragraph)
        current_len += len(paragraph) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    return [paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars)]


def _merge_short_chunks(drafts: list[AtomDraft], min_chars: int, max_chars: int) -> list[AtomDraft]:
    if not drafts:
        return []

    merged: list[AtomDraft] = []
    buffer = drafts[0]
    for draft in drafts[1:]:
        same_source = (
            draft.source_docid == buffer.source_docid
            and draft.source_anchor == buffer.source_anchor
            and draft.section_path == buffer.section_path
        )
        if same_source and len(buffer.text) < min_chars and len(buffer.text) + len(draft.text) + 2 <= max_chars:
            buffer = AtomDraft(
                sub_kb_id=buffer.sub_kb_id,
                source_docid=buffer.source_docid,
                source_title=buffer.source_title,
                source_anchor=buffer.source_anchor,
                section_path=buffer.section_path,
                text=f"{buffer.text}\n\n{draft.text}",
                contextual_prefix=buffer.contextual_prefix,
                layer=buffer.layer,
                status=buffer.status,
            )
        else:
            merged.append(buffer)
            buffer = draft
    merged.append(buffer)
    return merged


def _prefix(title: str, path: tuple[str, ...]) -> str:
    section = " / ".join(path)
    return f"本文档《{title}》中，本段位于「{section}」章节。"

