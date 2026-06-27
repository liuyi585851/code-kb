from __future__ import annotations

import re

from .code_location import parse_code_location
from .models import CitationPack, RetrievalResult

_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|[0-9]{2,}|[一-鿿]{2,}")


def build_citation_pack(result: RetrievalResult, *, max_chars: int = 280) -> tuple[CitationPack, ...]:
    query_terms = _TERM_RE.findall(result.query or "")
    packs: list[CitationPack] = []
    for item in result.top_atoms:
        draft = item.atom.draft
        loc = parse_code_location(draft)
        # 代码原子在 source_documents 里没有标题(那是文档语料才有的),
        # 用文件名兜底,让引用至少能标明出自哪个文件。
        title = draft.source_title
        if not title and loc:
            title = loc.file_path.rsplit("/", 1)[-1]
        terms = query_terms or list(item.matched_terms)
        packs.append(
            CitationPack(
                atom_id=item.atom.atom_id,
                docid=draft.source_docid,
                title=title,
                anchor=draft.source_anchor,
                section_path=draft.section_path,
                quote=_window(_strip_code_header(draft.text), terms, max_chars),
                score=item.score,
                file_path=loc.file_path if loc else "",
                start_line=loc.start_line if loc else 0,
                end_line=loc.end_line if loc else 0,
                language=loc.language if loc else "",
                repo_id=loc.repo_id if loc else "",
                qualified_symbol=loc.qualified_symbol if loc else "",
            )
        )
    return tuple(packs)


def _strip_code_header(text: str) -> str:
    """去掉片段开头那段自描述的代码头注 «...»。出处已记录在引用的
    file_path/start_line/symbol 字段里,无需重复;放在 _trim 之前做,
    免得很深的路径占满截断长度后把头注残留进引用。"""
    if text.startswith("« "):
        end = text.find("»")
        if end != -1:
            return text[end + 1 :].lstrip("\n ")
    return text


def _trim(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _window(text: str, terms: list[str], max_chars: int) -> str:
    """从片段中截取一段不超过 max_chars 的窗口,并对准命中查询里最有信息量的
    那个词,让引用展示真正相关的那一行(比如 ``1001`` 的定义),而不是片段开头
    (通常是文件的头部注释)。一个词都没命中时,退回从开头截取。"""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    low = flat.lower()
    best_pos = -1
    best_rank: tuple[int, int] | None = None
    for term in terms:
        tl = term.lower()
        count = low.count(tl)
        if count == 0:
            continue
        # 出现次数越少、长度越长的词定位越准(比如优先用 "1001" 而非 "authsdk")。
        rank = (count, -len(tl))
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_pos = low.find(tl)
    if best_pos <= 0:
        return flat[: max_chars - 1].rstrip() + "..."
    start = max(0, best_pos - max_chars // 4)
    end = min(len(flat), start + max_chars)
    return ("..." if start > 0 else "") + flat[start:end].strip() + ("..." if end < len(flat) else "")

