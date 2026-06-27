from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from .models import CitationPack


@dataclass(frozen=True)
class GenerationRequest:
    system: str
    prompt: str
    max_tokens: int = 16000
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""


@runtime_checkable
class LlmClient(Protocol):
    def generate(self, req: GenerationRequest) -> GenerationResult: ...


class EchoLlmClient:
    """确定性、不走网络的 LlmClient,用于测试和兜底接线。

    不传模板时原样回显请求的 prompt;传了模板就用 ``template.format(system=..., prompt=...)``
    渲染。token 数按空白分词的词数计算,结果稳定可复现。
    """

    def __init__(self, *, template: str | None = None) -> None:
        self._template = template

    def generate(self, req: GenerationRequest) -> GenerationResult:
        if self._template is None:
            text = req.prompt
        else:
            text = self._template.format(system=req.system, prompt=req.prompt)
        return GenerationResult(
            text=text,
            model="echo",
            latency_ms=0.0,
            input_tokens=len(req.prompt.split()),
            output_tokens=len(text.split()),
            finish_reason="stop",
        )


def _source_label(docid: str) -> str:
    return "doc" if docid.isdigit() else "pending"


def build_constrained_context(citations: Iterable[CitationPack]) -> str:
    """把引用包渲染成带编号、只含引用片段的上下文块。

    每条引用渲染为一行 ``[n] doc/<docid>《title》#section`` 标题,后跟引用片段,
    块与块之间空一行隔开。模型只能引用这些 ``[n]`` 块。
    """

    blocks: list[str] = []
    for idx, citation in enumerate(citations, start=1):
        if getattr(citation, "start_line", 0):  # 代码引用
            symbol = getattr(citation, "qualified_symbol", "") or ""
            header = f"[{idx}] {citation.file_path}:L{citation.start_line}-{citation.end_line}"
            if symbol:
                header += f" {symbol}"
            quote = _strip_code_header(citation.quote)
        else:
            section = " / ".join(citation.section_path)
            header = (
                f"[{idx}] {_source_label(citation.docid)}/{citation.docid}"
                f"《{citation.title}》#{section}"
            )
            quote = citation.quote
        blocks.append(f"{header}\n{quote}")
    return "\n\n".join(blocks)


def _strip_code_header(quote: str) -> str:
    quote = quote or ""
    if quote.startswith("« "):
        end = quote.find("»")
        if end != -1:
            return quote[end + 1 :].lstrip("\n ")
    return quote
