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

            latest_session_path = Path(tmp_dir) / "latest_session.txt"
            session_id = latest_session_path.read_text(encoding="utf-8").strip()
            session_path = Path(tmp_dir) / "sessions" / f"{session_id}.json"
            payload = json.loads(session_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(len(payload["user_tasks"]), 1)
            self.assertEqual(payload["user_tasks"][0]["content"], "实现 最小 CLI")

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
            self.assertIn("latest_task: 补充约束", second.stdout)

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


if __name__ == "__main__":
    unittest.main()
