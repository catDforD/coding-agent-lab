from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from claude_code.model_client import FakeModelClient, ModelTurnResult, ToolRequest
from claude_code.runtime import run_core_loop
from claude_code.session_store import SessionRecord, utc_now_iso


PROJECT_DIR = Path(__file__).resolve().parent.parent


class Phase5ReadOnlyValidationTest(unittest.TestCase):
    """验证 Phase 5 第 1 点的只读闭环。

    这里直接对应 `docs/claude-code/claude-code-todo.md` 的
    “读代码并解释”任务，并沿着《claude-code-study.md》的 4 / 4.1 / 5.3
    去检查最小 live runtime 是否真的跑出了:
    gather context -> search/read_file -> verify results
    """

    def make_record(self, task: str) -> SessionRecord:
        now = utc_now_iso()
        record = SessionRecord(
            session_id="phase5-read-only",
            created_at=now,
            updated_at=now,
            events=[],
        )
        record.add_user_message(task)
        return record

    def make_workspace(self, root: Path) -> None:
        package_dir = root / "claude_code"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "session_store.py").write_text(
            "\n".join(
                [
                    "class SessionStore:",
                    "    \"\"\"Keep session events on disk.\"\"\"",
                    "",
                    "    def save(self, session_id: str, payload: dict[str, object]) -> None:",
                    "        self._write_json(session_id, payload)",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "CLAUDE.md").write_text(
            "先读代码，再解释职责，不要猜测未读取的实现。\n",
            encoding="utf-8",
        )
        (root / "MEMORY.md").write_text(
            "SessionStore 负责把会话事件持久化到磁盘。\n",
            encoding="utf-8",
        )

    def test_live_read_only_loop_can_read_code_and_explain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请读代码并解释 SessionStore 在这里负责什么")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="",
                        tool_calls=[
                            ToolRequest(
                                call_id="call-1",
                                name="search",
                                arguments={"query": "SessionStore"},
                            )
                        ],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "search",
                                "arguments": "{\"query\": \"SessionStore\"}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-2",
                        output_text="",
                        tool_calls=[
                            ToolRequest(
                                call_id="call-2",
                                name="read_file",
                                arguments={"path": "claude_code/session_store.py"},
                            )
                        ],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-2",
                                "name": "read_file",
                                "arguments": "{\"path\": \"claude_code/session_store.py\"}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-3",
                        output_text=(
                            "SessionStore 在这个最小实现里负责把会话 payload 落盘，"
                            "save 会把 session_id 和事件内容交给底层写文件逻辑。"
                        ),
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 56},
                    ),
                ]
            )

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
            )

            self.assertEqual(result.verify.status, "completed")
            self.assertEqual(result.act.executed_tools, ["search", "read_file"])
            self.assertIn("SessionStore 在这个最小实现里负责把会话 payload 落盘", result.act.final_output)
            self.assertEqual(
                [event.kind for event in result.emitted_events],
                ["tool_call", "tool_result", "tool_call", "tool_result", "model_response"],
            )
            initial_text = client.requests[0]["input_items"][0]["content"][0]["text"]
            self.assertIn("Loaded rules:", initial_text)
            self.assertIn("先读代码，再解释职责", initial_text)
            self.assertIn("SessionStore 负责把会话事件持久化到磁盘。", initial_text)


