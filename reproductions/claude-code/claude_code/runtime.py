"""Claude Code cleanroom 的最小 runtime。

这个文件现在覆盖《claude-code-todo.md》里的:
- Phase 2 第 2 点: `gather -> act -> verify` 主循环
- Phase 2 第 3 点: 统一事件流结构
- Phase 2 第 4 点: 接入最小工具集

设计边界对应《claude-code-study.md》的 4. 核心运行循环、5.1 Tool Use、5.3 Memory / Context:
- 把 gather / act / verify 三段循环显式化，证明 CLI 已经不只是“存 session”
- 用统一事件流记录本轮的模型与工具观察，给后续 context builder 留稳定接口
- 先接入最小真实工具，但不提前实现更复杂的 planning、permission gate 和 checkpoint
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .session_store import SessionEvent, SessionRecord, USER_MESSAGE
from .tools import ToolCall, ToolExecutionResult, execute_tool_call, plan_tool_call


@dataclass
class GatherPhaseResult:
    latest_task: str
    recent_tasks: list[str]
    summary: str


@dataclass
class ActPhaseResult:
    strategy: str
    assistant_message: str
    next_action: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: dict[str, Any]
    tool_status: str
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
        event_kinds = ",".join(event.kind for event in self.emitted_events)
        return "\n".join(
            [
                "loop_phases: gather -> act -> verify",
                f"gather_summary: {self.gather.summary}",
                f"act_strategy: {self.act.strategy}",
                f"act_tool_name: {self.act.tool_name}",
                f"act_tool_status: {self.act.tool_status}",
                f"act_next_action: {self.act.next_action}",
                f"verify_status: {self.verify.status}",
                f"verify_summary: {self.verify.summary}",
                f"emitted_event_count: {len(self.emitted_events)}",
                f"emitted_event_kinds: {event_kinds}",
            ]
        )


def gather_context(record: SessionRecord, workspace_root: Path) -> GatherPhaseResult:
    """收集这轮 runtime 需要的最小上下文。

    对应学习文档“5.3 Memory / Context”的第一步，这里开始明确基于统一事件流取数。
    当前仍然只拿最近用户消息和工作目录，保持最小闭环。
    真正的文件读取、命令输出和规则文件拼装，会放到后续 context builder 条目里扩展。
    """
    recent_user_events = record.recent_events(kind=USER_MESSAGE, limit=3)
    latest_task = recent_user_events[-1].payload.get("content", "") if recent_user_events else ""
    recent_tasks = [str(event.payload.get("content", "")) for event in recent_user_events]
    summary = (
        f"loaded {len(recent_user_events)} recent user message(s) from {len(record.events)} total event(s) "
        f"and prepared "
        f"workspace context at {workspace_root.name}"
    )
    return GatherPhaseResult(
        latest_task=latest_task,
        recent_tasks=recent_tasks,
        summary=summary,
    )


def act_on_context(gathered: GatherPhaseResult, workspace_root: Path) -> ActPhaseResult:
    """根据 gather 结果产出当前这一轮的行动结论。

    按学习文档“4. 核心运行循环”和“5.1 Tool Use”的结论，act 阶段需要
    真正落到一个结构化工具调用上，而不只是给出抽象下一步。

    当前仍然保持最小版本:
    - 先由轻量规则把任务文本解析成一个工具调用
    - 再立刻执行这个工具
    - 把结果折叠回统一事件流

    这样可以先证明最小工具集已经进入 Claude Code cleanroom 的主循环，
    后续再把“谁来决定调用哪个工具”替换成更真实的模型决策层。
    """
    planned_call: ToolCall = plan_tool_call(gathered.latest_task)
    executed: ToolExecutionResult = execute_tool_call(planned_call, workspace_root)
    summary = f"executed {planned_call.tool_name} with strategy {planned_call.strategy}"
    return ActPhaseResult(
        strategy=planned_call.strategy,
        assistant_message=executed.assistant_message,
        next_action=planned_call.next_action,
        tool_name=planned_call.tool_name,
        tool_input=planned_call.tool_input,
        tool_output=executed.tool_output,
        tool_status=executed.status,
        summary=summary,
    )


def verify_action(gathered: GatherPhaseResult, acted: ActPhaseResult) -> VerifyPhaseResult:
    """验证这一轮循环是否形成了可继续的闭环。

    当前 verify 只检查三件事:
    - gather 阶段拿到了用户任务
    - act 阶段产出了可读的行动结论
    - 这轮循环已经给后续工具层留出了明确连接点

    这还是最小 cleanroom 验证，不等同于后续“重新跑测试 / 检查 git diff”的强验证。
    """
    is_ready = (
        bool(gathered.latest_task.strip())
        and bool(acted.next_action.strip())
        and bool(acted.tool_name.strip())
        and bool(acted.tool_status.strip())
    )
    if is_ready:
        return VerifyPhaseResult(
            status="loop-ready" if acted.tool_status == "ok" else "loop-needs-attention",
            summary=(
                "completed one minimal runtime pass with a real tool call; session now records tool input and output, "
                "and later phases can layer permission gate, checkpoint and richer context on top"
            ),
        )

    return VerifyPhaseResult(
        status="loop-incomplete",
        summary="runtime could not produce a valid next action from the current session",
    )


def emit_loop_events(record: SessionRecord, acted: ActPhaseResult) -> list[SessionEvent]:
    """把本轮 runtime 的关键观察写回统一事件流。

    关键代码链:
    act 结果 -> tool_call -> tool_result -> model_response -> session JSON

    这里先按最小顺序写三类非用户事件，满足 Phase 2 第 3 点。
    后续如果加入真实 LLM、多轮工具调用或流式输出，可以继续沿这条链路细化。
    """

    emitted = [
        record.add_tool_call(tool_name=acted.tool_name, tool_input=acted.tool_input),
        record.add_tool_result(
            tool_name=acted.tool_name,
            status=acted.tool_status,
            tool_output=acted.tool_output,
        ),
        record.add_model_response(
            acted.assistant_message,
            strategy=acted.strategy,
            next_action=acted.next_action,
        ),
    ]
    return emitted


def run_core_loop(record: SessionRecord, workspace_root: Path) -> LoopResult:
    """执行一轮最小 gather -> act -> verify 主循环。

    这里故意只跑一轮，不做自动多轮迭代:
    - 先把 Claude Code 的核心节拍和最小工具执行链搭出来
    - 再在后续条目中逐步补 permission gate、checkpoint 和更强验证
    """
    gathered = gather_context(record, workspace_root)
    acted = act_on_context(gathered, workspace_root)
    verified = verify_action(gathered, acted)
    emitted_events = emit_loop_events(record, acted)
    return LoopResult(
        gather=gathered,
        act=acted,
        verify=verified,
        emitted_events=emitted_events,
    )
