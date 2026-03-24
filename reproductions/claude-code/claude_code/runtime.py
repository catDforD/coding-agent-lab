"""Claude Code cleanroom runtime。

当前 runtime 同时支持两条路径:
- live 模式: 使用 OpenAI Responses API 驱动最小多轮只读代理
- tool-direct 模式: 继续保留 Phase 2 的显式工具调试入口
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model_client import ModelClient, ModelClientError
from .session_store import SessionEvent, SessionRecord, USER_MESSAGE
from .tools import (
    READ_ONLY_TOOL_NAMES,
    ToolCall,
    execute_named_tool,
    execute_tool_call,
    live_tool_schemas,
    plan_tool_call,
    tool_output_for_model,
)


LIVE_AGENT_PROMPT = """You are a Claude Code cleanroom reproduction agent running in a terminal.
You may use only these read-only tools: read_file, search, git_status.
Do not invent file contents or repository facts that you have not inspected.
Use tools when you need concrete repo information; otherwise answer directly.
Keep the final answer concise and useful for a terminal user."""


@dataclass
class GatherPhaseResult:
    latest_task: str
    recent_tasks: list[str]
    resume_transcript: str
    summary: str


@dataclass
class ActPhaseResult:
    mode: str
    strategy: str
    model: str
    step_count: int
    executed_tools: list[str]
    finish_reason: str
    status: str
    final_output: str
    summary: str


@dataclass
class VerifyPhaseResult:
    status: str
    summary: str


@dataclass
class LoopResult:
    gather: GatherPhaseResult
    act: ActPhaseResult
    verify: VerifyPhaseResult
    emitted_events: list[SessionEvent]

    def render_summary(self) -> str:
        event_kinds = ",".join(event.kind for event in self.emitted_events) or "<none>"
        tool_names = ",".join(self.act.executed_tools) or "<none>"
        lines = [
            f"mode: {self.act.mode}",
            "loop_phases: gather -> act -> verify",
            f"gather_summary: {self.gather.summary}",
            f"act_strategy: {self.act.strategy}",
            f"model: {self.act.model}",
            f"step_count: {self.act.step_count}",
            f"executed_tools: {tool_names}",
            f"finish_reason: {self.act.finish_reason}",
            f"verify_status: {self.verify.status}",
            f"verify_summary: {self.verify.summary}",
            f"emitted_event_count: {len(self.emitted_events)}",
            f"emitted_event_kinds: {event_kinds}",
            "assistant_response:",
            self.act.final_output or "<none>",
        ]
        return "\n".join(lines)


def gather_context(record: SessionRecord, workspace_root: Path) -> GatherPhaseResult:
    recent_user_events = record.recent_events(kind=USER_MESSAGE, limit=3)
    latest_task = recent_user_events[-1].payload.get("content", "") if recent_user_events else ""
    recent_tasks = [str(event.payload.get("content", "")) for event in recent_user_events]

    all_recent_events = record.recent_events(limit=20)
    transcript_events = list(all_recent_events)
    if transcript_events and transcript_events[-1].kind == USER_MESSAGE:
        transcript_events = transcript_events[:-1]

    resume_transcript = render_transcript(transcript_events)
    summary = (
        f"loaded {len(recent_user_events)} recent user message(s) from {len(record.events)} total event(s) "
        f"and prepared workspace context at {workspace_root.name}"
    )
    return GatherPhaseResult(
        latest_task=latest_task,
        recent_tasks=recent_tasks,
        resume_transcript=resume_transcript,
        summary=summary,
    )


def render_transcript(events: list[SessionEvent]) -> str:
    lines: list[str] = []
    for event in events:
        payload = event.payload
        if event.kind == "user_message":
            lines.append(f"User: {payload.get('content', '')}")
        elif event.kind == "tool_call":
            tool_name = payload.get("tool_name", "")
            step_index = payload.get("step_index")
            prefix = f"Tool call step {step_index}" if step_index is not None else "Tool call"
            lines.append(f"{prefix}: {tool_name} {json.dumps(payload.get('tool_input', {}), ensure_ascii=False)}")
        elif event.kind == "tool_result":
            tool_name = payload.get("tool_name", "")
            status = payload.get("status", "")
            step_index = payload.get("step_index")
            prefix = f"Tool result step {step_index}" if step_index is not None else "Tool result"
            lines.append(
                f"{prefix}: {tool_name} status={status} "
                f"{json.dumps(payload.get('tool_output', {}), ensure_ascii=False)}"
            )
        elif event.kind == "model_response":
            lines.append(f"Assistant: {payload.get('content', '')}")
    return "\n".join(lines)


def act_on_context(
    gathered: GatherPhaseResult,
    record: SessionRecord,
    workspace_root: Path,
    *,
    tool_direct: bool,
    max_steps: int,
    model_client: ModelClient | None = None,
) -> tuple[ActPhaseResult, list[SessionEvent]]:
    if tool_direct:
        return _act_via_tool_direct(gathered, record, workspace_root)
    if model_client is None:
        raise ValueError("model_client is required in live mode")
    return _act_via_live_agent(
        gathered,
        record,
        workspace_root,
        model_client=model_client,
        max_steps=max_steps,
    )


def _act_via_tool_direct(
    gathered: GatherPhaseResult,
    record: SessionRecord,
    workspace_root: Path,
) -> tuple[ActPhaseResult, list[SessionEvent]]:
    planned_call: ToolCall = plan_tool_call(gathered.latest_task)
    executed = execute_tool_call(planned_call, workspace_root)
    emitted = [
        record.add_tool_call(tool_name=planned_call.tool_name, tool_input=planned_call.tool_input, step_index=1),
        record.add_tool_result(
            tool_name=planned_call.tool_name,
            status=executed.status,
            tool_output=executed.tool_output,
            step_index=1,
        ),
        record.add_model_response(
            executed.assistant_message,
            strategy=planned_call.strategy,
            next_action=planned_call.next_action,
            mode="tool-direct",
            model="tool-direct",
            finish_reason="tool-direct-complete",
            step_index=1,
        ),
    ]
    act = ActPhaseResult(
        mode="tool-direct",
        strategy=planned_call.strategy,
        model="tool-direct",
        step_count=1,
        executed_tools=[planned_call.tool_name],
        finish_reason="tool-direct-complete",
        status=executed.status,
        final_output=executed.assistant_message,
        summary=f"executed {planned_call.tool_name} via deterministic tool-direct mode",
    )
    return act, emitted


def _act_via_live_agent(
    gathered: GatherPhaseResult,
    record: SessionRecord,
    workspace_root: Path,
    *,
    model_client: ModelClient,
    max_steps: int,
) -> tuple[ActPhaseResult, list[SessionEvent]]:
    emitted: list[SessionEvent] = []
    executed_tools: list[str] = []
    running_input = [_build_initial_user_input(gathered, workspace_root)]

    for step_index in range(1, max_steps + 1):
        try:
            turn = model_client.create_response(
                instructions=LIVE_AGENT_PROMPT,
                input_items=running_input,
                tools=live_tool_schemas(),
            )
        except ModelClientError as exc:
            return (
                ActPhaseResult(
                    mode="live",
                    strategy="live-responses-agent",
                    model=model_client.model_name,
                    step_count=step_index - 1,
                    executed_tools=executed_tools,
                    finish_reason="api-error",
                    status="error",
                    final_output="",
                    summary=str(exc),
                ),
                emitted,
            )

        if turn.tool_calls:
            running_input.extend(turn.output_items)
            for call in turn.tool_calls:
                if call.name not in READ_ONLY_TOOL_NAMES:
                    return (
                        ActPhaseResult(
                            mode="live",
                            strategy="live-responses-agent",
                            model=model_client.model_name,
                            step_count=step_index,
                            executed_tools=executed_tools,
                            finish_reason="invalid-tool-call",
                            status="error",
                            final_output="",
                            summary=f"model requested unsupported tool `{call.name}`",
                        ),
                        emitted,
                    )

                result = execute_named_tool(call.name, call.arguments, workspace_root)
                executed_tools.append(call.name)
                emitted.append(
                    record.add_tool_call(
                        tool_name=call.name,
                        tool_input=call.arguments,
                        step_index=step_index,
                        call_id=call.call_id,
                    )
                )
                emitted.append(
                    record.add_tool_result(
                        tool_name=call.name,
                        status=result.status,
                        tool_output=result.tool_output,
                        step_index=step_index,
                        call_id=call.call_id,
                    )
                )
                running_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(
                            tool_output_for_model(call.name, result),
                            ensure_ascii=False,
                        ),
                    }
                )
            continue

        final_output = turn.output_text or "模型结束了当前回合，但没有返回可见文本。"
        emitted.append(
            record.add_model_response(
                final_output,
                strategy="live-responses-agent",
                next_action="current turn complete",
                mode="live",
                model=model_client.model_name,
                finish_reason=turn.finish_reason,
                step_index=step_index,
                usage=turn.usage,
            )
        )
        return (
            ActPhaseResult(
                mode="live",
                strategy="live-responses-agent",
                model=model_client.model_name,
                step_count=step_index,
                executed_tools=executed_tools,
                finish_reason=turn.finish_reason,
                status="ok",
                final_output=final_output,
                summary=f"completed live agent turn in {step_index} step(s)",
            ),
            emitted,
        )

    return (
        ActPhaseResult(
            mode="live",
            strategy="live-responses-agent",
            model=model_client.model_name,
            step_count=max_steps,
            executed_tools=executed_tools,
            finish_reason="max-steps-reached",
            status="error",
            final_output="",
            summary=f"agent reached max steps ({max_steps}) without a final answer",
        ),
        emitted,
    )


def _build_initial_user_input(gathered: GatherPhaseResult, workspace_root: Path) -> dict[str, Any]:
    sections = [
        f"Workspace root: {workspace_root}",
        f"Current task:\n{gathered.latest_task}",
    ]
    if gathered.resume_transcript:
        sections.append(f"Recent session transcript:\n{gathered.resume_transcript}")
    return {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "\n\n".join(sections),
            }
        ],
    }


def verify_action(gathered: GatherPhaseResult, acted: ActPhaseResult) -> VerifyPhaseResult:
    if not gathered.latest_task.strip():
        return VerifyPhaseResult(
            status="loop-incomplete",
            summary="runtime could not find a user task in the current session",
        )

    if acted.mode == "tool-direct":
        status = "completed" if acted.status == "ok" else "loop-needs-attention"
        summary = (
            "completed one deterministic tool-direct pass"
            if acted.status == "ok"
            else "tool-direct execution failed and needs attention"
        )
        return VerifyPhaseResult(status=status, summary=summary)

    if acted.finish_reason == "completed":
        return VerifyPhaseResult(
            status="completed",
            summary="live Responses agent returned a final answer",
        )
    if acted.finish_reason == "api-error":
        return VerifyPhaseResult(
            status="api-error",
            summary=acted.summary,
        )
    if acted.finish_reason == "invalid-tool-call":
        return VerifyPhaseResult(
            status="invalid-tool-call",
            summary=acted.summary,
        )
    if acted.finish_reason == "max-steps-reached":
        return VerifyPhaseResult(
            status="max-steps-reached",
            summary=acted.summary,
        )

    return VerifyPhaseResult(
        status="loop-needs-attention",
        summary=acted.summary,
    )


def run_core_loop(
    record: SessionRecord,
    workspace_root: Path,
    *,
    tool_direct: bool,
    max_steps: int,
    model_client: ModelClient | None = None,
) -> LoopResult:
    gathered = gather_context(record, workspace_root)
    acted, emitted_events = act_on_context(
        gathered,
        record,
        workspace_root,
        tool_direct=tool_direct,
        max_steps=max_steps,
        model_client=model_client,
    )
    verified = verify_action(gathered, acted)
    return LoopResult(
        gather=gathered,
        act=acted,
        verify=verified,
        emitted_events=emitted_events,
    )