class Phase5WriteValidationTest(unittest.TestCase):
    """验证 Phase 5 第 2 点的写入闭环。

    当前仓库还没有开放 live 写入工具，所以这里按 README 里声明的最小边界，
    走 `tool-direct + continue-last` 来复现《claude-code-study.md》4.1 的
    “测试失败 -> 读代码 -> 修改 -> 重跑测试”链路。
    """

    def run_cli(
        self,
        *args: str,
        state_dir: Path,
        workspace_root: Path,
        extra_env: dict[str, str] | None = None,
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
            capture_output=True,
            text=True,
            check=False,
        )

    def make_workspace(self, root: Path) -> Path:
        (root / "calculator.py").write_text(
            "\n".join(
                [
                    "def add(left: int, right: int) -> int:",
                    "    return left - right",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "test_calculator.py").write_text(
            "\n".join(
                [
                    "import unittest",
                    "",
                    "from calculator import add",
                    "",
                    "",
                    "class CalculatorTest(unittest.TestCase):",
                    "    def test_add(self) -> None:",
                    "        self.assertEqual(add(2, 3), 5)",
                    "",
                    "",
                    "if __name__ == \"__main__\":",
                    "    unittest.main()",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        rules_dir = root / ".claude-code"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_path = rules_dir / "permission-rules.json"
        rules_path.write_text(
            json.dumps(
                {
                    "bash": {
                        "allowlist": ["python -m unittest -q"],
                    },
                    "edit": {
                        "allowlist": ["calculator.py"],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "init"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return rules_path

    def read_session_payload(self, state_dir: Path) -> dict[str, object]:
        session_id = (state_dir / "latest_session.txt").read_text(encoding="utf-8").strip()
        session_path = state_dir / "sessions" / f"{session_id}.json"
        return json.loads(session_path.read_text(encoding="utf-8"))

    def test_tool_direct_loop_can_fix_failing_test_and_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace_root = root / "workspace"
            state_dir = root / "state"
            workspace_root.mkdir()
            rules_path = self.make_workspace(workspace_root)
            env = {"CLAUDE_CODE_PERMISSION_RULES": str(rules_path)}

            first_run = self.run_cli(
                "--tool-direct",
                "bash",
                "python -m unittest -q",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env=env,
            )
            self.assertEqual(first_run.returncode, 1, first_run.stderr)
            self.assertIn("verify_status: loop-needs-attention", first_run.stdout)
            first_payload = self.read_session_payload(state_dir)
            self.assertIn(
                "FAILED (failures=1)",
                first_payload["events"][2]["payload"]["tool_output"]["stderr"],
            )

            inspect_run = self.run_cli(
                "--tool-direct",
                "--continue-last",
                "read_file",
                "calculator.py",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env=env,
            )
            self.assertEqual(inspect_run.returncode, 0, inspect_run.stderr)
            self.assertIn("executed_tools: read_file", inspect_run.stdout)
            inspect_payload = self.read_session_payload(state_dir)
            self.assertIn(
                "return left - right",
                inspect_payload["events"][6]["payload"]["tool_output"]["content"],
            )

            edit_run = self.run_cli(
                "--tool-direct",
                "--continue-last",
                "edit calculator.py -- return left - right -- return left + right",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env=env,
            )
            self.assertEqual(edit_run.returncode, 0, edit_run.stderr)
            self.assertIn("executed_tools: edit", edit_run.stdout)
            self.assertEqual(
                (workspace_root / "calculator.py").read_text(encoding="utf-8"),
                "def add(left: int, right: int) -> int:\n    return left + right\n",
            )

            rerun = self.run_cli(
                "--tool-direct",
                "--continue-last",
                "bash",
                "python -m unittest -q",
                state_dir=state_dir,
                workspace_root=workspace_root,
                extra_env=env,
            )
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertIn("verify_status: completed", rerun.stdout)

            payload = self.read_session_payload(state_dir)
            self.assertEqual(len(payload["events"]), 16)
            self.assertEqual(payload["events"][0]["payload"]["content"], "bash python -m unittest -q")
            self.assertEqual(payload["events"][-2]["payload"]["tool_name"], "bash")
            self.assertEqual(payload["events"][-2]["payload"]["status"], "ok")
            self.assertEqual(
                payload["events"][-2]["payload"]["tool_output"]["command"],
                "python -m unittest -q",
            )
            self.assertIn(
                "FAILED (failures=1)",
                payload["events"][2]["payload"]["tool_output"]["stderr"],
            )
            self.assertIn(
                "OK",
                payload["events"][-2]["payload"]["tool_output"]["stderr"],
            )


if __name__ == "__main__":
    unittest.main()
