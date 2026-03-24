from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from claude_code.config import OpenAISettings
from claude_code.model_client import LiveOpenAIClient


class _FakeResponse:
    id = "resp_123"
    output = []
    output_text = "ok"
    status = "completed"
    usage = {"total_tokens": 1}


class _FakeResponsesAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse()


class _FakeOpenAIClient:
    def __init__(self, *, api_key: str, base_url: str | None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.responses = _FakeResponsesAPI()


class LiveOpenAIClientTest(unittest.TestCase):
    def test_create_response_sets_store_true_for_previous_response_chains(self) -> None:
        fake_openai_module = types.SimpleNamespace(OpenAI=_FakeOpenAIClient)

        with patch.dict(sys.modules, {"openai": fake_openai_module}):
            client = LiveOpenAIClient(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5.4",
                    base_url="https://api.openai.com/v1",
                )
            )

            result = client.create_response(
                instructions="test instructions",
                input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
                tools=[],
                previous_response_id="resp_prev",
            )

        self.assertEqual(result.response_id, "resp_123")
        self.assertEqual(client._client.responses.calls[0]["store"], True)
        self.assertEqual(client._client.responses.calls[0]["previous_response_id"], "resp_prev")


if __name__ == "__main__":
    unittest.main()
