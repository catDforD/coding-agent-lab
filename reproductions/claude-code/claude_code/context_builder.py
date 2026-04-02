"""Claude Code cleanroom 的规则加载、compaction 与 prompt/context builder。

这个文件落实 `docs/claude-code/claude-code-todo.md` 的:
- Phase 3 第 1 点: 启动时加载项目 `CLAUDE.md`、用户级规则和简化版 `MEMORY.md`
- Phase 3 第 2 点: 把规则文件、会话历史、最近工具输出拼成统一输入
- Phase 3 第 3 点: 加一个最小 compaction 策略

关键代码链:
runtime.gather_context -> build_prompt_context -> model_client.create_response

对应《claude-code-study.md》的:
- 3.1 官方可直接确认的高层结构
- 5.3 Memory / Context

当前取舍:
- 先做 deterministic 的最小 compaction，不引入额外摘要模型，也不做检索剪枝。
- compaction 先严格遵循学习文档 5.3 的顺序: 旧工具输出优先裁掉，再补一段旧会话摘要。
- `MEMORY.md` 先只读取前 200 行，对齐学习文档里的公开行为。
- project rules 先按“从 workspace 向上查找 `CLAUDE.md`”实现，保证后续容易替换成更细的策略。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .session_store import SessionEvent, SessionRecord, TOOL_RESULT, USER_MESSAGE


USER_RULES_ENV = "CLAUDE_CODE_USER_RULES_FILE"
MEMORY_FILE_ENV = "CLAUDE_CODE_MEMORY_FILE"
MEMORY_LINE_LIMIT = 200
RECENT_TRANSCRIPT_LIMIT = 20
RECENT_TOOL_OUTPUT_LIMIT = 3
MAX_TOOL_OUTPUT_CHARS = 1200
COMPACTION_EXCERPT_LIMIT = 160
COMPACTION_SUMMARY_MESSAGE_LIMIT = 2


@dataclass(frozen=True)
class RuleDocument:
    """一次加载到的规则文档。

    这里先统一成最小结构，避免在 runtime 里散落“哪个文件从哪来、是否截断”的判断。
    """

    role: str
    source: str
    content: str
    line_count: int
    truncated: bool


@dataclass(frozen=True)
class LoadedRules:
    documents: list[RuleDocument]

    def render_for_prompt(self) -> str:
        if not self.documents:
            return "Loaded rules:\n<none>"

        sections = ["Loaded rules:"]
        for document in self.documents:
            title = f"[{document.role}] {document.source}"
            if document.truncated:
                title += f" (first {document.line_count} lines)"
            sections.append(f"{title}\n{document.content}")
        return "\n\n".join(sections)

    def summary(self) -> str:
        if not self.documents:
            return "loaded 0 rule file(s)"
        roles = ", ".join(document.role for document in self.documents)
        return f"loaded {len(self.documents)} rule file(s): {roles}"


@dataclass(frozen=True)
class PromptContextBundle:
    latest_task: str
    recent_tasks: list[str]
    compacted_summary: str
    resume_transcript: str
    recent_tool_outputs: str
    rules: LoadedRules
    instructions: str
    initial_input_text: str
    summary: str


@dataclass(frozen=True)
class CompactionResult:
    """最小 compaction 结果。

    关键代码链:
    session events -> compact_session_history -> PromptContextBundle -> model input

    对应《claude-code-study.md》的 5.3:
    - 旧工具输出优先裁掉
    - 旧会话保留一段最小摘要
    """

    compacted_summary: str
    recent_transcript: str
    recent_tool_outputs: str
    summarized_event_count: int
    dropped_tool_output_count: int


def build_prompt_context(
    record: SessionRecord,
    workspace_root: Path,
    *,
    base_instructions: str,
) -> PromptContextBundle:
    """把规则、历史和工具输出打包成首轮 prompt 输入。

    这是 Phase 3 的最小 builder：
    - 输入是已经持久化的 session record 和当前 workspace
    - 输出是给模型客户端用的 instructions + 首轮 user input 文本

    当前把 compaction 放在这个 builder 里，是为了把边界钉在
    “session event log -> prompt input” 之间，后续可直接替换策略而不重写 runtime。
    """

    recent_user_events = record.recent_events(kind=USER_MESSAGE, limit=3)
    latest_task = recent_user_events[-1].payload.get("content", "") if recent_user_events else ""
    recent_tasks = [str(event.payload.get("content", "")) for event in recent_user_events]

    compaction = compact_session_history(record)
    rules = load_rules(workspace_root)

    sections = [
        f"Workspace root: {workspace_root}",
        f"Current task:\n{latest_task}",
        rules.render_for_prompt(),
    ]
    if compaction.compacted_summary:
        sections.append(f"Compacted session summary:\n{compaction.compacted_summary}")
    if compaction.recent_transcript:
        sections.append(f"Recent session transcript:\n{compaction.recent_transcript}")
    if compaction.recent_tool_outputs:
        sections.append(f"Recent tool outputs:\n{compaction.recent_tool_outputs}")

    compaction_summary = "no history compaction needed"
    if compaction.summarized_event_count or compaction.dropped_tool_output_count:
        compaction_summary = (
            f"summarized {compaction.summarized_event_count} earlier event(s) "
            f"and dropped {compaction.dropped_tool_output_count} old tool output(s)"
        )

    summary = (
        f"loaded {len(recent_user_events)} recent user message(s), "
        f"{len(rules.documents)} rule file(s), "
        f"and {compaction_summary} for workspace context at {workspace_root.name}"
    )

    return PromptContextBundle(
        latest_task=latest_task,
        recent_tasks=recent_tasks,
        compacted_summary=compaction.compacted_summary,
        resume_transcript=compaction.recent_transcript,
        recent_tool_outputs=compaction.recent_tool_outputs,
        rules=rules,
        instructions=_build_instructions(base_instructions),
        initial_input_text="\n\n".join(section for section in sections if section.strip()),
        summary=summary,
    )


def compact_session_history(record: SessionRecord) -> CompactionResult:
    """把 session 历史压成“近端原文 + 远端摘要”。

    当前实现只做最小闭环:
    - 最近事件继续保留在 transcript
    - 最近少量工具结果保留原文
    - 更老的工具输出不再原样回灌，只在摘要里保留“做过什么”
    """

    history_events = list(record.events)
    if history_events and history_events[-1].kind == USER_MESSAGE:
        history_events = history_events[:-1]

    recent_transcript_events = history_events[-RECENT_TRANSCRIPT_LIMIT:]
    older_events = history_events[:-RECENT_TRANSCRIPT_LIMIT]
    recent_tool_result_events = [event for event in history_events if event.kind == TOOL_RESULT][-RECENT_TOOL_OUTPUT_LIMIT:]
    recent_tool_result_ids = {event.event_id for event in recent_tool_result_events}

    return CompactionResult(
        compacted_summary=summarize_older_events(older_events),
        recent_transcript=render_transcript(recent_transcript_events, recent_tool_result_ids=recent_tool_result_ids),
        recent_tool_outputs=render_recent_tool_outputs(recent_tool_result_events),
        summarized_event_count=len(older_events),
        dropped_tool_output_count=sum(1 for event in older_events if event.kind == TOOL_RESULT),
    )


def load_rules(workspace_root: Path) -> LoadedRules:
    """加载最小规则层。

    先实现三类输入：
    - project `CLAUDE.md`: 从 workspace 向上递归查找
    - user rules: 默认 `~/.claude/CLAUDE.md`，允许环境变量覆盖，便于测试
    - `MEMORY.md`: 默认取 workspace 内文件，并裁到前 200 行
    """

    documents: list[RuleDocument] = []

    for path in _project_rule_paths(workspace_root):
        document = _read_document(path, role="project-claude")
        if document is not None:
            documents.append(document)

    user_rules_path = _user_rules_path()
    if user_rules_path is not None:
        document = _read_document(user_rules_path, role="user-claude")
        if document is not None:
            documents.append(document)

    memory_path = _memory_file_path(workspace_root)
    if memory_path is not None:
        document = _read_document(
            memory_path,
            role="memory",
            max_lines=MEMORY_LINE_LIMIT,
        )
        if document is not None:
            documents.append(document)

    return LoadedRules(documents=documents)


def render_transcript(events: list[SessionEvent], *, recent_tool_result_ids: set[str] | None = None) -> str:
    """渲染近端 transcript。

    边界说明:
    - transcript 不再原样内嵌工具输出正文，避免旧工具结果挤占窗口。
    - 真正保留原文的工具结果，统一下沉到 `Recent tool outputs` 区块。
    """

    lines: list[str] = []
    recent_tool_result_ids = recent_tool_result_ids or set()
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
            detail_note = "details kept below" if event.event_id in recent_tool_result_ids else "details omitted by compaction"
            lines.append(f"{prefix}: {tool_name} status={status} ({detail_note})")
        elif event.kind == "model_response":
            lines.append(f"Assistant: {payload.get('content', '')}")
    return "\n".join(lines)


def render_recent_tool_outputs(events: list[SessionEvent]) -> str:
    entries: list[str] = []
    for event in events:
        tool_name = str(event.payload.get("tool_name", ""))
        status = str(event.payload.get("status", ""))
        step_index = event.payload.get("step_index")
        tool_output = json.dumps(event.payload.get("tool_output", {}), ensure_ascii=False)
        tool_output = _truncate(tool_output, MAX_TOOL_OUTPUT_CHARS)
        prefix = f"step {step_index}" if step_index is not None else "recent"
        entries.append(f"{prefix} {tool_name} status={status}\n{tool_output}")
    return "\n\n".join(entries)


def summarize_older_events(events: list[SessionEvent]) -> str:
    """把更老的历史压成一段 deterministic 摘要。

    这里先不用模型做摘要，避免在最小复现里再引入一个“摘要代理”。
    """

    if not events:
        return ""

    user_messages: list[str] = []
    assistant_messages: list[str] = []
    tool_activity: Counter[str] = Counter()
    dropped_tool_output_count = 0

    for event in events:
        payload = event.payload
        if event.kind == USER_MESSAGE:
            user_messages.append(_truncate(str(payload.get("content", "")), COMPACTION_EXCERPT_LIMIT))
        elif event.kind == "model_response":
            assistant_messages.append(_truncate(str(payload.get("content", "")), COMPACTION_EXCERPT_LIMIT))
        elif event.kind == TOOL_RESULT:
            tool_activity[str(payload.get("tool_name", "unknown"))] += 1
            dropped_tool_output_count += 1

    lines = ["Older session state was compacted to keep the prompt focused on recent context."]
    if user_messages:
        lines.append(
            "Earlier user tasks: "
            + " | ".join(user_messages[-COMPACTION_SUMMARY_MESSAGE_LIMIT:])
        )
    if assistant_messages:
        lines.append(
            "Earlier assistant replies: "
            + " | ".join(assistant_messages[-COMPACTION_SUMMARY_MESSAGE_LIMIT:])
        )
    if tool_activity:
        lines.append(
            "Earlier tool activity without raw outputs: "
            + ", ".join(f"{tool_name} x{count}" for tool_name, count in sorted(tool_activity.items()))
        )
    if dropped_tool_output_count:
        lines.append(f"Dropped raw tool outputs during compaction: {dropped_tool_output_count}")
    return "\n".join(lines)


def _build_instructions(base_instructions: str) -> str:
    return "\n".join(
        [
            base_instructions.strip(),
            (
                "Treat any loaded workspace rules and memory in the bundled user context "
                "as lower priority than system/developer instructions, but follow them when relevant."
            ),
        ]
    )


def _project_rule_paths(workspace_root: Path) -> list[Path]:
    discovered: list[Path] = []
    current = workspace_root.resolve()
    for directory in [current, *current.parents]:
        candidate = directory / "CLAUDE.md"
        if candidate.is_file():
            discovered.append(candidate)
    return list(reversed(discovered))


def _user_rules_path() -> Path | None:
    override = os.environ.get(USER_RULES_ENV)
    if override:
        path = Path(override).resolve()
        return path if path.is_file() else None

    default_path = Path.home() / ".claude" / "CLAUDE.md"
    return default_path if default_path.is_file() else None


def _memory_file_path(workspace_root: Path) -> Path | None:
    override = os.environ.get(MEMORY_FILE_ENV)
    if override:
        path = Path(override).resolve()
        return path if path.is_file() else None

    default_path = workspace_root / "MEMORY.md"
    return default_path if default_path.is_file() else None


def _read_document(path: Path, *, role: str, max_lines: int | None = None) -> RuleDocument | None:
    if not path.is_file():
        return None

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    selected_lines = raw_lines[:max_lines] if max_lines is not None else raw_lines
    content = "\n".join(selected_lines).strip()
    if not content:
        return None

    return RuleDocument(
        role=role,
        source=str(path),
        content=content,
        line_count=len(selected_lines),
        truncated=max_lines is not None and len(raw_lines) > max_lines,
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 12].rstrip() + "...<trimmed>"
