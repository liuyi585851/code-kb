from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .llm import build_constrained_context
from .models import CitationPack

REFUSAL_SENTINEL = "NO_SUPPORT"

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]+")
_CONTENT_RE = re.compile(r"[\w一-鿿]")

_SYSTEM_PROMPT = (
    "你是企业知识库的回答助手。必须严格遵守以下规则：\n"
    "1. 只能依据下方提供的、以 [n] 编号的引用块作答，禁止使用任何编号之外的知识。\n"
    "2. 【硬性格式要求】每一句话都必须在句末、句号之前紧跟其依据的引用编号，"
    "如 [1] 或 [1][2]；不允许出现任何不带编号的句子。\n"
    "3. 禁止引用未提供的编号；禁止编造来源或编号。\n"
    "4. 直接输出最终答案本身，不要输出思考过程、解释、前言或多余的客套。\n"
    f"5. 如果提供的引用块不足以支撑回答，只输出 {REFUSAL_SENTINEL}，不要补充任何其它内容。\n"
    "\n"
    "格式示例（务必照此为每句带编号）：\n"
    "DEVICE_SEQ 是平台内置的 int 变量，表示当前设备在本次任务中的数字序号 [1]。"
    "该序号从 0 开始 [1]。"
)


@dataclass(frozen=True)
class CiteCheck:
    ok: bool
    refused: bool
    used_indices: tuple[int, ...]
    out_of_range: tuple[int, ...]
    has_uncited_claim: bool


def build_answer_messages(
    query: str, citations: Iterable[CitationPack]
) -> tuple[str, str]:
    """构造"不引用就拒答"受约束回答所需的 (system, prompt) 二元组。"""

    context = build_constrained_context(citations)
    prompt = (
        f"用户问题：{query}\n\n"
        f"可引用的知识块：\n{context}\n\n"
        f"请依据上述引用块作答，并为每个事实句标注 [n]；"
        f"若无足够依据，只回复 {REFUSAL_SENTINEL}。"
    )
    return _SYSTEM_PROMPT, prompt


def extract_cited_indices(text: str) -> set[int]:
    """返回 ``text`` 中以 ``[n]`` 形式引用到的全部编号集合。"""

    return {int(match) for match in _CITATION_RE.findall(text)}


def _has_uncited_claim(text: str) -> bool:
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        residual = _CITATION_RE.sub("", sentence)
        if not _CONTENT_RE.search(residual):
            # 这句除了编号标记之外没有实质内容。
            continue
        if not _CITATION_RE.search(sentence):
            return True
    return False


def enforce_cite_or_die(text: str, n_citations: int) -> CiteCheck:
    """校验 ``text`` 要么干脆拒答,要么引用合法。

    - text 去空白后等于 NO_SUPPORT -> 拒答。
    - 出现 1..n_citations 之外的 [n] -> 越界。
    - 有实质内容的句子缺 [n] -> 存在未引用的断言。
    - 每个内容句都带合法 [n] -> 通过。
    """

    if text.strip() == REFUSAL_SENTINEL:
        return CiteCheck(
            ok=False,
            refused=True,
            used_indices=(),
            out_of_range=(),
            has_uncited_claim=False,
        )

    used = tuple(sorted(extract_cited_indices(text)))
    out_of_range = tuple(i for i in used if i < 1 or i > n_citations)
    has_uncited = _has_uncited_claim(text)
    # 只有编号、没有正文的回答(比如光一个 "[1]")虽然带了引用却没内容,
    # 不能当作有效的带引用回答放过。
    has_substance = bool(_CONTENT_RE.search(_CITATION_RE.sub("", text)))
    ok = has_substance and bool(used) and not out_of_range and not has_uncited
    return CiteCheck(
        ok=ok,
        refused=False,
        used_indices=used,
        out_of_range=out_of_range,
        has_uncited_claim=has_uncited,
    )
