import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.llm import GenerationRequest
from codekb.llm_openai_compat import OpenAICompatLlmClient


def _make_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _ChatHandlerFactory:
    def __init__(self, *, status=200, content="生成答案 [1]"):
        self.status = status
        self.content = content
        self.requests = []

    def handler(self):
        factory = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                factory.requests.append(
                    {"path": self.path, "payload": payload, "auth": self.headers.get("authorization")}
                )
                if factory.status >= 400:
                    self.send_response(factory.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                body = json.dumps(
                    {
                        "model": payload.get("model"),
                        "choices": [
                            {"message": {"role": "assistant", "content": factory.content}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        return Handler


class OpenAICompatLlmClientTests(unittest.TestCase):
    def test_generate_parses_chat_completion(self):
        factory = _ChatHandlerFactory(content="回归测试需在发布前完成 [1]")
        server, thread = _make_server(factory.handler())
        try:
            client = OpenAICompatLlmClient(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="k",
                model="example-model",
            )
            result = client.generate(GenerationRequest(system="s", prompt="p"))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual(result.text, "回归测试需在发布前完成 [1]")
        self.assertEqual(result.model, "example-model")
        self.assertEqual(result.input_tokens, 11)
        self.assertEqual(result.output_tokens, 7)
        self.assertGreaterEqual(result.latency_ms, 0.0)
        req = factory.requests[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["auth"], "Bearer k")
        self.assertEqual([m["role"] for m in req["payload"]["messages"]], ["system", "user"])

    def test_http_error_raises_runtime_error(self):
        factory = _ChatHandlerFactory(status=500)
        server, thread = _make_server(factory.handler())
        try:
            client = OpenAICompatLlmClient(endpoint=f"http://127.0.0.1:{server.server_port}", model="m")
            with self.assertRaises(RuntimeError):
                client.generate(GenerationRequest(system="s", prompt="p"))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_temperature_from_env_is_sent_in_request(self):
        client = OpenAICompatLlmClient.from_env(
            env={"CODEKB_LLM_ENDPOINT": "http://x/v1", "CODEKB_LLM_TEMPERATURE": "0"}
        )
        self.assertEqual(client.temperature, 0.0)
        factory = _ChatHandlerFactory()
        server, thread = _make_server(factory.handler())
        try:
            c = OpenAICompatLlmClient(
                endpoint=f"http://127.0.0.1:{server.server_port}/v1", model="m", temperature=0.0
            )
            c.generate(GenerationRequest(system="s", prompt="p"))
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        self.assertEqual(factory.requests[0]["payload"].get("temperature"), 0.0)

    def test_from_env_none_without_endpoint(self):
        self.assertIsNone(OpenAICompatLlmClient.from_env(env={}))

    def test_from_env_builds_client(self):
        client = OpenAICompatLlmClient.from_env(
            env={
                "CODEKB_LLM_ENDPOINT": "https://llm.example.com/v1",
                "CODEKB_LLM_API_KEY": "sk",
                "CODEKB_LLM_MODEL": "example-pro",
            }
        )
        self.assertIsNotNone(client)
        self.assertEqual(client.model, "example-pro")
        self.assertTrue(client.endpoint.endswith("/chat/completions"))


class LlmProviderResolverTests(unittest.TestCase):
    def test_default_provider_resolves_openai_compat(self):
        from codekb.service import _llm_client_from_env

        keys = ("CODEKB_LLM_PROVIDER", "CODEKB_LLM_ENDPOINT", "ANTHROPIC_API_KEY")
        old = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            # 默认 provider + 什么都没配 -> 返回 None(降级,不抛错)
            self.assertIsNone(_llm_client_from_env())
            # 默认 provider + 配了 endpoint -> 返回 OpenAI 兼容客户端
            os.environ["CODEKB_LLM_ENDPOINT"] = "http://proxy.internal/v1"
            client = _llm_client_from_env()
            self.assertIsNotNone(client)
            self.assertEqual(type(client).__name__, "OpenAICompatLlmClient")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
