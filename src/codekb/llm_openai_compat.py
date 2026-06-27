"""兼容 OpenAI chat/completions 的 LLM 适配器。

适配任意兼容 OpenAI 的端点 —— 比如托管的模型 API
(``https://llm.example.com/v1``)或团队的模型代理网关。
这是本项目规划的生成路径(任意兼容 OpenAI 的模型代理,详见 docs/p0-dependencies.md)。
纯 ``urllib`` 实现,不依赖 SDK。测试时可注入自定义 ``opener``,无需走网络。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

from .llm import GenerationRequest, GenerationResult

DEFAULT_MODEL = "example-model"


class OpenAICompatLlmClient:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
        timeout_seconds: float = 30.0,
        temperature: float | None = None,
        opener: Any | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint is required for OpenAICompatLlmClient")
        self.endpoint = self._chat_url(endpoint)
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self._opener = opener

    @staticmethod
    def _chat_url(endpoint: str) -> str:
        base = endpoint.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OpenAICompatLlmClient | None":
        env = os.environ if env is None else env
        endpoint = (env.get("CODEKB_LLM_ENDPOINT") or "").strip()
        if not endpoint:
            return None
        kwargs: dict[str, Any] = {}
        max_tokens = (env.get("CODEKB_LLM_MAX_TOKENS") or "").strip()
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        timeout = (env.get("CODEKB_LLM_TIMEOUT") or "").strip()
        if timeout:
            kwargs["timeout_seconds"] = float(timeout)
        temperature = (env.get("CODEKB_LLM_TEMPERATURE") or "").strip()
        if temperature:
            kwargs["temperature"] = float(temperature)
        return cls(
            endpoint=endpoint,
            api_key=(env.get("CODEKB_LLM_API_KEY") or "").strip(),
            model=(env.get("CODEKB_LLM_MODEL") or DEFAULT_MODEL).strip(),
            **kwargs,
        )

    def generate(self, req: GenerationRequest) -> GenerationResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.prompt},
            ],
            "max_tokens": req.max_tokens or self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Content-Length": str(len(data)),
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=data, method="POST", headers=headers)

        start = time.perf_counter()
        try:
            payload = self._open(request, req.timeout_seconds)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenAI-compatible LLM request failed") from exc
        latency_ms = (time.perf_counter() - start) * 1000.0
        return self._parse(payload, latency_ms)

    def _open(self, request: urllib.request.Request, timeout_override: float) -> Any:
        timeout = timeout_override or self.timeout_seconds
        if self._opener is not None:
            response = self._opener.open(request, timeout=timeout)
        else:
            response = urllib.request.urlopen(request, timeout=timeout)
        with response:
            return json.loads(response.read().decode("utf-8"))

    def _parse(self, payload: Any, latency_ms: float) -> GenerationResult:
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAI-compatible LLM response is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("OpenAI-compatible LLM response has no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return GenerationResult(
            text=str(message.get("content", "") or ""),
            model=str(payload.get("model") or self.model),
            latency_ms=latency_ms,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            finish_reason=str(first.get("finish_reason", "") or ""),
        )
