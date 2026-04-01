"""Claude Code cleanroom 的最小文件 checkpoint 模块。

这个文件对应 `docs/claude-code/claude-code-todo.md` 的 “Phase 4 第 2 点”:
- 做文件 checkpoint：写入前备份、支持撤销最近一次修改

关键代码链:
tool-direct `edit` -> CheckpointStore.save_latest_edit -> 文件落盘
-> tool-direct `undo_last_edit` -> CheckpointStore.undo_last_edit -> 恢复最近一次备份

对应《claude-code-study.md》的:
- 5.5 Safety / Boundaries
- 9.1 第一阶段必须有

当前取舍:
- 先只保存“最近一次 edit 修改”的文本快照，不抢跑到多文件批量 patch、diff 栈或 git 级恢复。
- checkpoint 文件先直接写在 state dir 下，和 session store 并列，便于 CLI/runtime 共用。
- 当前只覆盖 UTF-8 文本文件，因为现阶段 `edit` 工具本身也只处理文本替换。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .session_store import utc_now_iso


@dataclass(frozen=True)
class FileCheckpoint:
    """单文件最近一次修改的最小 checkpoint。

    当前只保留撤销链路真正需要的信息:
    - 改动发生时的相对路径
    - 写入前的原始文本
    - 一个稳定的 checkpoint id，便于事件流和终端摘要引用
    """

    checkpoint_id: str
    created_at: str
    relative_path: str
    original_content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "relative_path": self.relative_path,
            "original_content": self.original_content,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, str]) -> "FileCheckpoint":
        return cls(
            checkpoint_id=payload["checkpoint_id"],
            created_at=payload["created_at"],
            relative_path=payload["relative_path"],
            original_content=payload["original_content"],
        )


@dataclass(frozen=True)
class UndoResult:
    """撤销最近一次修改后的最小结果。"""

    checkpoint_id: str
    restored_at: str
    relative_path: str


class CheckpointStore:
    """管理最近一次文件写入的 checkpoint。

    这里故意不和 session JSON 混在一起，而是给控制层单独一个状态目录。
    这样后续扩展成多 checkpoint、批量修改或更复杂的恢复策略时，
    不必碰会话事件流的 schema。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.latest_edit_file = self.root / "latest_edit.json"

    def save_latest_edit(self, workspace_root: Path, file_path: Path, original_content: str) -> FileCheckpoint:
        """在真正写文件前保存一个可恢复的最新快照。

        输入:
        - `workspace_root`: 当前工作区根目录
        - `file_path`: 将被修改的真实文件路径
        - `original_content`: 写入前的完整文本

        输出:
        - 返回刚写入的 checkpoint 元数据，供工具结果和终端摘要复用
        """

        relative_path = str(file_path.resolve().relative_to(workspace_root.resolve()))
        checkpoint = FileCheckpoint(
            checkpoint_id=str(uuid4()),
            created_at=utc_now_iso(),
            relative_path=relative_path,
            original_content=original_content,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.latest_edit_file.write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return checkpoint

    def load_latest_edit(self) -> FileCheckpoint | None:
        if not self.latest_edit_file.exists():
            return None
        payload = json.loads(self.latest_edit_file.read_text(encoding="utf-8"))
        return FileCheckpoint.from_dict(payload)

    def undo_last_edit(self, workspace_root: Path) -> UndoResult:
        """恢复最近一次 `edit` 写入前的原始文本。

        当前只支持单个最近 checkpoint:
        - 找不到 checkpoint 时直接报错
        - 恢复成功后立即清除 checkpoint，避免重复撤销同一条记录
        """

        checkpoint = self.load_latest_edit()
        if checkpoint is None:
            raise FileNotFoundError("no checkpoint available for undo")

        target = _resolve_checkpoint_target(workspace_root, checkpoint.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(checkpoint.original_content, encoding="utf-8")
        self.clear_latest_edit()
        return UndoResult(
            checkpoint_id=checkpoint.checkpoint_id,
            restored_at=utc_now_iso(),
            relative_path=checkpoint.relative_path,
        )

    def clear_latest_edit(self) -> None:
        if self.latest_edit_file.exists():
            self.latest_edit_file.unlink()


def _resolve_checkpoint_target(workspace_root: Path, relative_path: str) -> Path:
    """把 checkpoint 记录里的相对路径重新收束到 workspace 内。

    这里继续沿用 cleanroom 当前阶段的最小边界:
    checkpoint 虽然来自本地状态文件，但恢复时仍然要做一次路径校验，
    避免把控制层变成另一个绕过 workspace 边界的入口。
    """

    root = workspace_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"checkpoint path escapes workspace root: {relative_path}") from exc
    return candidate
