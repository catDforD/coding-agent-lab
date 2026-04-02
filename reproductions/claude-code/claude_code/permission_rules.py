"""Claude Code cleanroom 的最小 allowlist / denylist 配置模块。

这个文件对应 `docs/claude-code/claude-code-todo.md` 的 “Phase 4 第 3 点”:
- 把 allowlist / denylist 配置做成单独模块，避免以后和 runtime 耦合

关键代码链:
CLI / runtime 准备进入 permission gate
-> load_permission_rules 读取独立规则文件
-> PermissionRuleSet.match 判断当前工具输入是否命中 allow / deny 规则
-> permissions.InteractivePermissionGate 决定自动放行、自动拒绝或继续询问用户

对应《claude-code-study.md》的:
- 5.5 Safety / Boundaries
- 9.1 第一阶段必须有
- 9.3 一个推荐的 cleanroom 架构

当前取舍:
- 先只支持 `bash` 和 `edit` 两类受控工具，保持和当前 control layer 边界一致。
- 规则文件先用一个最小 JSON 协议，不抢跑到完整 managed settings / plugin 文件协议。
- 匹配规则先做“前缀命中”，其中 denylist 优先级高于 allowlist，便于把高风险命令或路径先钉死。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PERMISSION_RULES_FILE = Path(".claude-code/permission-rules.json")


class PermissionRulesError(ValueError):
    """permission rules 配置格式无效。"""


@dataclass(frozen=True)
class ToolRuleList:
    """单个工具的最小 allow / deny 规则集合。"""

    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleMatch:
    """规则匹配结果。

    `candidate` 保留下来，是为了让 permission gate 在事件流里解释
    “为什么这次是自动放行 / 自动拒绝”，而不是只知道命中了哪条配置。
    """

    action: str
    source: str
    pattern: str
    candidate: str


@dataclass(frozen=True)
class PermissionRuleSet:
    """`bash` / `edit` 两类受控工具的最小规则集。"""

    bash: ToolRuleList = ToolRuleList()
    edit: ToolRuleList = ToolRuleList()
    source_path: Path | None = None

    def match(self, tool_name: str, tool_input: dict[str, Any]) -> RuleMatch | None:
        """判断本次工具输入是否命中 allowlist / denylist。

        当前只做最小策略:
        - denylist 先判，命中就直接拒绝
        - allowlist 再判，命中就自动放行
        - 都没命中时返回 `None`，交回交互式 gate 继续询问
        """

        rules = _tool_rules_for_name(self, tool_name)
        if rules is None:
            return None

        candidate = _candidate_for_tool(tool_name, tool_input)
        if not candidate:
            return None

        denied = _first_matching_pattern(rules.denylist, candidate)
        if denied is not None:
            return RuleMatch(
                action="deny",
                source="denylist",
                pattern=denied,
                candidate=candidate,
            )

        allowed = _first_matching_pattern(rules.allowlist, candidate)
        if allowed is not None:
            return RuleMatch(
                action="allow",
                source="allowlist",
                pattern=allowed,
                candidate=candidate,
            )

        return None


def load_permission_rules(workspace_root: Path) -> PermissionRuleSet:
    """从独立配置文件加载最小 allowlist / denylist。

    加载顺序先保持简单:
    1. 若设置了 `CLAUDE_CODE_PERMISSION_RULES`，优先读取该路径
    2. 否则读取 workspace 下默认的 `.claude-code/permission-rules.json`

    默认路径缺失时返回空规则；显式指定路径缺失时直接报错，避免把拼错配置静默吞掉。
    """

    override = os.environ.get("CLAUDE_CODE_PERMISSION_RULES")
    if override:
        path = _resolve_rules_path(workspace_root, override)
        if not path.exists():
            raise FileNotFoundError(f"permission rules file not found: {path}")
    else:
        path = workspace_root / DEFAULT_PERMISSION_RULES_FILE
        if not path.exists():
            return PermissionRuleSet()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PermissionRulesError(
            f"permission rules file is not valid JSON: {path}"
        ) from exc

    if not isinstance(payload, dict):
        raise PermissionRulesError("permission rules root must be a JSON object")

    return PermissionRuleSet(
        bash=_load_tool_rule_list(payload, "bash"),
        edit=_load_tool_rule_list(payload, "edit"),
        source_path=path,
    )


def _resolve_rules_path(workspace_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (workspace_root / candidate).resolve()


def _load_tool_rule_list(payload: dict[str, Any], tool_name: str) -> ToolRuleList:
    raw_rules = payload.get(tool_name, {})
    if raw_rules in ({}, None):
        return ToolRuleList()
    if not isinstance(raw_rules, dict):
        raise PermissionRulesError(f"`{tool_name}` rules must be a JSON object")

    return ToolRuleList(
        allowlist=_read_rule_items(raw_rules, tool_name, "allowlist"),
        denylist=_read_rule_items(raw_rules, tool_name, "denylist"),
    )


def _read_rule_items(
    raw_rules: dict[str, Any],
    tool_name: str,
    field_name: str,
) -> tuple[str, ...]:
    raw_items = raw_rules.get(field_name, [])
    if raw_items in (None, []):
        return ()
    if not isinstance(raw_items, list):
        raise PermissionRulesError(
            f"`{tool_name}.{field_name}` must be a JSON array of strings"
        )

    items: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            raise PermissionRulesError(
                f"`{tool_name}.{field_name}` must contain only strings"
            )
        normalized = item.strip()
        if normalized:
            items.append(normalized)
    return tuple(items)


def _tool_rules_for_name(rule_set: PermissionRuleSet, tool_name: str) -> ToolRuleList | None:
    if tool_name == "bash":
        return rule_set.bash
    if tool_name == "edit":
        return rule_set.edit
    return None


def _candidate_for_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "bash":
        return str(tool_input.get("command", "")).strip()
    if tool_name == "edit":
        return _normalize_relative_path(str(tool_input.get("path", "")))
    return ""


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _first_matching_pattern(patterns: tuple[str, ...], candidate: str) -> str | None:
    for pattern in patterns:
        if candidate == pattern or candidate.startswith(pattern):
            return pattern
    return None
