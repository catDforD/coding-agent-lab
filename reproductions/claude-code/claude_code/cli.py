"""Claude Code cleanroom CLI 入口。

当前文件负责把命令行输入接到 session store，再把 session 交给最小 runtime。
这次实现对应 todo 的“Phase 2 第 2 点”，把链路推进到:
CLI 参数 -> session store -> gather/act/verify 主循环。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .runtime import run_core_loop
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
    """把 CLI 输入折叠成一个可运行的 session。

    这里仍然只处理“新建 / 继续 / 读取”三种入口分流。
    更细的事件流和工具级恢复，会留到 Phase 2 第 3 点以后再展开。
    """
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
    """运行最小 Claude Code CLI。

    关键代码链:
    CLI 参数 -> session store -> runtime.run_core_loop -> 终端摘要输出

    对应《claude-code-study.md》的 4. 核心运行循环。
    当前故意只跑一轮 gather -> act -> verify，不在这里提前接入复杂 planning、
    统一事件流或真实工具执行。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    store = SessionStore.from_environment(workspace_root())

    try:
        status, record = create_or_resume_session(args, store)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    loop_result = run_core_loop(record, workspace_root())
    print("\n".join([render_summary(status, record), loop_result.render_summary()]))
    return 0
