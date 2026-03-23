"""Claude Code cleanroom 的最小模型客户端抽象。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

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
                model=self.model_name,
                instructions=instructions,
                input=input_items,
                previous_response_id=previous_response_id,
                store=True,
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=True,
            )
        except Exception as exc:  # noqa: BLE001 - SDK/网络/服务错误统一折叠
            raise ModelClientError(f"responses.create failed: {type(exc).__name__}: {exc}") from exc

        return _normalize_response(response)


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
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "tools": tools,
                "previous_response_id": previous_response_id,
            }
        )
        if not self._turns:
            raise ModelClientError("fake model has no scripted response left")
        return self._turns.pop(0)


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
