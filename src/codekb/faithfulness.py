"""可注入的"忠实度"评判器。

quality.py 里旧的 score_answer 忠实度检查只是个子串判断:``citation.quote[:80] in answer``。
当答案本身就是把各条引用原文拼接而成时,这个检查对每条引用都必然成立——
"引用子串自我证明为 1.0"——于是夹带进答案的捏造句子根本不会拉低分数。

本模块给出可插拔的 :class:`FaithfulnessJudge` 协议,外加一个确定性实现:它**逐句**评分——
某句话的实词若没被所有引用原文的并集覆盖,就算作无出处,因此捏造的、引用里找不到的句子
会把分数压到 1.0 以下。另有一个占位的 LLM 评判器,仅留出注入接口,不绑定任何 SDK。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+|(?<=[.])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


@dataclass(frozen=True)
class JudgeVerdict:
    score: float
    supported: tuple[bool, ...]
    rationale: str = ""
    judge: str = "deterministic"


@runtime_checkable
class FaithfulnessJudge(Protocol):
    def judge(self, *, answer: str, citations) -> "JudgeVerdict":  # pragma: no cover - protocol
        ...


class DeterministicFaithfulnessJudge:
    """基于词覆盖的忠实度评判器——确定性,不依赖网络/SDK。

    做法:
      1. 把所有引用 ``quote`` 的文本合并成一个词表。
      2. 把答案切成句子,逐句分词。
      3. 一句话里至少有 ``coverage_threshold`` 比例的实词出现在词表中,才算"有出处"。
         若某句引入了所有引用都没有的词(即捏造),覆盖率达不到阈值,判为无出处。
      4. ``score`` = 有出处的句数 / 总句数。

    效果:纯由引用原文拼成的答案得 1.0;一旦追加一句引用之外的话,分数必然下降(< 1.0),
    从而堵住"子串自我证明"的漏洞。没有任何引用时词表为空,所有实质句子都判无出处,
    评判器返回低分而非报错。

    局限:这只是词面覆盖,是忠实度的**近似**,而非真正的蕴含判断。打乱语序或改写、
    但复用了相同词(尤其 CJK 单字)的文本,仍可能拿到高分。升级到 n-gram 覆盖或
    NLI/LLM 评判属于后续工作,届时需要重新标定 ``min_faithfulness`` 门槛。
    """

    def __init__(self, *, coverage_threshold: float = 0.6) -> None:
        if not 0.0 < coverage_threshold <= 1.0:
            raise ValueError("coverage_threshold must be in (0, 1]")
        self.coverage_threshold = coverage_threshold

    def judge(self, *, answer: str, citations) -> JudgeVerdict:
        corpus: set[str] = set()
        for citation in citations or ():
            quote = getattr(citation, "quote", "") or ""
            corpus.update(_tokens(quote))

        sentences = [segment.strip() for segment in _SENTENCE_SPLIT.split(answer or "") if segment.strip()]
        if not sentences:
            return JudgeVerdict(score=0.0, supported=(), rationale="empty answer", judge="deterministic")

        supported: list[bool] = []
        for sentence in sentences:
            tokens = _tokens(sentence)
            if not tokens:
                # 只有标点的碎片:视作天然有出处,不扣分
                supported.append(True)
                continue
            covered = sum(1 for token in tokens if token in corpus)
            ratio = covered / len(tokens)
            supported.append(ratio >= self.coverage_threshold)

        score = sum(supported) / len(supported)
        supported_count = sum(supported)
        rationale = (
            f"{supported_count}/{len(supported)} sentences covered by citations "
            f"(threshold={self.coverage_threshold})"
        )
        return JudgeVerdict(
            score=score,
            supported=tuple(supported),
            rationale=rationale,
            judge="deterministic",
        )


class LlmFaithfulnessJudge:
    """占位的 LLM 评判器,只留出注入接口。

    本次改动不绑定真实 SDK;``judge`` 直接抛 ``NotImplementedError``,把接线显式化,
    留待后续改动接入具体的 ``client``。
    """

    def __init__(self, client) -> None:
        self.client = client

    def judge(self, *, answer: str, citations) -> JudgeVerdict:
        raise NotImplementedError("LlmFaithfulnessJudge is a placeholder; no SDK bound in this PR")


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]
