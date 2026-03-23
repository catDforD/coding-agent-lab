from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_code.model_client import FakeModelClient, ModelTurnResult, ToolRequest
from claude_code.runtime import run_core_loop
from claude_code.session_store import SessionRecord, utc_now_iso


class LiveRuntimeTest(unittest.TestCase):
    def make_record(self, task: str) -> SessionRecord:
        now = utc_now_iso()
        record = SessionRecord(
            session_id="test-session",
            created_at=now,
            updated_at=now,
            events=[],
        )
        record.add_user_message(task)
        return record

    def make_workspace(self, root: Path) -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "sample.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        (root / "notes.md").write_text("SessionStore lives here\n", encoding="utf-8")

    def test_live_agent_can_answer_without_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请直接回答 hi")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="hi",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 12},
                    )
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
            self.assertEqual(result.act.final_output, "hi")
            self.assertEqual(result.act.executed_tools, [])
            self.assertEqual(record.events[-1].payload["mode"], "live")

    def test_live_agent_can_search_then_read_then_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请解释 SessionStore 在哪里")
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
                                arguments={"path": "notes.md"},
                            )
                        ],
                        output_items=[
                            {
                                "type": "reasoning",
                                "id": "rs_1",
                                "summary": [],
                            },
                            {
                                "type": "function_call",
                                "call_id": "call-2",
                                "name": "read_file",
                                "arguments": "{\"path\": \"notes.md\"}",
                            },
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-3",
                        output_text="SessionStore appears in notes.md.",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 34},
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
            self.assertEqual(result.act.step_count, 3)
            self.assertIn("SessionStore appears", result.act.final_output)
            second_request_items = client.requests[1]["input_items"]
            self.assertEqual(second_request_items[1]["type"], "function_call")
            self.assertEqual(second_request_items[1]["call_id"], "call-1")
            self.assertEqual(second_request_items[2]["type"], "function_call_output")
            self.assertEqual(second_request_items[2]["call_id"], "call-1")
            self.assertEqual(
                [event.kind for event in result.emitted_events],
                ["tool_call", "tool_result", "tool_call", "tool_result", "model_response"],
            )

    def test_live_agent_rejects_unknown_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请修改 sample.txt")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="",
                        tool_calls=[
                            ToolRequest(
                                call_id="call-1",
                                name="edit",
                                arguments={"path": "src/sample.txt", "old_text": "beta", "new_text": "gamma"},
                            )
                        ],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "edit",
                                "arguments": "{\"path\": \"src/sample.txt\", \"old_text\": \"beta\", \"new_text\": \"gamma\"}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    )
                ]
            )

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
            )

            self.assertEqual(result.verify.status, "invalid-tool-call")
            self.assertEqual(result.act.executed_tools, [])
            self.assertEqual(result.emitted_events, [])

    def test_live_agent_stops_at_max_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请检查仓库状态")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="",
                        tool_calls=[ToolRequest(call_id="call-1", name="git_status", arguments={})],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "git_status",
                                "arguments": "{}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-2",
                        output_text="",
                        tool_calls=[ToolRequest(call_id="call-2", name="git_status", arguments={})],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-2",
                                "name": "git_status",
                                "arguments": "{}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                ]
            )

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=2,
                model_client=client,
            )

            self.assertEqual(result.verify.status, "max-steps-reached")
            self.assertEqual(result.act.executed_tools, ["git_status", "git_status"])
            self.assertEqual(result.act.final_output, "")


if __name__ == "__main__":
    unittest.main()
