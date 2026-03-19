from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    created_at: str
    updated_at: str
    user_tasks: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_tasks": self.user_tasks,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionRecord":
        return cls(
            session_id=payload["session_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            user_tasks=list(payload.get("user_tasks", [])),
        )


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
            # 先只保存用户任务，满足 Phase 2 第 1 点的 resume 需求。
            # 等做到统一事件流时，再扩成用户消息、模型响应、工具事件的完整结构。
            user_tasks=[{"content": task, "created_at": now}],
        )
        self.save(record)
        return record

    def append_task(self, session_id: str, task: str) -> SessionRecord:
        record = self.load(session_id)
        now = utc_now_iso()
        record.user_tasks.append({"content": task, "created_at": now})
        record.updated_at = now
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
