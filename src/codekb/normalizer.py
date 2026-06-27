from __future__ import annotations

import re

from .models import NormalizedDocument, NormalizedSection, RawDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EXCESS_BLANK_RE = re.compile(r"\n{3,}")


class DocumentNormalizer:
    def normalize(self, raw: RawDocument) -> NormalizedDocument:
        text = _clean_common(raw.body)
        warnings: list[str] = []
        if raw.content_type.upper() == "TXDOC":
            warnings.append("txdoc_text_noise_high")
            text = _clean_txdoc_noise(text)

        sections = tuple(_split_sections(text, fallback_title=raw.title))
        if not sections:
            sections = (
                NormalizedSection(
                    path=(raw.title,),
                    anchor=_slug(raw.title),
                    text=text.strip(),
                ),
            )

        return NormalizedDocument(raw.docid, raw.title, sections, tuple(warnings))


def _clean_common(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_BR_RE.sub("\n", text)
    text = _IMAGE_RE.sub(lambda m: f"[attachment:image title={m.group(1)!r} url={m.group(2)!r}]", text)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("&nbsp;", " ")
    text = _expand_markdown_tables(text)
    text = _EXCESS_BLANK_RE.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _expand_markdown_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines) and _is_table_row(lines[index]) and _is_table_separator(lines[index + 1]):
            headers = _split_table_cells(lines[index])
            index += 2
            rows: list[str] = []
            while index < len(lines) and _is_table_row(lines[index]):
                cells = _split_table_cells(lines[index])
                pairs = []
                for header, cell in zip(headers, cells, strict=False):
                    header = header.strip()
                    cell = cell.strip()
                    if header and cell:
                        pairs.append(f"{header}={cell}")
                if pairs:
                    rows.append("表格行：" + "；".join(pairs))
                index += 1
            for row in rows:
                output.append(row)
                output.append("")
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not _is_table_row(stripped):
        return False
    return all(set(cell.strip()) <= {"-", ":"} and "-" in cell for cell in _split_table_cells(stripped))


def _split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _clean_txdoc_noise(text: str) -> str:
    # TXDOC 抽取经常会把中文字符重复一遍。这里保守处理:
    # 只压缩明确的两字重复和明显的模板标记。
    text = re.sub(r"([一二三四五六七八九十])\1、", r"\1、", text)
    text = re.sub(r"[●○■]{2,}", "\n", text)
    text = re.sub(r"([A-Za-z0-9])\1{3,}", r"\1", text)
    return _EXCESS_BLANK_RE.sub("\n\n", text).strip()


def _split_sections(text: str, fallback_title: str) -> list[NormalizedSection]:
    sections: list[NormalizedSection] = []
    current_path: list[str] = [fallback_title]
    current_lines: list[str] = []
    current_anchor = _slug(fallback_title)

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            _append_section(sections, current_path, current_anchor, current_lines)
            level = len(match.group(1))
            title = match.group(2).strip()
            current_path = current_path[: max(level - 1, 0)] + [title]
            current_anchor = _slug(" ".join(current_path))
            current_lines = []
        else:
            current_lines.append(line)

    _append_section(sections, current_path, current_anchor, current_lines)
    return sections


def _append_section(
    sections: list[NormalizedSection],
    path: list[str],
    anchor: str,
    lines: list[str],
) -> None:
    text = "\n".join(lines).strip()
    if text:
        sections.append(NormalizedSection(tuple(path), anchor, text))


def _slug(text: str) -> str:
    slug = re.sub(r"\s+", "-", text.strip().lower())
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff_-]+", "", slug)
    return slug[:120] or "section"
