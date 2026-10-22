"""模型网关短暂断连时，单题继续执行并计入真实请求次数。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIConnectionError

from research_agent.integrations.llm import GatewayLLM


class GatewayRetryTests(unittest.TestCase):
    def test_connection_failure_then_success(self):
        class FakeClient:
            calls = 0

            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def create(self, **kwargs):
                FakeClient.calls += 1
                if FakeClient.calls == 1:
                    raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
                return SimpleNamespace(usage=SimpleNamespace(total_tokens=50), model="test-model",
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"answer":"ok"}'))])

        with patch.dict("os.environ", {"PLATFORM_LLM_URL": "https://example.invalid",
                                       "PLATFORM_API_KEY": "test-key", "AGENT_MODEL_NAME": "test-model"}), \
             patch("openai.OpenAI", FakeClient), patch("research_agent.integrations.llm.time.sleep"):
            gateway = GatewayLLM(max_calls=3, token_budget=1000, timeout=20)
            self.assertEqual(gateway.json("chat", {}, max_tokens=10)["answer"], "ok")
            self.assertEqual(gateway.calls, 2)


if __name__ == "__main__":
    unittest.main()
