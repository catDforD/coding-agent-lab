"""Claude Code cleanroom 的应用服务层。

这个模块把 CLI 与 Web API 共享的会话和 runtime 操作收拢到一处，
避免把 SessionStore / run_core_loop 的编排散落在多个入口里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError, load_openai_settings, workspace_root
from .model_client import LiveOpenAIClient, ModelClientError
from .runtime import LoopResult, run_core_loop
from .session_store import MODEL_RESPONSE, SessionRecord, SessionStore


class RuntimeUnavailableError(RuntimeError):
    """当前环境无法运行 live runtime。"""


@dataclass(frozen=True)
class RuntimeStatus:
    ready: bool
    model: str | None
    base_url: str | None
    state_dir: str
    missing_config: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "model": self.model,
            "base_url": self.base_url,
            "state_dir": self.state_dir,
            "missing_config": self.missing_config,
        }


class ClaudeCodeAppService:
    def __init__(self, workspace: Path, store: SessionStore) -> None:
        self.workspace = workspace
        self.store = store

    @classmethod
    def for_current_workspace(cls) -> "ClaudeCodeAppService":
        root = workspace_root()
        return cls(root, SessionStore.from_environment(root))

    def runtime_status(self) -> RuntimeStatus:
        messages: list[str] = []
        model: str | None = None
        base_url: str | None = None

        try:
            settings = load_openai_settings()
            model = settings.model
            base_url = settings.base_url
            try:
                LiveOpenAIClient(settings)
            except ModelClientError as exc:
                messages.append(str(exc))
        except ConfigError as exc:
            messages.append(str(exc))

        return RuntimeStatus(
            ready=not messages,
            model=model,
            base_url=base_url,
            state_dir=str(self.store.root),
            missing_config=messages,
        )

    def create_session(self, task: str) -> SessionRecord:
        return self.store.create(task)

    def append_task(self, session_id: str, task: str) -> SessionRecord:
        return self.store.append_task(session_id, task)

    def get_session(self, session_id: str) -> SessionRecord:
        return self.store.load(session_id)

    def load_latest_session_id(self) -> str:
        return self.store.load_latest_session_id()

    def list_sessions(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for record in self.store.list_records():
            latest_task = record.user_tasks[-1]["content"] if record.user_tasks else ""
            assistant_events = record.recent_events(kind=MODEL_RESPONSE, limit=1)
            last_response = assistant_events[-1].payload.get("content", "") if assistant_events else ""
            summaries.append(
                {
                    "session_id": record.session_id,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "latest_task": latest_task,
                    "event_count": len(record.events),
                    "last_response_excerpt": _truncate(str(last_response), 180),
                }
            )
        return summaries

    def run_turn(
        self,
        record: SessionRecord,
        *,
        tool_direct: bool,
        max_steps: int,
    ) -> LoopResult:
        model_client = None
        if not tool_direct:
            model_client = self._build_live_client()

        loop_result = run_core_loop(
            record,
            self.workspace,
            tool_direct=tool_direct,
            max_steps=max_steps,
            model_client=model_client,
        )
        self.store.save(record)
        return loop_result

    def create_and_run_live(self, task: str, *, max_steps: int = 6) -> tuple[SessionRecord, LoopResult]:
        self._build_live_client()
        record = self.create_session(task)
        return record, self.run_turn(record, tool_direct=False, max_steps=max_steps)

    def append_and_run_live(
        self,
        session_id: str,
        task: str,
        *,
        max_steps: int = 6,
    ) -> tuple[SessionRecord, LoopResult]:
        self._build_live_client()
        record = self.append_task(session_id, task)
        return record, self.run_turn(record, tool_direct=False, max_steps=max_steps)

    def _build_live_client(self) -> LiveOpenAIClient:
        try:
            settings = load_openai_settings()
            return LiveOpenAIClient(settings)
        except (ConfigError, ModelClientError) as exc:
            raise RuntimeUnavailableError(str(exc)) from exc


def serialize_session(record: SessionRecord) -> dict[str, Any]:
    return record.to_dict()


def serialize_loop_result(loop_result: LoopResult) -> dict[str, Any]:
    return {
        "mode": loop_result.act.mode,
        "step_count": loop_result.act.step_count,
        "executed_tools": loop_result.act.executed_tools,
        "finish_reason": loop_result.act.finish_reason,
        "verify_status": loop_result.verify.status,
        "verify_summary": loop_result.verify.summary,
        "assistant_response": loop_result.act.final_output,
    }


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
