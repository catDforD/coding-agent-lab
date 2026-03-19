from __future__ import annotations

import argparse
from pathlib import Path

from .session_store import SessionRecord, SessionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-code",
        description="Claude Code cleanroom CLI skeleton",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="New user task to attach to the session",
    )
    parser.add_argument(
        "--session-id",
        help="Continue a specific session id instead of creating a new one",
    )
    parser.add_argument(
        "--continue-last",
        action="store_true",
        help="Continue the latest saved session",
    )
    return parser


def resolve_task(raw_task: list[str]) -> str | None:
    task = " ".join(raw_task).strip()
    return task or None


def workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def create_or_resume_session(args: argparse.Namespace, store: SessionStore) -> tuple[str, SessionRecord]:
    task = resolve_task(args.task)

    if args.session_id and args.continue_last:
        raise ValueError("cannot use --session-id and --continue-last together")

    if args.session_id or args.continue_last:
        session_id = args.session_id or store.load_latest_session_id()
        if task is None:
            return "loaded", store.load(session_id)
        return "resumed", store.append_task(session_id, task)

    if task is None:
        raise ValueError("a task is required when creating a new session")

    return "created", store.create(task)


def render_summary(status: str, record: SessionRecord) -> str:
    latest_task = record.user_tasks[-1]["content"] if record.user_tasks else ""
    return "\n".join(
        [
            f"status: {status}",
            f"session_id: {record.session_id}",
            f"task_count: {len(record.user_tasks)}",
            f"latest_task: {latest_task}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = SessionStore.from_environment(workspace_root())

    try:
        status, record = create_or_resume_session(args, store)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    # 这里先只做“接收任务 + 维护 session”的 CLI 入口。
    # gather -> act -> verify 主循环留给下一条 todo 实现，避免这一阶段把边界做散。
    print(render_summary(status, record))
    return 0
