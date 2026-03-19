"""Claude Code cleanroom 的最小 runtime。

这个文件只实现《claude-code-todo.md》里 Phase 2 第 2 点要求的
gather -> act -> verify 主循环。

设计边界对应《claude-code-study.md》的 4. 核心运行循环:
- 先把三段循环显式化，证明 CLI 已经不只是“存 session”
- 暂时不用复杂 planning，也不提前实现统一事件流
- act 阶段只生成最小行动结论，为后续工具层接入预留连接点
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .session_store import SessionRecord


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

    def render_summary(self) -> str:
        return "\n".join(
            [
                "loop_phases: gather -> act -> verify",
                f"gather_summary: {self.gather.summary}",
                f"act_strategy: {self.act.strategy}",
                f"act_next_action: {self.act.next_action}",
                f"verify_status: {self.verify.status}",
                f"verify_summary: {self.verify.summary}",
            ]
        )


def gather_context(record: SessionRecord, workspace_root: Path) -> GatherPhaseResult:
    """收集这轮 runtime 需要的最小上下文。

    这里先只拿 session 里的近期任务和当前工作目录，保持最小闭环。
    真正的文件读取、命令输出和规则文件拼装，会放到后续 context builder 条目里扩展。
    """
    latest_task = record.user_tasks[-1]["content"] if record.user_tasks else ""
    recent_tasks = [item["content"] for item in record.user_tasks[-3:]]
    summary = (
        f"loaded {len(record.user_tasks)} task(s) from session and prepared "
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
    """
    strategy, next_action = _choose_strategy(gathered.latest_task)
    assistant_message = (
        f"已接收任务“{gathered.latest_task}”。"
        f"当前最小 runtime 判定下一步应当：{next_action}。"
    )
    summary = f"selected {strategy} for the latest task"
    return ActPhaseResult(
        strategy=strategy,
        assistant_message=assistant_message,
        next_action=next_action,
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
    is_ready = bool(gathered.latest_task.strip()) and bool(acted.next_action.strip())
    if is_ready:
        return VerifyPhaseResult(
            status="loop-ready",
            summary=(
                "completed one minimal runtime pass; the next todo can wire this act step "
                "to event records and real tools"
            ),
        )

    return VerifyPhaseResult(
        status="loop-incomplete",
        summary="runtime could not produce a valid next action from the current session",
    )


def run_core_loop(record: SessionRecord, workspace_root: Path) -> LoopResult:
    """执行一轮最小 gather -> act -> verify 主循环。

    这里故意只跑一轮，不做自动多轮迭代:
    - 先把 Claude Code 的核心节拍搭出来
    - 再在后续条目中逐步接事件流、工具执行和更强验证
    """
    gathered = gather_context(record, workspace_root)
    acted = act_on_context(gathered)
    verified = verify_action(gathered, acted)
    return LoopResult(gather=gathered, act=acted, verify=verified)
