from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_code import cli
from claude_code.runtime import ActPhaseResult, GatherPhaseResult, LoopResult, VerifyPhaseResult
from claude_code.session_store import SessionEvent, SessionRecord, utc_now_iso


PROJECT_DIR = Path(__file__).resolve().parent.parent


class CliEntryTest(unittest.TestCase):
    """覆盖 CLI 的 tool-direct 调试链和 live 配置边界。"""

    def run_cli(
        self,
        *args: str,
        state_dir: Path,
        workspace_root: Path,
        extra_env: dict[str, str] | None = None,
        stdin_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CLAUDE_CODE_STATE_DIR"] = str(state_dir)
        env["CLAUDE_CODE_WORKSPACE_ROOT"] = str(workspace_root)
        env["CLAUDE_CODE_ENV_FILE"] = str(state_dir / "test.env")
        env.pop("OPENAI_API_KEY", None)
        env.pop("OPENAI_MODEL", None)
        env.pop("OPENAI_BASE_URL", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "claude_code", *args],
            cwd=PROJECT_DIR,
            env=env,
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def make_workspace(self, root: Path) -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        (root / "notes.md").write_text("SessionStore lives here\n", encoding="utf-8")
        subprocess.run(
            ["git", "init"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_session_payload(self, state_dir: Path) -> dict[str, object]:
        session_id = (state_dir / "latest_session.txt").read_text(encoding="utf-8").strip()
        session_path = state_dir / "sessions" / f"{session_id}.json"
        return json.loads(session_path.read_text(encoding="utf-8"))

    def write_permission_rules(self, workspace_root: Path, payload: dict[str, object]) -> Path:
        rules_dir = workspace_root / ".claude-code"
        rules_dir.mkdir(parents=True, exist_ok=True)
        path = rules_dir / "permission-rules.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def make_record(self, task: str) -> SessionRecord:
        now = utc_now_iso()
        record = SessionRecord(
            session_id="cli-test-session",
            created_at=now,
            updated_at=now,
            events=[],
        )
        record.add_user_message(task)
        return record

    def test_read_file_tool_records_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "read_file",
                "notes.md",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mode: tool-direct", result.stdout)
            self.assertIn("executed_tools: read_file", result.stdout)

            payload = self.read_session_payload(state_dir)
            self.assertEqual(len(payload["events"]), 4)
            self.assertEqual(payload["events"][1]["payload"]["tool_name"], "read_file")
            self.assertEqual(payload["events"][1]["payload"]["step_index"], 1)
            self.assertEqual(payload["events"][2]["payload"]["status"], "ok")
            self.assertEqual(payload["events"][2]["payload"]["tool_output"]["path"], "notes.md")
            self.assertIn("SessionStore lives here", payload["events"][2]["payload"]["tool_output"]["content"])

    def test_search_tool_finds_workspace_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "search",
                "SessionStore",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("executed_tools: search", result.stdout)

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["query"], "SessionStore")
            self.assertGreaterEqual(tool_output["match_count"], 1)
            self.assertTrue(any("notes.md" in line for line in tool_output["matches"]))

    def test_edit_tool_updates_file_and_records_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "edit src/sample.txt -- beta -- gamma",
                state_dir=state_dir,
                workspace_root=workspace_root,
                stdin_text="y\n",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("executed_tools: edit", result.stdout)
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\ngamma\n",
            )

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["path"], "src/sample.txt")
            self.assertEqual(tool_output["replacements"], 1)
            self.assertIn("checkpoint", tool_output)
            self.assertTrue((state_dir / "checkpoints" / "latest_edit.json").exists())

    def test_undo_last_edit_restores_latest_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            edit_result = self.run_cli(
                "--tool-direct",
                "edit src/sample.txt -- beta -- gamma",
                state_dir=state_dir,
                workspace_root=workspace_root,
                stdin_text="y\n",
            )
            self.assertEqual(edit_result.returncode, 0, edit_result.stderr)
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\ngamma\n",
            )

            undo_result = self.run_cli(
                "--tool-direct",
                "undo_last_edit",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(undo_result.returncode, 0, undo_result.stderr)
            self.assertIn("executed_tools: undo_last_edit", undo_result.stdout)
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\nbeta\n",
            )
            self.assertFalse((state_dir / "checkpoints" / "latest_edit.json").exists())

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["path"], "src/sample.txt")
            self.assertTrue(tool_output["restored"])
            self.assertIn("checkpoint", tool_output)

    def test_undo_last_edit_fails_without_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "undo_last_edit",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("executed_tools: undo_last_edit", result.stdout)

            payload = self.read_session_payload(state_dir)
            self.assertEqual(payload["events"][2]["payload"]["status"], "error")
            self.assertEqual(
                payload["events"][2]["payload"]["tool_output"]["error_type"],
                "FileNotFoundError",
            )

    def test_edit_tool_stops_when_user_denies_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "edit src/sample.txt -- beta -- gamma",
                state_dir=state_dir,
                workspace_root=workspace_root,
                stdin_text="n\n",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Permission required for tool `edit`.", result.stdout)
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\nbeta\n",
            )

            payload = self.read_session_payload(state_dir)
            self.assertEqual(payload["events"][2]["payload"]["status"], "denied")
            self.assertEqual(
                payload["events"][2]["payload"]["tool_output"]["permission"]["status"],
                "denied",
            )

    def test_edit_tool_can_auto_allow_via_permission_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)
            rules_path = self.write_permission_rules(
                workspace_root,
                {
                    "edit": {
                        "allowlist": ["src/"],
                    }
                },
            )

            result = self.run_cli(
                "--tool-direct",
                "edit src/sample.txt -- beta -- gamma",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env={"CLAUDE_CODE_PERMISSION_RULES": str(rules_path)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Permission required for tool `edit`.", result.stdout)
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\ngamma\n",
            )

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["permission"]["status"], "allowed")
            self.assertEqual(tool_output["permission"]["source"], "allowlist")

    def test_bash_tool_can_auto_deny_via_permission_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)
            rules_path = self.write_permission_rules(
                workspace_root,
                {
                    "bash": {
                        "denylist": ["printf"],
                    }
                },
            )

            result = self.run_cli(
                "--tool-direct",
                "bash",
                "printf ready",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env={"CLAUDE_CODE_PERMISSION_RULES": str(rules_path)},
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("Permission required for tool `bash`.", result.stdout)

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(payload["events"][2]["payload"]["status"], "denied")
            self.assertEqual(tool_output["permission"]["status"], "denied")
            self.assertEqual(tool_output["permission"]["source"], "denylist")

    def test_bash_tool_records_command_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "bash",
                "printf ready",
                state_dir=state_dir,
                workspace_root=workspace_root,
                stdin_text="yes\n",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("executed_tools: bash", result.stdout)

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["command"], "printf ready")
            self.assertEqual(tool_output["stdout"], "ready")
            self.assertEqual(tool_output["returncode"], 0)

    def test_bash_tool_stops_when_input_stream_has_no_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "--tool-direct",
                "bash",
                "printf ready",
                state_dir=state_dir,
                workspace_root=workspace_root,
                stdin_text="",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Permission required for tool `bash`.", result.stdout)

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(payload["events"][2]["payload"]["status"], "denied")
            self.assertEqual(tool_output["permission"]["status"], "denied")
            self.assertEqual(tool_output["tool_name"], "bash")

    def test_git_status_tool_records_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)
            (workspace_root / "new.txt").write_text("draft\n", encoding="utf-8")

            result = self.run_cli(
                "--tool-direct",
                "git_status",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("executed_tools: git_status", result.stdout)

            payload = self.read_session_payload(state_dir)
            tool_output = payload["events"][2]["payload"]["tool_output"]
            self.assertEqual(tool_output["returncode"], 0)
            self.assertIn("?? new.txt", tool_output["stdout"])

    def test_legacy_session_still_migrates_into_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)
            sessions_dir = state_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            session_id = "legacy-session"
            payload = {
                "session_id": session_id,
                "created_at": "2026-03-19T00:00:00+00:00",
                "updated_at": "2026-03-19T00:00:00+00:00",
                "user_tasks": [
                    {
                        "content": "read_file notes.md",
                        "created_at": "2026-03-19T00:00:00+00:00",
                    }
                ],
            }
            (sessions_dir / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (state_dir / "latest_session.txt").write_text(session_id + "\n", encoding="utf-8")

            result = self.run_cli(
                "--tool-direct",
                "--session-id",
                session_id,
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status: loaded", result.stdout)
            self.assertIn("executed_tools: read_file", result.stdout)

            migrated = self.read_session_payload(state_dir)
            self.assertNotIn("user_tasks", migrated)
            self.assertEqual(
                [event["kind"] for event in migrated["events"]],
                ["user_message", "tool_call", "tool_result", "model_response"],
            )

    def test_live_mode_requires_openai_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)

            result = self.run_cli(
                "请解释 notes.md",
                state_dir=state_dir,
                workspace_root=workspace_root,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("missing OPENAI_API_KEY", result.stderr)

    def test_live_cli_attaches_permission_gate_to_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)

            class FakeService:
                def __init__(self, workspace: Path, record: SessionRecord) -> None:
                    self.workspace = workspace
                    self.record = record
                    self.permission_gate = None

                def create_session(self, task: str) -> SessionRecord:
                    self.record = CliEntryTest.make_record(self, task)
                    return self.record

                def run_turn(
                    self,
                    record: SessionRecord,
                    *,
                    tool_direct: bool,
                    max_steps: int,
                    permission_gate=None,
                    text_delta_callback=None,
                ) -> LoopResult:
                    self.permission_gate = permission_gate
                    return LoopResult(
                        gather=GatherPhaseResult(
                            latest_task=record.user_tasks[-1]["content"],
                            recent_tasks=[],
                            resume_transcript="",
                            recent_tool_outputs="",
                            prompt_instructions="",
                            prompt_input_text="",
                            summary="ok",
                        ),
                        act=ActPhaseResult(
                            mode="live",
                            strategy="live-responses-agent",
                            model="fake-live",
                            step_count=1,
                            executed_tools=[],
                            finish_reason="completed",
                            status="ok",
                            final_output="done",
                            summary="ok",
                        ),
                        verify=VerifyPhaseResult(status="completed", summary="ok"),
                        emitted_events=[],
                    )

            fake_service = FakeService(workspace_root, self.make_record("placeholder"))
            with (
                patch.object(cli.ClaudeCodeAppService, "for_current_workspace", return_value=fake_service),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                exit_code = cli.main(["请解释 notes.md"])

            self.assertEqual(exit_code, 0)
            self.assertIsNotNone(fake_service.permission_gate)

    def test_live_cli_prints_streamed_text_before_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)

            class FakeService:
                def __init__(self, workspace: Path, record: SessionRecord) -> None:
                    self.workspace = workspace
                    self.record = record

                def create_session(self, task: str) -> SessionRecord:
                    self.record = CliEntryTest.make_record(self, task)
                    return self.record

                def run_turn(
                    self,
                    record: SessionRecord,
                    *,
                    tool_direct: bool,
                    max_steps: int,
                    permission_gate=None,
                    text_delta_callback=None,
                ) -> LoopResult:
                    if text_delta_callback is not None:
                        text_delta_callback("你好")
                        text_delta_callback("！")
                    return LoopResult(
                        gather=GatherPhaseResult(
                            latest_task=record.user_tasks[-1]["content"],
                            recent_tasks=[],
                            resume_transcript="",
                            recent_tool_outputs="",
                            prompt_instructions="",
                            prompt_input_text="",
                            summary="ok",
                        ),
                        act=ActPhaseResult(
                            mode="live",
                            strategy="live-responses-agent",
                            model="fake-live",
                            step_count=1,
                            executed_tools=[],
                            finish_reason="completed",
                            status="ok",
                            final_output="你好！",
                            summary="ok",
                        ),
                        verify=VerifyPhaseResult(status="completed", summary="ok"),
                        emitted_events=[],
                    )

            fake_service = FakeService(workspace_root, self.make_record("placeholder"))
            stdout = io.StringIO()
            with (
                patch.object(cli.ClaudeCodeAppService, "for_current_workspace", return_value=fake_service),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                exit_code = cli.main(["请直接回答 hi"])

            self.assertEqual(exit_code, 0)
            rendered = stdout.getvalue()
            self.assertIn("你好！\nstatus: created", rendered)
            self.assertNotIn("assistant_response:\n你好！", rendered)


if __name__ == "__main__":
    unittest.main()
