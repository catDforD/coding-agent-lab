from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_code.checkpoints import CheckpointStore
from claude_code.model_client import (
    FakeModelClient,
    ModelTextDeltaEvent,
    ModelTurnCompletedEvent,
    ModelTurnResult,
    ToolRequest,
)
from claude_code.permissions import InteractivePermissionGate
from claude_code.runtime import run_core_loop
from claude_code.session_store import SessionRecord, utc_now_iso


class FakeStreamingModelClient:
    def __init__(self, turns: list[list[object]], *, model_name: str = "fake-streaming-model") -> None:
        self._turns = [list(turn) for turn in turns]
        self.model_name = model_name
        self.requests: list[dict[str, object]] = []

    def stream_response(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, object]],
        tools: list[dict[str, object]],
        previous_response_id: str | None = None,
    ):
        self.requests.append(
            {
                "instructions": instructions,
                "input_items": input_items,
                "tools": tools,
                "previous_response_id": previous_response_id,
            }
        )
        if not self._turns:
            raise AssertionError("fake streaming model has no scripted turn left")
        return iter(self._turns.pop(0))


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

    def test_live_agent_streams_text_and_still_records_final_model_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请直接回答 hi")
            client = FakeStreamingModelClient(
                [
                    [
                        ModelTextDeltaEvent(delta="h"),
                        ModelTextDeltaEvent(delta="i"),
                        ModelTurnCompletedEvent(
                            result=ModelTurnResult(
                                response_id="resp-stream-1",
                                output_text="hi",
                                tool_calls=[],
                                output_items=[],
                                finish_reason="completed",
                                usage={"total_tokens": 12},
                            )
                        ),
                    ]
                ]
            )
            streamed: list[str] = []

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
                text_delta_callback=streamed.append,
            )

            self.assertEqual("".join(streamed), "hi")
            self.assertEqual(result.verify.status, "completed")
            self.assertEqual(result.act.final_output, "hi")
            self.assertEqual(record.events[-1].kind, "model_response")
            self.assertEqual(record.events[-1].payload["content"], "hi")

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

    def test_live_agent_streaming_keeps_tool_loop_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请解释 SessionStore 在哪里")
            client = FakeStreamingModelClient(
                [
                    [
                        ModelTextDeltaEvent(delta="先看一下。"),
                        ModelTurnCompletedEvent(
                            result=ModelTurnResult(
                                response_id="resp-stream-1",
                                output_text="先看一下。",
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
                            )
                        ),
                    ],
                    [
                        ModelTextDeltaEvent(delta="SessionStore 在 notes.md。"),
                        ModelTurnCompletedEvent(
                            result=ModelTurnResult(
                                response_id="resp-stream-2",
                                output_text="SessionStore 在 notes.md。",
                                tool_calls=[],
                                output_items=[],
                                finish_reason="completed",
                                usage={"total_tokens": 18},
                            )
                        ),
                    ],
                ]
            )
            streamed: list[str] = []

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
                text_delta_callback=streamed.append,
            )

            self.assertEqual(streamed, ["先看一下。", "SessionStore 在 notes.md。"])
            self.assertEqual(result.verify.status, "completed")
            self.assertEqual(result.act.executed_tools, ["search"])
            self.assertEqual(
                [event.kind for event in result.emitted_events],
                ["tool_call", "tool_result", "model_response"],
            )
            self.assertEqual(record.events[-1].payload["content"], "SessionStore 在 notes.md。")

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

    def test_live_agent_can_edit_with_permission_gate_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            workspace_root = temp_root / "workspace"
            workspace_root.mkdir()
            self.make_workspace(workspace_root)
            record = self.make_record("请把 sample.txt 里的 beta 改成 gamma")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="",
                        tool_calls=[
                            ToolRequest(
                                call_id="call-1",
                                name="edit",
                                arguments={
                                    "path": "src/sample.txt",
                                    "old_text": "beta",
                                    "new_text": "gamma",
                                },
                            )
                        ],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "edit",
                                "arguments": (
                                    "{\"path\": \"src/sample.txt\", "
                                    "\"old_text\": \"beta\", \"new_text\": \"gamma\"}"
                                ),
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-2",
                        output_text="sample.txt 已更新。",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 28},
                    ),
                ]
            )
            checkpoint_store = CheckpointStore(temp_root / "state" / "checkpoints")
            permission_gate = InteractivePermissionGate(input_fn=lambda _: "yes")

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
                permission_gate=permission_gate,
                checkpoint_store=checkpoint_store,
            )

            self.assertEqual(result.verify.status, "completed")
            self.assertEqual(result.act.executed_tools, ["edit"])
            self.assertEqual(
                (workspace_root / "src" / "sample.txt").read_text(encoding="utf-8"),
                "alpha\ngamma\n",
            )
            self.assertTrue((temp_root / "state" / "checkpoints" / "latest_edit.json").exists())
            second_request_items = client.requests[1]["input_items"]
            self.assertEqual(second_request_items[2]["type"], "function_call_output")
            self.assertIn("\"status\": \"ok\"", second_request_items[2]["output"])
            self.assertIn("edit and bash", client.requests[0]["instructions"])

    def test_live_agent_can_run_bash_with_permission_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            record = self.make_record("请运行命令并返回结果")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="",
                        tool_calls=[
                            ToolRequest(
                                call_id="call-1",
                                name="bash",
                                arguments={"command": "printf ready"},
                            )
                        ],
                        output_items=[
                            {
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "bash",
                                "arguments": "{\"command\": \"printf ready\"}",
                            }
                        ],
                        finish_reason="tool_calls",
                        usage=None,
                    ),
                    ModelTurnResult(
                        response_id="resp-2",
                        output_text="命令输出是 ready。",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 24},
                    ),
                ]
            )
            permission_gate = InteractivePermissionGate(input_fn=lambda _: "y")

            result = run_core_loop(
                record,
                workspace_root,
                tool_direct=False,
                max_steps=6,
                model_client=client,
                permission_gate=permission_gate,
            )

            self.assertEqual(result.verify.status, "completed")
            self.assertEqual(result.act.executed_tools, ["bash"])
            second_request_items = client.requests[1]["input_items"]
            self.assertIn("\"command\": \"printf ready\"", second_request_items[2]["output"])
            self.assertIn("\"stdout\": \"ready\"", second_request_items[2]["output"])

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

    def test_live_agent_initial_input_includes_rules_memory_history_and_tool_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            workspace_root = temp_root / "workspace" / "nested"
            workspace_root.mkdir(parents=True)
            self.make_workspace(workspace_root)

            (temp_root / "workspace" / "CLAUDE.md").write_text(
                "项目规则：先读代码，再下结论。\n",
                encoding="utf-8",
            )
            user_rules_path = temp_root / "user-claude.md"
            user_rules_path.write_text(
                "用户规则：回答保持简洁。\n",
                encoding="utf-8",
            )
            (workspace_root / "MEMORY.md").write_text(
                "已知信息：SessionStore 负责会话持久化。\n",
                encoding="utf-8",
            )

            now = utc_now_iso()
            record = SessionRecord(
                session_id="test-session",
                created_at=now,
                updated_at=now,
                events=[],
            )
            record.add_user_message("先看看 SessionStore")
            record.add_tool_call(tool_name="search", tool_input={"query": "SessionStore"}, step_index=1)
            record.add_tool_result(
                tool_name="search",
                status="ok",
                tool_output={"query": "SessionStore", "matches": ["notes.md:1:SessionStore lives here"]},
                step_index=1,
            )
            record.add_model_response(
                "SessionStore 在 notes.md 里被提到过。",
                strategy="live-responses-agent",
                next_action="wait for next user task",
                mode="live",
                model="fake-responses-model",
                finish_reason="completed",
                step_index=1,
            )
            record.add_user_message("继续总结刚才的结果")

            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="已结合规则和上下文完成总结。",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 21},
                    )
                ]
            )

            with patch.dict(
                os.environ,
                {"CLAUDE_CODE_USER_RULES_FILE": str(user_rules_path)},
                clear=False,
            ):
                result = run_core_loop(
                    record,
                    workspace_root,
                    tool_direct=False,
                    max_steps=6,
                    model_client=client,
                )

            request = client.requests[0]
            initial_text = request["input_items"][0]["content"][0]["text"]

            self.assertEqual(result.verify.status, "completed")
            self.assertIn("Loaded rules:", initial_text)
            self.assertIn("项目规则：先读代码，再下结论。", initial_text)
            self.assertIn("用户规则：回答保持简洁。", initial_text)
            self.assertIn("已知信息：SessionStore 负责会话持久化。", initial_text)
            self.assertIn("Recent session transcript:", initial_text)
            self.assertIn("Assistant: SessionStore 在 notes.md 里被提到过。", initial_text)
            self.assertIn("Recent tool outputs:", initial_text)
            self.assertIn('"query": "SessionStore"', initial_text)
            self.assertIn("Treat any loaded workspace rules and memory", request["instructions"])
            self.assertIn("2 recent user message(s)", result.gather.summary)

    def test_live_agent_compacts_older_tool_outputs_before_older_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)

            now = utc_now_iso()
            record = SessionRecord(
                session_id="test-session",
                created_at=now,
                updated_at=now,
                events=[],
            )

            for index in range(8):
                step_index = index + 1
                record.add_user_message(f"历史任务 {index}")
                record.add_tool_call(tool_name="search", tool_input={"query": f"legacy-{index}"}, step_index=step_index)
                record.add_tool_result(
                    tool_name="search",
                    status="ok",
                    tool_output={"query": f"legacy-{index}", "matches": [f"raw-output-{index}"]},
                    step_index=step_index,
                )
                record.add_model_response(
                    f"历史回答 {index}",
                    strategy="live-responses-agent",
                    next_action="continue",
                    mode="live",
                    model="fake-responses-model",
                    finish_reason="completed",
                    step_index=step_index,
                )

            record.add_user_message("现在只总结最近情况")
            client = FakeModelClient(
                [
                    ModelTurnResult(
                        response_id="resp-1",
                        output_text="已完成压缩后的总结。",
                        tool_calls=[],
                        output_items=[],
                        finish_reason="completed",
                        usage={"total_tokens": 42},
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

            request = client.requests[0]
            initial_text = request["input_items"][0]["content"][0]["text"]

            self.assertEqual(result.verify.status, "completed")
            self.assertIn("Compacted session summary:", initial_text)
            self.assertIn("Earlier tool activity without raw outputs: search x3", initial_text)
            self.assertIn("Dropped raw tool outputs during compaction: 3", initial_text)
            self.assertNotIn("raw-output-0", initial_text)
            self.assertNotIn("raw-output-1", initial_text)
            self.assertNotIn("raw-output-2", initial_text)
            self.assertIn("raw-output-7", initial_text)
            self.assertIn("summarized 12 earlier event(s) and dropped 3 old tool output(s)", result.gather.summary)


if __name__ == "__main__":
    unittest.main()
