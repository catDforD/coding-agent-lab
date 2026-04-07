"""Claude Code cleanroom 的最小模型客户端抽象。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

from .config import OpenAISettings


class ModelClientError(RuntimeError):
    """模型调用失败。"""


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurnResult:
    response_id: str | None
    output_text: str
    tool_calls: list[ToolRequest]
    output_items: list[dict[str, Any]]
    finish_reason: str
    usage: dict[str, Any] | None


@dataclass(frozen=True)
class ModelTextDeltaEvent:
    delta: str


@dataclass(frozen=True)
class ModelTurnCompletedEvent:
    result: ModelTurnResult


class ModelClient(Protocol):
    model_name: str

    def create_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ModelTurnResult: ...


class LiveOpenAIClient:
    """使用官方 OpenAI SDK 的 Responses API。"""

    def __init__(self, settings: OpenAISettings) -> None:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - 取决于本地环境
            raise ModelClientError(
                "openai package is not installed; run `uv sync` inside reproductions/claude-code"
            ) from exc

        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )
        self.model_name = settings.model
        self._base_url = settings.base_url

    def create_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ModelTurnResult:
        try:
            response = self._client.responses.create(
                **self._response_request_kwargs(
                    instructions=instructions,
                    input_items=input_items,
                    tools=tools,
                    previous_response_id=previous_response_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - SDK/网络/服务错误统一折叠
            raise ModelClientError(f"responses.create failed: {type(exc).__name__}: {exc}") from exc

        normalized = _normalize_response(response)
        if _is_empty_completed_response(normalized):
            raise _empty_completed_response_error(self.model_name, self._base_url)
        return normalized

    def stream_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> Iterator[ModelTextDeltaEvent | ModelTurnCompletedEvent]:
        accumulator = _StreamTurnAccumulator()

        try:
            with self._client.responses.stream(
                **self._response_request_kwargs(
                    instructions=instructions,
                    input_items=input_items,
                    tools=tools,
                    previous_response_id=previous_response_id,
                )
            ) as stream:
                saw_completed = False

                for event in stream:
                    accumulator.observe(event)
                    event_type = str(_value(event, "type", ""))
                    if event_type == "response.output_text.delta":
                        yield ModelTextDeltaEvent(delta=str(_value(event, "delta", "")))
                        continue
                    if event_type != "response.completed":
                        continue

                    result = accumulator.build_result(_value(event, "response"))
                    if _is_empty_completed_response(result):
                        raise _empty_completed_response_error(self.model_name, self._base_url)
                    saw_completed = True
                    yield ModelTurnCompletedEvent(result=result)

                if not saw_completed:
                    raise ModelClientError("responses.stream ended before `response.completed`")
        except ModelClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK/网络/服务错误统一折叠
            raise ModelClientError(f"responses.stream failed: {type(exc).__name__}: {exc}") from exc

    def _response_request_kwargs(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "instructions": instructions,
            "input": input_items,
            "previous_response_id": previous_response_id,
            "store": True,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }


class FakeModelClient:
    """测试用替身客户端。"""

    def __init__(self, turns: list[ModelTurnResult], *, model_name: str = "fake-responses-model") -> None:
        self._turns = list(turns)
        self.model_name = model_name
        self.requests: list[dict[str, Any]] = []

    def create_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ModelTurnResult:
        self._record_request(
            instructions=instructions,
            input_items=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
        )
        return self._next_turn()

    def stream_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> Iterator[ModelTextDeltaEvent | ModelTurnCompletedEvent]:
        self._record_request(
            instructions=instructions,
            input_items=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
        )
        turn = self._next_turn()
        if turn.output_text:
            yield ModelTextDeltaEvent(delta=turn.output_text)
        yield ModelTurnCompletedEvent(result=turn)

    def _record_request(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
    ) -> None:
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "tools": tools,
                "previous_response_id": previous_response_id,
            }
        )

    def _next_turn(self) -> ModelTurnResult:
        if not self._turns:
            raise ModelClientError("fake model has no scripted response left")
        return self._turns.pop(0)


class _StreamTurnAccumulator:
    def __init__(self) -> None:
        self._output_items: dict[int, dict[str, Any]] = {}
        self._text_chunks: list[str] = []

    def observe(self, event: Any) -> None:
        event_type = str(_value(event, "type", ""))
        if event_type == "response.output_item.added":
            self._set_output_item(_value(event, "output_index"), _value(event, "item"))
            return
        if event_type == "response.output_item.done":
            self._set_output_item(_value(event, "output_index"), _value(event, "item"))
            return
        if event_type == "response.output_text.delta":
            self._text_chunks.append(str(_value(event, "delta", "")))
            return
        if event_type == "response.output_text.done":
            self._set_message_text(
                output_index=_value(event, "output_index"),
                content_index=_value(event, "content_index"),
                text=str(_value(event, "text", "")),
            )
            return
        if event_type == "response.function_call_arguments.delta":
            self._append_function_call_arguments(
                output_index=_value(event, "output_index"),
                delta=str(_value(event, "delta", "")),
            )
            return
        if event_type == "response.function_call_arguments.done":
            self._set_function_call_arguments(
                output_index=_value(event, "output_index"),
                arguments=str(_value(event, "arguments", "")),
            )

    def build_result(self, response: Any) -> ModelTurnResult:
        output_items = self._ordered_output_items()
        response_payload = {
            "id": _value(response, "id"),
            "status": _value(response, "status", "completed"),
            "usage": _value(response, "usage"),
            "output_text": "".join(self._text_chunks) or _value(response, "output_text", ""),
            "output": output_items or _item_list(_value(response, "output", [])),
        }
        return _normalize_response(response_payload)

    def _ordered_output_items(self) -> list[dict[str, Any]]:
        return [self._output_items[index] for index in sorted(self._output_items)]

    def _set_output_item(self, output_index: Any, item: Any) -> None:
        index = _optional_int(output_index)
        payload = _to_dict(item)
        if index is None or payload is None:
            return
        self._output_items[index] = payload

    def _set_message_text(self, *, output_index: Any, content_index: Any, text: str) -> None:
        item = self._get_item(output_index)
        index = _optional_int(content_index)
        if item is None or item.get("type") != "message" or index is None:
            return

        content = item.setdefault("content", [])
        while len(content) <= index:
            content.append({"type": "output_text", "text": ""})
        if isinstance(content[index], dict):
            content[index]["text"] = text
            content[index].setdefault("type", "output_text")

    def _append_function_call_arguments(self, *, output_index: Any, delta: str) -> None:
        item = self._get_item(output_index)
        if item is None or item.get("type") != "function_call":
            return
        item["arguments"] = str(item.get("arguments", "")) + delta

    def _set_function_call_arguments(self, *, output_index: Any, arguments: str) -> None:
        item = self._get_item(output_index)
        if item is None or item.get("type") != "function_call":
            return
        item["arguments"] = arguments

    def _get_item(self, output_index: Any) -> dict[str, Any] | None:
        index = _optional_int(output_index)
        if index is None:
            return None
        return self._output_items.get(index)


def _normalize_response(response: Any) -> ModelTurnResult:
    output_items = list(_item_list(_value(response, "output", [])))
    tool_calls: list[ToolRequest] = []

    for item in output_items:
        if _value(item, "type") != "function_call":
            continue

        raw_arguments = _value(item, "arguments", "{}")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ModelClientError(f"model returned invalid tool arguments JSON: {raw_arguments}") from exc

        tool_calls.append(
            ToolRequest(
                call_id=str(_value(item, "call_id")),
                name=str(_value(item, "name")),
                arguments=arguments,
            )
        )

    output_text = str(_value(response, "output_text", "") or _extract_output_text(output_items))
    finish_reason = "tool_calls" if tool_calls else str(_value(response, "status", "completed"))
    usage = _to_dict(_value(response, "usage"))

    return ModelTurnResult(
        response_id=_optional_str(_value(response, "id")),
        output_text=output_text.strip(),
        tool_calls=tool_calls,
        output_items=[_to_dict(item) or {"type": str(_value(item, "type", ""))} for item in output_items],
        finish_reason=finish_reason,
        usage=usage,
    )


def _extract_output_text(output_items: list[Any]) -> str:
    texts: list[str] = []
    for item in output_items:
        item_type = _value(item, "type")
        if item_type == "message":
            for content in _item_list(_value(item, "content", [])):
                content_type = _value(content, "type")
                if content_type == "output_text":
                    texts.append(str(_value(content, "text", "")))
                elif content_type == "refusal":
                    texts.append(str(_value(content, "refusal", "")))
        elif item_type == "reasoning":
            continue
    return "\n".join(text for text in texts if text.strip())


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _item_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value)


def _to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": str(value)}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_empty_completed_response(result: ModelTurnResult) -> bool:
    return (
        result.finish_reason == "completed"
        and not result.output_text.strip()
        and not result.tool_calls
        and not result.output_items
    )


def _empty_completed_response_error(model_name: str, base_url: str | None) -> ModelClientError:
    backend = base_url or "https://api.openai.com/v1"
    return ModelClientError(
        "Responses API returned `completed` with no visible text, no tool calls, "
        f"and no output items for model `{model_name}` via `{backend}`; "
        "the configured backend likely does not implement OpenAI Responses text output correctly"
    )
