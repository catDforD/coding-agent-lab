from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
