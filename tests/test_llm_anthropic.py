import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.llm import GenerationRequest
from codekb.llm_anthropic import (
    DEFAULT_MODEL,
    AnthropicLlmClient,
    LlmUnavailableError,
)


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self):
        self.content = [_FakeBlock("生成的答案 [1]。")]
        self.usage = _FakeUsage(123, 45)
        self.model = "claude-opus-4-8"
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage()


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessages()


class AnthropicLlmClientTests(unittest.TestCase):
    def test_generate_passes_required_params_and_no_forbidden_params(self):
        fake = _FakeAnthropicClient()
        client = AnthropicLlmClient(client=fake)
        req = GenerationRequest(system="SYS", prompt="PROMPT", max_tokens=8000)

        result = client.generate(req)

        self.assertEqual(len(fake.messages.calls), 1)
        kwargs = fake.messages.calls[0]
        self.assertEqual(kwargs["model"], "claude-opus-4-8")
        self.assertEqual(kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(kwargs["max_tokens"], 8000)
        self.assertEqual(kwargs["system"], "SYS")
        self.assertEqual(
            kwargs["messages"], [{"role": "user", "content": "PROMPT"}]
        )
        for forbidden in ("temperature", "top_p", "budget_tokens"):
            self.assertNotIn(forbidden, kwargs)
        # thinking 里不能夹带 budget_tokens
        self.assertNotIn("budget_tokens", kwargs["thinking"])

    def test_generate_maps_text_usage_and_latency(self):
        client = AnthropicLlmClient(client=_FakeAnthropicClient())
        result = client.generate(GenerationRequest(system="s", prompt="p"))
        self.assertEqual(result.text, "生成的答案 [1]。")
        self.assertEqual(result.model, "claude-opus-4-8")
        self.assertEqual(result.input_tokens, 123)
        self.assertEqual(result.output_tokens, 45)
        self.assertEqual(result.finish_reason, "end_turn")
        self.assertGreaterEqual(result.latency_ms, 0.0)

    def test_default_model_constant(self):
        self.assertEqual(DEFAULT_MODEL, "claude-opus-4-8")

    def test_custom_model_passed_through(self):
        fake = _FakeAnthropicClient()
        client = AnthropicLlmClient(client=fake, model="claude-opus-4-7")
        client.generate(GenerationRequest(system="s", prompt="p"))
        self.assertEqual(fake.messages.calls[0]["model"], "claude-opus-4-7")

    def test_from_env_returns_none_without_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(AnthropicLlmClient.from_env())

    def test_from_env_reads_key_and_model(self):
        env = {"ANTHROPIC_API_KEY": "sk-test", "CODEKB_LLM_MODEL": "claude-opus-4-7"}
        with mock.patch.dict("os.environ", env, clear=True):
            client = AnthropicLlmClient.from_env()
        self.assertIsNotNone(client)
        self.assertEqual(client._model, "claude-opus-4-7")

    def test_from_env_defaults_model(self):
        with mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True
        ):
            client = AnthropicLlmClient.from_env()
        self.assertEqual(client._model, DEFAULT_MODEL)

    def test_missing_sdk_raises_unavailable(self):
        client = AnthropicLlmClient(api_key="sk-test")  # 没有注入 client
        # 强制让 import anthropic 失败。
        with mock.patch.dict("sys.modules", {"anthropic": None}):
            with self.assertRaises(LlmUnavailableError):
                client.generate(GenerationRequest(system="s", prompt="p"))

    def test_missing_key_raises_unavailable_when_sdk_present(self):
        client = AnthropicLlmClient(api_key=None)
        fake_sdk = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"anthropic": fake_sdk}):
            with self.assertRaises(LlmUnavailableError):
                client.generate(GenerationRequest(system="s", prompt="p"))


if __name__ == "__main__":
    unittest.main()
