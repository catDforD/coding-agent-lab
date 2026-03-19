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
    def run_cli(self, *args: str, state_dir: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CLAUDE_CODE_STATE_DIR"] = str(state_dir)
        return subprocess.run(
            [sys.executable, "-m", "claude_code", *args],
            cwd=PROJECT_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_create_session_from_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = self.run_cli("实现", "最小", "CLI", state_dir=Path(tmp_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status: created", result.stdout)
            self.assertIn("task_count: 1", result.stdout)
            self.assertIn("event_count: 4", result.stdout)
            self.assertIn("loop_phases: gather -> act -> verify", result.stdout)
            self.assertIn("verify_status: loop-ready", result.stdout)
            self.assertIn("act_strategy: change-code", result.stdout)
            self.assertIn("emitted_event_count: 3", result.stdout)
            self.assertIn(
                "emitted_event_kinds: tool_call,tool_result,model_response",
                result.stdout,
            )

            latest_session_path = Path(tmp_dir) / "latest_session.txt"
            session_id = latest_session_path.read_text(encoding="utf-8").strip()
            session_path = Path(tmp_dir) / "sessions" / f"{session_id}.json"
            payload = json.loads(session_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["session_id"], session_id)
            self.assertNotIn("user_tasks", payload)
            self.assertEqual(len(payload["events"]), 4)
            self.assertEqual(
                [event["kind"] for event in payload["events"]],
                ["user_message", "tool_call", "tool_result", "model_response"],
            )
            self.assertEqual(payload["events"][0]["payload"]["content"], "实现 最小 CLI")
            self.assertEqual(
                payload["events"][1]["payload"]["tool_name"],
                "runtime.next_action_router",
            )
            self.assertEqual(
                payload["events"][2]["payload"]["tool_output"]["strategy"],
                "change-code",
            )

    def test_continue_latest_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_dir = Path(tmp_dir)
            first = self.run_cli("第一轮任务", state_dir=state_dir)
            self.assertEqual(first.returncode, 0, first.stderr)

            second = self.run_cli(
                "--continue-last",
                "补充约束",
                state_dir=state_dir,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("status: resumed", second.stdout)
            self.assertIn("task_count: 2", second.stdout)
            self.assertIn("event_count: 8", second.stdout)
            self.assertIn("latest_task: 补充约束", second.stdout)
            self.assertIn("act_strategy: change-code", second.stdout)

            session_id = (state_dir / "latest_session.txt").read_text(encoding="utf-8").strip()
            session_path = state_dir / "sessions" / f"{session_id}.json"
            payload = json.loads(session_path.read_text(encoding="utf-8"))

            self.assertEqual(len(payload["events"]), 8)
            self.assertEqual(
                [event["payload"]["content"] for event in payload["events"] if event["kind"] == "user_message"],
                ["第一轮任务", "补充约束"],
            )

    def test_load_existing_session_without_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_dir = Path(tmp_dir)
            created = self.run_cli("第一轮任务", state_dir=state_dir)
            self.assertEqual(created.returncode, 0, created.stderr)

            session_id = (state_dir / "latest_session.txt").read_text(encoding="utf-8").strip()
            loaded = self.run_cli("--session-id", session_id, state_dir=state_dir)

            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertIn("status: loaded", loaded.stdout)
            self.assertIn("task_count: 1", loaded.stdout)
            self.assertIn("event_count: 7", loaded.stdout)
            self.assertIn("verify_status: loop-ready", loaded.stdout)

    def test_load_legacy_session_and_migrate_to_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_dir = Path(tmp_dir)
            sessions_dir = state_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            session_id = "legacy-session"
            payload = {
                "session_id": session_id,
                "created_at": "2026-03-19T00:00:00+00:00",
                "updated_at": "2026-03-19T00:00:00+00:00",
                "user_tasks": [
                    {
                        "content": "解释旧版 session",
                        "created_at": "2026-03-19T00:00:00+00:00",
                    }
                ],
            }
            (sessions_dir / f"{session_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (state_dir / "latest_session.txt").write_text(session_id + "\n", encoding="utf-8")

            result = self.run_cli("--session-id", session_id, state_dir=state_dir)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status: loaded", result.stdout)
            self.assertIn("event_count: 4", result.stdout)

            migrated = json.loads((sessions_dir / f"{session_id}.json").read_text(encoding="utf-8"))
            self.assertNotIn("user_tasks", migrated)
            self.assertEqual(
                [event["kind"] for event in migrated["events"]],
                ["user_message", "tool_call", "tool_result", "model_response"],
            )
            self.assertEqual(migrated["events"][0]["payload"]["content"], "解释旧版 session")


if __name__ == "__main__":
    unittest.main()
