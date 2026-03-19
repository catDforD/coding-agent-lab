"""Claude Code cleanroom 的最小 runtime。

这个文件现在同时覆盖《claude-code-todo.md》里的:
- Phase 2 第 2 点: `gather -> act -> verify` 主循环
- Phase 2 第 3 点: 统一事件流结构

设计边界对应《claude-code-study.md》的 4. 核心运行循环 和 5.3 Memory / Context:
- 先把三段循环显式化，证明 CLI 已经不只是“存 session”
- 用统一事件流记录本轮的模型与工具观察，给后续 context builder 留稳定接口
- 仍然不提前实现真实工具集，避免抢跑下一个 todo 条目
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .session_store import SessionEvent, SessionRecord, USER_MESSAGE


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
    tool_input: dict[str, str]
    tool_output: dict[str, str]
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


def _choose_strategy(latest_task: str) -> tuple[str, str]:
    normalized = latest_task.strip()
    read_keywords = ("解释", "阅读", "查看", "梳理", "总结", "分析")
    write_keywords = ("修复", "修改", "重构", "补充", "实现")

    if any(keyword in normalized for keyword in read_keywords):
        return (
            "inspect-code",
            "先读取相关文件并整理关键代码链，再决定是否需要进一步动作",
        )

    if any(keyword in normalized for keyword in write_keywords):
        return (
            "change-code",
            "先定位相关文件，再进入修改和验证步骤",
        )

    return (
        "general-task",
        "先补足任务上下文，再决定是偏只读分析还是偏写入执行",
    )


def act_on_context(gathered: GatherPhaseResult) -> ActPhaseResult:
    """根据 gather 结果产出当前这一轮的行动结论。

    按学习文档“4. 核心运行循环”的结论，这里需要有一个明确的“下一步要做什么”。
    但这次只做最小版本，所以先用轻量规则给出行动方向，而不是提前引入复杂 planning。

    为了先把统一事件流跑起来，这里显式产出一对最小工具事件:
    `runtime.next_action_router` 的调用与结果。
    它不是 Phase 2 第 4 点里的真实工具，而是一个 cleanroom 过渡层，
    用来证明 session 已经能承载“模型 -> 工具 -> 结果”的结构化链路。
    """
    strategy, next_action = _choose_strategy(gathered.latest_task)
    tool_name = "runtime.next_action_router"
    tool_input = {"latest_task": gathered.latest_task}
    tool_output = {
        "strategy": strategy,
        "next_action": next_action,
    }
    assistant_message = (
        f"已接收任务“{gathered.latest_task}”。"
        f"当前最小 runtime 判定下一步应当：{next_action}。"
    )
    summary = f"selected {strategy} for the latest task via {tool_name}"
    return ActPhaseResult(
        strategy=strategy,
        assistant_message=assistant_message,
        next_action=next_action,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
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
    is_ready = bool(gathered.latest_task.strip()) and bool(acted.next_action.strip()) and bool(acted.tool_name.strip())
    if is_ready:
        return VerifyPhaseResult(
            status="loop-ready",
            summary=(
                "completed one minimal runtime pass; session now records model and tool events, "
                "and the next todo can swap the stub tool for real tools"
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
            status="ok",
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
    - 先把 Claude Code 的核心节拍搭出来
    - 再在后续条目中逐步把 stub tool 换成真实工具执行和更强验证
    """
    gathered = gather_context(record, workspace_root)
    acted = act_on_context(gathered)
    verified = verify_action(gathered, acted)
    emitted_events = emit_loop_events(record, acted)
    return LoopResult(
        gather=gathered,
        act=acted,
        verify=verified,
        emitted_events=emitted_events,
    )
