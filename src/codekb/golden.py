from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import GoldenQuestion
from .retrieval import tokenize

# 前 4 列(id | question | sources | focus)沿用老的 70 行格式。
# 后面用竖线分隔的单元格是可选扩展列(expected_anchors | holdout | paraphrase),
# 老格式的行里没有,直接忽略。
_ROW_RE = re.compile(
    r"^\|\s*([A-Z]+-\d{3})\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|(?P<extra>.*)$"
)
_DOCID_RE = re.compile(r"`?(\d{7,})`?")
_ANCHOR_SPLIT_RE = re.compile(r"[、,;，；]+")
_TRUTHY = {"true", "yes", "y", "1", "holdout", "blind", "盲测", "是", "✓", "✔"}


def load_golden_questions(path: str | Path) -> list[GoldenQuestion]:
    questions: list[GoldenQuestion] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        question_id, question, sources, focus = (part.strip() for part in match.groups()[:4])
        anchors, holdout, paraphrase = _parse_extra_columns(match.group("extra"))
        questions.append(
            GoldenQuestion(
                question_id=question_id,
                question=_strip_markdown(question),
                expected_sources=tuple(_DOCID_RE.findall(sources)),
                focus=_strip_markdown(focus),
                expected_anchors=anchors,
                holdout=holdout,
                paraphrase=paraphrase,
            )
        )
    return questions


def _parse_extra_columns(extra: str) -> tuple[tuple[str, ...], bool, str]:
    """解析可选的扩展单元格:expected_anchors | holdout | paraphrase。

    老的 4 列行没有扩展单元格,返回各项默认值。"""
    cells = [cell.strip() for cell in extra.split("|")]
    while cells and cells[-1] == "":
        cells.pop()
    if not cells:
        return (), False, ""

    anchors_cell = cells[0] if len(cells) > 0 else ""
    holdout_cell = cells[1] if len(cells) > 1 else ""
    paraphrase_cell = cells[2] if len(cells) > 2 else ""

    anchors = tuple(
        _strip_markdown(token)
        for token in _ANCHOR_SPLIT_RE.split(anchors_cell)
        if _strip_markdown(token)
    )
    holdout = _strip_markdown(holdout_cell).lower() in _TRUTHY
    paraphrase = _strip_markdown(paraphrase_cell)
    return anchors, holdout, paraphrase


@dataclass(frozen=True)
class KeywordLeakage:
    overlap_ratio: float
    high_leakage: bool
    shared_tokens: tuple[str, ...] = ()


def detect_keyword_leakage(question: GoldenQuestion, store, *, threshold: float = 0.8) -> KeywordLeakage:
    """衡量问题里的非停用词词元和其期望 atom 词元的重合度。比例越高,说明
    问题文本把答案的关键词泄露了(检索靠字面回声就能命中,而非真正理解),
    于是标记出来待复核 / 改写成盲测变体。"""
    query_tokens = list(dict.fromkeys(tokenize(question.question)))
    if not query_tokens:
        return KeywordLeakage(overlap_ratio=0.0, high_leakage=False, shared_tokens=())

    source_docids = set(question.expected_sources) or None
    atom_tokens: set[str] = set()
    for atom in store.list_atoms(source_docids=source_docids):
        atom_tokens.update(tokenize(atom.text))

    shared = tuple(token for token in query_tokens if token in atom_tokens)
    ratio = len(shared) / len(query_tokens)
    return KeywordLeakage(
        overlap_ratio=round(ratio, 3),
        high_leakage=ratio >= threshold,
        shared_tokens=shared,
    )


def _strip_markdown(text: str) -> str:
    text = text.replace("`", "")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return text.strip()
