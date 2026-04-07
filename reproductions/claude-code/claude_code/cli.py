"""Claude Code cleanroom CLI 入口。"""

from __future__ import annotations

import argparse
import sys

from .app_service import ClaudeCodeAppService, RuntimeUnavailableError
from .permission_rules import PermissionRulesError, load_permission_rules
from .permissions import InteractivePermissionGate
from .session_store import SessionRecord


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
    parser.add_argument(
        "--tool-direct",
        action="store_true",
        help="Use the deterministic direct tool path instead of the live Responses agent",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=6,
        help="Maximum number of live agent steps before stopping",
    )
    return parser


def resolve_task(raw_task: list[str]) -> str | None:
    task = " ".join(raw_task).strip()
    return task or None


def create_or_resume_session(args: argparse.Namespace, service: ClaudeCodeAppService) -> tuple[str, SessionRecord]:
    task = resolve_task(args.task)

    if args.session_id and args.continue_last:
        raise ValueError("cannot use --session-id and --continue-last together")

    if args.session_id or args.continue_last:
        session_id = args.session_id or service.load_latest_session_id()
        if task is None:
            return "loaded", service.get_session(session_id)
        return "resumed", service.append_task(session_id, task)

    if task is None:
        raise ValueError("a task is required when creating a new session")

    return "created", service.create_session(task)


def render_summary(status: str, record: SessionRecord) -> str:
    latest_task = record.user_tasks[-1]["content"] if record.user_tasks else ""
    return "\n".join(
        [
            f"status: {status}",
            f"session_id: {record.session_id}",
            f"task_count: {len(record.user_tasks)}",
            f"event_count: {len(record.events)}",
            f"latest_task: {latest_task}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_steps <= 0:
        parser.error("--max-steps must be greater than 0")

    service = ClaudeCodeAppService.for_current_workspace()

    try:
        status, record = create_or_resume_session(args, service)
        # CLI 参数 -> 独立规则模块 -> permission gate -> runtime/tool 层。
        # 统一给 live/tool-direct 复用同一套 gate，这样受控工具的边界不会只停留在调试路径。
        rule_set = load_permission_rules(service.workspace)
        permission_gate = InteractivePermissionGate(rule_set=rule_set)
    except (FileNotFoundError, PermissionRulesError, ValueError) as exc:
        parser.error(str(exc))

    try:
        loop_result = service.run_turn(
            record,
            tool_direct=args.tool_direct,
            max_steps=args.max_steps,
            permission_gate=permission_gate,
        )
    except RuntimeUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\n".join([render_summary(status, record), loop_result.render_summary()]))

    if loop_result.verify.status == "completed":
        return 0
    if args.tool_direct and loop_result.verify.status == "loop-needs-attention":
        return 1
    if not args.tool_direct:
        return 1
    return 0
