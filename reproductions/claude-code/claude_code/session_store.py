"""Claude Code cleanroom 的最小 session/event store。

这个文件负责把 CLI 和 runtime 之间共享的会话状态协议化，对应
`docs/claude-code/claude-code-todo.md` 的 “Phase 2 第 3 点”。

关键代码链:
CLI 新任务/继续会话 -> SessionRecord.events -> runtime 追加模型/工具事件 -> JSON 持久化

对应《claude-code-study.md》的:
- 4. 核心运行循环
- 5.3 Memory / Context
- 9.1 第一阶段必须有

当前取舍:
- 先把统一事件流落成单文件 JSON，证明 session 已经不只是 user task 列表。
- 暂时不做复杂 event schema version、stream compaction、附件存储。
- 为了兼容前两步生成的旧 session，仍然支持从 `user_tasks` 自动迁移到 `events`。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


USER_MESSAGE = "user_message"
MODEL_RESPONSE = "model_response"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionEvent:
    """统一事件结构。

    这里不把 schema 设计得太复杂，只保留后续 Phase 会稳定用到的公共外壳:
    - `kind`: 事件类型，先固定为用户消息 / 模型响应 / 工具调用 / 工具结果
    - `payload`: 各类事件的结构化内容

    这样做的目的，是先把“事件流”这个抽象钉住，而不是抢跑到完整 tracing 系统。
    """

    event_id: str
    kind: str
    created_at: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionEvent":
        return cls(
            event_id=payload.get("event_id", str(uuid4())),
            kind=payload["kind"],
            created_at=payload["created_at"],
            payload=dict(payload.get("payload", {})),
        )


@dataclass
class SessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    events: list[SessionEvent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": [event.to_dict() for event in self.events],
        }

    @property
    def user_tasks(self) -> list[dict[str, str]]:
        """兼容早期 CLI/runtime 的只读视图。

        当前仓库里仍有一些摘要输出和测试心智模型在看“任务列表”。
        这里把它退化成 `user_message` 事件的派生结果，避免一边写 `events`
        一边再维护一份重复来源。
        """

        tasks: list[dict[str, str]] = []
        for event in self.events:
            if event.kind != USER_MESSAGE:
                continue

            tasks.append(
                {
                    "content": str(event.payload.get("content", "")),
                    "created_at": event.created_at,
                }
            )
        return tasks

    def add_event(self, kind: str, payload: dict[str, Any], *, created_at: str | None = None) -> SessionEvent:
        """向 session 追加一个统一事件，并同步更新时间。

        这是事件流真正的写入口。CLI 和 runtime 都通过它写数据，
        这样后续要加 hooks、checkpoint 或事件审计时，不必再到处找散落写法。
        """

        timestamp = created_at or utc_now_iso()
        event = SessionEvent(
            event_id=str(uuid4()),
            kind=kind,
            created_at=timestamp,
            payload=payload,
        )
        self.events.append(event)
        self.updated_at = timestamp
        return event

    def add_user_message(self, content: str) -> SessionEvent:
        return self.add_event(USER_MESSAGE, {"content": content})

    def add_model_response(
        self,
        content: str,
        *,
        strategy: str,
        next_action: str,
    ) -> SessionEvent:
        return self.add_event(
            MODEL_RESPONSE,
            {
                "content": content,
                "strategy": strategy,
                "next_action": next_action,
            },
        )

    def add_tool_call(self, *, tool_name: str, tool_input: dict[str, Any]) -> SessionEvent:
        return self.add_event(
            TOOL_CALL,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
        )

    def add_tool_result(
        self,
        *,
        tool_name: str,
        status: str,
        tool_output: dict[str, Any],
    ) -> SessionEvent:
        return self.add_event(
            TOOL_RESULT,
            {
                "tool_name": tool_name,
                "status": status,
                "tool_output": tool_output,
            },
        )

    def recent_events(self, *, kind: str | None = None, limit: int | None = None) -> list[SessionEvent]:
        selected = [event for event in self.events if kind is None or event.kind == kind]
        if limit is None:
            return selected
        return selected[-limit:]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionRecord":
        raw_events = payload.get("events")
        if raw_events is None:
            raw_events = _migrate_legacy_user_tasks(payload.get("user_tasks", []))

        return cls(
            session_id=payload["session_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            events=[SessionEvent.from_dict(item) for item in raw_events],
        )


def _migrate_legacy_user_tasks(user_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把旧版 `user_tasks` session 自动折叠成统一事件流。

    这里故意只做最小兼容迁移:
    - 旧数据只会变成 `user_message`
    - 不会臆造当时不存在的模型和工具事件
    """

    migrated: list[dict[str, Any]] = []
    for task in user_tasks:
        migrated.append(
            {
                "event_id": str(uuid4()),
                "kind": USER_MESSAGE,
                "created_at": task["created_at"],
                "payload": {
                    "content": task["content"],
                },
            }
        )
    return migrated


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions_dir = self.root / "sessions"
        self.latest_session_file = self.root / "latest_session.txt"

    @classmethod
    def from_environment(cls, workspace_root: Path) -> "SessionStore":
        override = os.environ.get("CLAUDE_CODE_STATE_DIR")
        if override:
            return cls(Path(override))

        # 这里把会话状态固定在项目内的隐藏目录，便于先做最小闭环。
        # 后续如果要支持用户级/全局级 session，再把存储位置抽象出去。
        return cls(workspace_root / ".claude-code")

    def create(self, task: str) -> SessionRecord:
        self._ensure_dirs()
        now = utc_now_iso()
        record = SessionRecord(
            session_id=str(uuid4()),
            created_at=now,
            updated_at=now,
            events=[],
        )
        record.add_user_message(task)
        self.save(record)
        return record

    def append_task(self, session_id: str, task: str) -> SessionRecord:
        record = self.load(session_id)
        record.add_user_message(task)
        self.save(record)
        return record

    def load(self, session_id: str) -> SessionRecord:
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")

        payload = json.loads(session_path.read_text(encoding="utf-8"))
        return SessionRecord.from_dict(payload)

    def load_latest_session_id(self) -> str:
        if not self.latest_session_file.exists():
            raise FileNotFoundError("no previous session found")
        return self.latest_session_file.read_text(encoding="utf-8").strip()

    def save(self, record: SessionRecord) -> None:
        self._ensure_dirs()
        session_path = self.sessions_dir / f"{record.session_id}.json"
        session_path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.latest_session_file.write_text(record.session_id + "\n", encoding="utf-8")

    def _ensure_dirs(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
