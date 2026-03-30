"""Claude Code cleanroom 的最小 permission gate。

这个文件对应 `docs/claude-code/claude-code-todo.md` 的 “Phase 4 第 1 点”:
- 给 `bash` 和 `edit` 加最简单的 confirm/deny 交互

关键代码链:
CLI / runtime 准备执行受控工具 -> permission gate 询问用户 -> allow / deny -> tools 层决定是否真正执行

对应《claude-code-study.md》的:
- 5.5 Safety / Boundaries
- 9.1 第一阶段必须有

当前取舍:
- 先只拦截高风险的 `bash` 和 `edit`，不提前实现 allowlist / denylist 或复杂 policy。
- 先做终端内同步确认，默认输入不是 yes 就视为 deny；这样能先把控制层闭环钉住。
- Web UI 和 live 写入工具开放还不是这一条的目标，后续再把同一套 gate 接到更完整的 runtime。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


CONTROLLED_TOOL_NAMES = ("bash", "edit")


@dataclass(frozen=True)
class PermissionDecision:
    """最小权限决策结果。

    这里先只保留 cleanroom 当前阶段真正需要的两个信号:
    - `allowed`: 是否放行
    - `reason`: 给事件流和终端摘要复用的简短说明
    """

    allowed: bool
    reason: str


class PermissionGate:
    """抽象权限门。

    当前只需要一个同步 `confirm / deny` 接口，但先把边界单独抽出来，
    这样 Phase 4 后续加 allowlist / denylist、plan mode 或 hooks 时，
    不必把控制逻辑再塞回工具执行函数里。
    """

    def confirm_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision:
        return PermissionDecision(allowed=True, reason=f"tool `{tool_name}` does not require approval")


class InteractivePermissionGate(PermissionGate):
    """最小交互式 permission gate。

    当前只面向 CLI:
    - `bash`: 展示待执行命令
    - `edit`: 展示目标路径和替换片段

    这里故意不用更复杂的全屏 UI 或多步确认，先把“执行前明确获得用户批准”
    这条控制链跑通。
    """

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._input_fn = input_fn
        self._output_fn = output_fn

    def confirm_tool_use(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision:
        if tool_name not in CONTROLLED_TOOL_NAMES:
            return PermissionDecision(allowed=True, reason=f"tool `{tool_name}` does not require approval")

        prompt = _build_permission_prompt(tool_name, tool_input)
        if self._output_fn is not None:
            self._output_fn(prompt)
            question = "Allow this tool run? [y/N]: "
        else:
            question = prompt + "\nAllow this tool run? [y/N]: "

        try:
            raw_answer = self._input_fn(question)
        except EOFError:
            return PermissionDecision(
                allowed=False,
                reason=f"user did not provide confirmation for `{tool_name}`",
            )

        normalized = raw_answer.strip().lower()
        if normalized in {"y", "yes"}:
            return PermissionDecision(
                allowed=True,
                reason=f"user approved `{tool_name}`",
            )

        return PermissionDecision(
            allowed=False,
            reason=f"user denied `{tool_name}`",
        )


def _build_permission_prompt(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "bash":
        command = str(tool_input.get("command", ""))
        return "\n".join(
            [
                f"Permission required for tool `{tool_name}`.",
                f"command: {command}",
            ]
        )

    if tool_name == "edit":
        path = str(tool_input.get("path", ""))
        old_text = _preview_text(str(tool_input.get("old_text", "")))
        new_text = _preview_text(str(tool_input.get("new_text", "")))
        return "\n".join(
            [
                f"Permission required for tool `{tool_name}`.",
                f"path: {path}",
                f"old_text: {old_text}",
                f"new_text: {new_text}",
            ]
        )

    return f"Permission required for tool `{tool_name}`."


def _preview_text(value: str, *, limit: int = 80) -> str:
    compact = value.replace("\n", "\\n")
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
