"""Claude Code cleanroom 的最小工具层。

这个文件对应 `docs/claude-code/claude-code-todo.md` 的 Phase 2 第 4 点:
- 接入 `read_file`、`search`、`edit`、`bash`、`git_status`

关键代码链:
runtime 解析任务文本 -> ToolCall -> execute_tool_call -> 文件系统 / subprocess -> 结构化 tool_result

对应《claude-code-study.md》的:
- 5.1 Tool Use
- 5.5 Safety / Boundaries
- 9.1 第一阶段必须有

当前取舍:
- 先做一个最小、可替换的工具执行层，不提前实现 permission gate 和 checkpoint。
- 任务解析只支持少量显式命令格式，目的是先把“真实工具接入事件流”这件事钉住。
- 所有文件路径都限制在 workspace 内，给后续控制层留清晰边界。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .permissions import PermissionGate

READ_ONLY_TOOL_NAMES = ("read_file", "search", "git_status")


@dataclass
class ToolCall:
    """最小工具调用描述。

    当前 runtime 还没有真实模型，所以先把“模型会决定调用哪个工具”退化成
    基于任务文本的显式解析结果。这样后续要替换成真实 LLM 决策时，
    只需要替换规划层，不需要重写执行层。
    """

    tool_name: str
    tool_input: dict[str, Any]
    strategy: str
    next_action: str


@dataclass
class ToolExecutionResult:
    """统一的工具执行结果。

    这里先固定成 Claude Code session 目前稳定需要的最小外壳:
    - `status`: `ok` / `error`
    - `tool_output`: 结构化结果，直接进入 session 的 `tool_result`
    - `assistant_message`: 给当前最小 runtime 用来生成可读摘要
    """

    status: str
    tool_output: dict[str, Any]
    assistant_message: str
    summary: str


def plan_tool_call(task: str) -> ToolCall:
    """把用户任务折叠成一个最小工具调用。

    当前只支持显式命令格式，保持 cleanroom 最小实现边界:
    - `read_file <path>` / `读取文件 <path>`
    - `search <query>` / `搜索 <query>`
    - `edit <path> -- <old> -- <new>` / `编辑 <path> -- <old> -- <new>`
    - `bash <command>` / `执行命令 <command>`
    - `git_status` / `查看 git 状态`

    如果任务还不是这种格式，就退回到 `search`，先提供一个最小可执行的检索动作。
    这和学习文档“先 gather，再 act”的结论一致，也避免现在就伪造复杂 planning。
    """

    normalized = task.strip()
    lowered = normalized.lower()

    if lowered in {"git_status", "git status", "查看 git 状态", "查看git状态"}:
        return ToolCall(
            tool_name="git_status",
            tool_input={},
            strategy="inspect-workspace-state",
            next_action="先查看当前工作区状态，再决定后续读取、修改还是验证",
        )

    for prefix in ("read_file ", "读取文件 "):
        if normalized.startswith(prefix):
            path = normalized[len(prefix) :].strip()
            return ToolCall(
                tool_name="read_file",
                tool_input={"path": path},
                strategy="inspect-file",
                next_action="先读取目标文件内容，确认后续是否需要搜索、修改或解释",
            )

    for prefix in ("search ", "搜索 "):
        if normalized.startswith(prefix):
            query = normalized[len(prefix) :].strip()
            return ToolCall(
                tool_name="search",
                tool_input={"query": query},
                strategy="search-codebase",
                next_action="先在仓库里搜索相关线索，再缩小到具体文件",
            )

    for prefix in ("bash ", "执行命令 "):
        if normalized.startswith(prefix):
            command = normalized[len(prefix) :].strip()
            return ToolCall(
                tool_name="bash",
                tool_input={"command": command},
                strategy="run-command",
                next_action="先运行命令收集观察结果，再决定是否继续迭代",
            )

    for prefix in ("edit ", "编辑 "):
        if normalized.startswith(prefix):
            body = normalized[len(prefix) :].strip()
            parts = body.split(" -- ", 2)
            if len(parts) != 3:
                raise ValueError(
                    "edit task must use 'edit <path> -- <old> -- <new>' format"
                )
            path, old_text, new_text = parts
            return ToolCall(
                tool_name="edit",
                tool_input={
                    "path": path.strip(),
                    "old_text": _decode_inline_text(old_text),
                    "new_text": _decode_inline_text(new_text),
                },
                strategy="change-file",
                next_action="先按最小补丁修改目标文件，再把结果交给后续验证步骤",
            )

    return ToolCall(
        tool_name="search",
        tool_input={"query": normalized},
        strategy="search-codebase",
        next_action="当前任务还不是显式工具命令，先做一次仓库搜索来补足上下文",
    )


def execute_tool_call(
    call: ToolCall,
    workspace_root: Path,
    *,
    permission_gate: PermissionGate | None = None,
) -> ToolExecutionResult:
    """执行一个最小工具调用。

    这里把 runtime 和具体工具逻辑拆开，是为了对齐学习文档 5.1 的结论:
    Claude Code 的关键不是把 shell 命令塞进 prompt，而是把工具调用协议化。
    后续要加 permission gate、hooks、checkpoint，都应该围绕这里继续展开。
    """

    return execute_named_tool(
        call.tool_name,
        call.tool_input,
        workspace_root,
        permission_gate=permission_gate,
    )


def execute_named_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    workspace_root: Path,
    *,
    permission_gate: PermissionGate | None = None,
) -> ToolExecutionResult:
    handlers = {
        "read_file": _run_read_file,
        "search": _run_search,
        "edit": _run_edit,
        "bash": _run_bash,
        "git_status": _run_git_status,
    }
    if tool_name not in handlers:
        raise ValueError(f"unsupported tool: {tool_name}")

    # Phase 4 先把写入和命令执行纳入最小 permission gate，后续再继续扩成策略模块。
    if permission_gate is not None:
        decision = permission_gate.confirm_tool_use(tool_name, tool_input)
        if not decision.allowed:
            return ToolExecutionResult(
                status="denied",
                tool_output={
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "permission": {
                        "status": "denied",
                        "reason": decision.reason,
                    },
                },
                assistant_message=(
                    f"工具 `{tool_name}` 未执行：{decision.reason}。"
                    "当前最小 control layer 会在这里停止，不继续触发真实写入或命令执行。"
                ),
                summary=f"permission denied for tool {tool_name}",
            )

    handler = handlers[tool_name]
    try:
        return handler(workspace_root, tool_input)
    except Exception as exc:  # noqa: BLE001 - 这里先统一折叠成最小 tool_result
        return ToolExecutionResult(
            status="error",
            tool_output={
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            assistant_message=(
                f"工具 `{tool_name}` 执行失败：{type(exc).__name__}: {exc}。"
                "当前最小 runtime 已把失败写入事件流，后续可以继续补权限、重试和恢复策略。"
            ),
            summary=f"tool {tool_name} failed with {type(exc).__name__}",
        )


def live_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read one UTF-8 text file inside the current workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, relative to the workspace root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search",
            "description": "Search the workspace for a text query using ripgrep when available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Plain text or regex query to search for in the workspace.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "git_status",
            "description": "Collect `git status --short` from the current workspace.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]


def tool_output_for_model(tool_name: str, result: ToolExecutionResult) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": result.status,
        "output": result.tool_output,
    }


def _decode_inline_text(value: str) -> str:
    return value.replace("\\n", "\n")


def _resolve_workspace_path(workspace_root: Path, raw_path: str) -> Path:
    """把工具输入路径限制在当前 workspace 内。

    这一步先做最小边界检查，而不是完整的权限系统:
    - 允许相对路径和位于 workspace 内的绝对路径
    - 禁止逃逸到 workspace 之外
    """

    root = workspace_root.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace root: {raw_path}") from exc

    return candidate


def _run_read_file(workspace_root: Path, tool_input: dict[str, Any]) -> ToolExecutionResult:
    path = _resolve_workspace_path(workspace_root, str(tool_input["path"]))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {path.relative_to(workspace_root.resolve())}")

    content = path.read_text(encoding="utf-8")
    relative_path = str(path.relative_to(workspace_root.resolve()))
    return ToolExecutionResult(
        status="ok",
        tool_output={"path": relative_path, "content": content},
        assistant_message=f"已读取 `{relative_path}`，可以继续解释内容或基于结果决定下一步。",
        summary=f"read file {relative_path}",
    )


def _run_search(workspace_root: Path, tool_input: dict[str, Any]) -> ToolExecutionResult:
    query = str(tool_input["query"])
    command = ["rg", "-n", "--no-heading", "--color", "never", query, "."]
    if shutil.which("rg") is None:
        command = ["grep", "-R", "-n", query, "."]

    completed = subprocess.run(
        command,
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )

    matches = completed.stdout.strip()
    if completed.returncode not in {0, 1}:
        return ToolExecutionResult(
            status="error",
            tool_output={
                "query": query,
                "matches": matches,
                "stderr": completed.stderr.strip(),
                "returncode": completed.returncode,
            },
            assistant_message=f"搜索 `{query}` 时出错，需要先处理命令执行失败。",
            summary=f"search command failed for query {query}",
        )

    match_lines = [line for line in matches.splitlines() if line.strip()]
    return ToolExecutionResult(
        status="ok",
        tool_output={"query": query, "matches": match_lines, "match_count": len(match_lines)},
        assistant_message=(
            f"已在 workspace 中搜索 `{query}`，找到 {len(match_lines)} 条结果。"
        ),
        summary=f"searched codebase for {query}",
    )


def _run_edit(workspace_root: Path, tool_input: dict[str, Any]) -> ToolExecutionResult:
    path = _resolve_workspace_path(workspace_root, str(tool_input["path"]))
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"file not found: {path.relative_to(workspace_root.resolve())}")

    old_text = str(tool_input["old_text"])
    new_text = str(tool_input["new_text"])
    if not old_text:
        raise ValueError("edit requires a non-empty old_text")

    original = path.read_text(encoding="utf-8")
    if old_text not in original:
        raise ValueError("old_text not found in target file")

    updated = original.replace(old_text, new_text, 1)
    path.write_text(updated, encoding="utf-8")
    relative_path = str(path.relative_to(workspace_root.resolve()))
    return ToolExecutionResult(
        status="ok",
        tool_output={
            "path": relative_path,
            "replacements": 1,
            "old_text": old_text,
            "new_text": new_text,
        },
        assistant_message=f"已按最小替换规则更新 `{relative_path}`，可以继续做验证或查看 diff。",
        summary=f"edited file {relative_path}",
    )


def _run_bash(workspace_root: Path, tool_input: dict[str, Any]) -> ToolExecutionResult:
    command = str(tool_input["command"])
    completed = subprocess.run(
        ["zsh", "-lc", command],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    status = "ok" if completed.returncode == 0 else "error"
    return ToolExecutionResult(
        status=status,
        tool_output={
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        },
        assistant_message=f"已执行命令 `{command}`，退出码是 {completed.returncode}。",
        summary=f"ran bash command {command}",
    )


def _run_git_status(workspace_root: Path, tool_input: dict[str, Any]) -> ToolExecutionResult:
    del tool_input
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    status = "ok" if completed.returncode == 0 else "error"
    return ToolExecutionResult(
        status=status,
        tool_output={
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        },
        assistant_message="已收集当前工作区状态，可以据此决定是否继续修改或验证。",
        summary="collected git status",
    )
