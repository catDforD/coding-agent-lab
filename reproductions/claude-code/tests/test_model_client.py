from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from claude_code.config import OpenAISettings
from claude_code.model_client import (
    LiveOpenAIClient,
    ModelClientError,
    ModelTextDeltaEvent,
    ModelTurnCompletedEvent,
)


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


class _EmptyResponse:
    id = "resp_empty"
    output = []
    output_text = ""
    status = "completed"
    usage = {"total_tokens": 1}


class _FakeEmptyResponsesAPI(_FakeResponsesAPI):
    def create(self, **kwargs: object) -> _EmptyResponse:
        self.calls.append(kwargs)
        return _EmptyResponse()


class _FakeOpenAIClient:
    def __init__(self, *, api_key: str, base_url: str | None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.responses = _FakeResponsesAPI()


class _FakeEmptyOpenAIClient(_FakeOpenAIClient):
    def __init__(self, *, api_key: str, base_url: str | None) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.responses = _FakeEmptyResponsesAPI()


class _FakeStreamContext:
    def __init__(self, events: list[object]) -> None:
        self._events = list(events)

    def __enter__(self) -> "_FakeStreamContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def __iter__(self):
        return iter(self._events)


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

    def test_create_response_raises_when_backend_returns_completed_but_empty(self) -> None:
        fake_openai_module = types.SimpleNamespace(OpenAI=_FakeEmptyOpenAIClient)

        with patch.dict(sys.modules, {"openai": fake_openai_module}):
            client = LiveOpenAIClient(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5.4",
                    base_url="http://example.invalid/v1",
                )
            )

            with self.assertRaises(ModelClientError) as exc_info:
                client.create_response(
                    instructions="test instructions",
                    input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
                    tools=[],
                )

        self.assertIn("returned `completed` with no visible text", str(exc_info.exception))

    def test_stream_response_uses_sse_state_when_completed_payload_is_empty(self) -> None:
        stream_calls: list[dict[str, object]] = []

        class FakeStreamingResponsesAPI:
            def stream(self, **kwargs: object) -> _FakeStreamContext:
                stream_calls.append(kwargs)
                return _FakeStreamContext(
                    [
                        types.SimpleNamespace(
                            type="response.created",
                            response=types.SimpleNamespace(
                                id="resp_stream",
                                output=[],
                                output_text="",
                                status="in_progress",
                                usage=None,
                            ),
                        ),
                        types.SimpleNamespace(
                            type="response.output_item.added",
                            output_index=0,
                            item={"type": "message", "content": [{"type": "output_text", "text": ""}]},
                        ),
                        types.SimpleNamespace(
                            type="response.output_text.delta",
                            output_index=0,
                            content_index=0,
                            delta="你好",
                        ),
                        types.SimpleNamespace(
                            type="response.output_text.delta",
                            output_index=0,
                            content_index=0,
                            delta="！",
                        ),
                        types.SimpleNamespace(
                            type="response.output_item.done",
                            output_index=0,
                            item={"type": "message", "content": [{"type": "output_text", "text": "你好！"}]},
                        ),
                        types.SimpleNamespace(
                            type="response.completed",
                            response=types.SimpleNamespace(
                                id="resp_stream",
                                output=[],
                                output_text="",
                                status="completed",
                                usage={"total_tokens": 7},
                            ),
                        ),
                    ]
                )

        class FakeStreamingOpenAIClient:
            def __init__(self, *, api_key: str, base_url: str | None) -> None:
                self.api_key = api_key
                self.base_url = base_url
                self.responses = FakeStreamingResponsesAPI()

        fake_openai_module = types.SimpleNamespace(OpenAI=FakeStreamingOpenAIClient)

        with patch.dict(sys.modules, {"openai": fake_openai_module}):
            client = LiveOpenAIClient(
                OpenAISettings(
                    api_key="test-key",
                    model="gpt-5.4",
                    base_url="http://example.invalid/v1",
                )
            )

            events = list(
                client.stream_response(
                    instructions="test instructions",
                    input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
                    tools=[],
                    previous_response_id="resp_prev",
                )
            )

        self.assertEqual(stream_calls[0]["store"], True)
        self.assertEqual(stream_calls[0]["previous_response_id"], "resp_prev")
        self.assertEqual([type(event) for event in events], [ModelTextDeltaEvent, ModelTextDeltaEvent, ModelTurnCompletedEvent])
        self.assertEqual([event.delta for event in events[:-1]], ["你好", "！"])
        completed = events[-1]
        self.assertEqual(completed.result.response_id, "resp_stream")
        self.assertEqual(completed.result.output_text, "你好！")
        self.assertEqual(completed.result.finish_reason, "completed")
        self.assertEqual(completed.result.usage, {"total_tokens": 7})


if __name__ == "__main__":
    unittest.main()
