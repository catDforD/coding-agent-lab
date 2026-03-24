from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from claude_code.app_service import ClaudeCodeAppService
from claude_code.model_client import FakeModelClient, ModelTurnResult
from claude_code.session_store import SessionStore
from claude_code.web import create_app


class FakeWebService(ClaudeCodeAppService):
    def __init__(self, workspace: Path, store: SessionStore, client: FakeModelClient | None = None) -> None:
        super().__init__(workspace, store)
        self._client = client

    def runtime_status(self):  # type: ignore[override]
        status = super().runtime_status()
        if self._client is None:
            return status
        return status.__class__(
            ready=True,
            model=self._client.model_name,
            base_url=None,
            state_dir=status.state_dir,
            missing_config=[],
        )

    def _build_live_client(self):  # type: ignore[override]
        if self._client is None:
            raise RuntimeError("missing fake live client")
        return self._client


class WebApiTest(unittest.TestCase):
    def make_workspace(self, root: Path) -> None:
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "notes.md").write_text("SessionStore lives here\n", encoding="utf-8")

    def make_client(self, workspace_root: Path, model_client: FakeModelClient | None = None) -> TestClient:
        store = SessionStore(workspace_root / ".claude-code")
        service = FakeWebService(workspace_root, store, model_client)
        return TestClient(create_app(service))

    def test_runtime_status_reports_ready_when_fake_client_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(
                workspace_root,
                FakeModelClient(
                    [
                        ModelTurnResult(
                            response_id="resp-1",
                            output_text="hi",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage={"total_tokens": 12},
                        )
                    ],
                    model_name="fake-web-model",
                ),
            )

            response = client.get("/api/runtime/status")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ready"])
            self.assertEqual(payload["model"], "fake-web-model")

    def test_create_session_runs_live_turn_and_persists_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(
                workspace_root,
                FakeModelClient(
                    [
                        ModelTurnResult(
                            response_id="resp-1",
                            output_text="SessionStore lives in notes.md.",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage={"total_tokens": 30},
                        )
                    ]
                ),
            )

            response = client.post("/api/sessions", json={"task": "请总结 SessionStore"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["loop"]["assistant_response"], "SessionStore lives in notes.md.")
            self.assertEqual(payload["loop"]["verify_status"], "completed")
            self.assertEqual(payload["session"]["events"][0]["kind"], "user_message")
            self.assertEqual(payload["session"]["events"][-1]["kind"], "model_response")

    def test_append_message_extends_existing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(
                workspace_root,
                FakeModelClient(
                    [
                        ModelTurnResult(
                            response_id="resp-1",
                            output_text="first",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage={"total_tokens": 10},
                        ),
                        ModelTurnResult(
                            response_id="resp-2",
                            output_text="second",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage={"total_tokens": 10},
                        ),
                    ]
                ),
            )

            created = client.post("/api/sessions", json={"task": "first task"}).json()
            session_id = created["session"]["session_id"]

            response = client.post(f"/api/sessions/{session_id}/messages", json={"task": "second task"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["session"]["events"][-2]["kind"], "user_message")
            self.assertEqual(payload["session"]["events"][-1]["payload"]["content"], "second")

    def test_list_sessions_returns_latest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(
                workspace_root,
                FakeModelClient(
                    [
                        ModelTurnResult(
                            response_id="resp-1",
                            output_text="older answer",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage=None,
                        ),
                        ModelTurnResult(
                            response_id="resp-2",
                            output_text="newer answer",
                            tool_calls=[],
                            output_items=[],
                            finish_reason="completed",
                            usage=None,
                        ),
                    ]
                ),
            )

            client.post("/api/sessions", json={"task": "older"})
            client.post("/api/sessions", json={"task": "newer"})

            response = client.get("/api/sessions")

            self.assertEqual(response.status_code, 200)
            sessions = response.json()["sessions"]
            self.assertEqual(sessions[0]["latest_task"], "newer")
            self.assertEqual(sessions[1]["latest_task"], "older")

    def test_missing_session_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(workspace_root)

            response = client.get("/api/sessions/missing")

            self.assertEqual(response.status_code, 404)

    def test_blank_task_returns_422(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir)
            self.make_workspace(workspace_root)
            client = self.make_client(workspace_root)

            response = client.post("/api/sessions", json={"task": "   "})

            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
