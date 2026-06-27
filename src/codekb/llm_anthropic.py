from __future__ import annotations

import os
import time
from typing import Any

from .llm import GenerationRequest, GenerationResult

DEFAULT_MODEL = "claude-opus-4-8"


class LlmUnavailableError(RuntimeError):
    """Anthropic SDK 或凭证不可用时抛出。

    调用方(如 answer.py 的生成式路径)捕获后会回退到确定性的抽取式答案,
    而不是让请求直接失败。
    """


class AnthropicLlmClient:
    """Anthropic Messages API 的厂商适配器。

    SDK 在 ``generate`` 内部惰性导入,因此导入本模块不强依赖 ``anthropic`` 包。
    测试时可注入预先构造好的 ``client``,无需走网络或真实凭证。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    @classmethod
    def from_env(cls) -> "AnthropicLlmClient | None":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        model = os.environ.get("CODEKB_LLM_MODEL", DEFAULT_MODEL)
        return cls(api_key=api_key, model=model)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # 未安装 SDK
            raise LlmUnavailableError("anthropic SDK is not installed") from exc
        if not self._api_key:
            raise LlmUnavailableError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate(self, req: GenerationRequest) -> GenerationResult:
        client = self._ensure_client()
        start = time.perf_counter()
        message = client.messages.create(
            model=self._model,
            system=req.system,
            messages=[{"role": "user", "content": req.prompt}],
            max_tokens=req.max_tokens,
            thinking={"type": "adaptive"},
        )
        latency_ms = (time.perf_counter() - start) * 1000.0

        usage = getattr(message, "usage", None)
        return GenerationResult(
            text=_extract_text(message),
            model=getattr(message, "model", self._model) or self._model,
            latency_ms=latency_ms,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            finish_reason=getattr(message, "stop_reason", "") or "",
        )


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)
