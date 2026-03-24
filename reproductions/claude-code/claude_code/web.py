"""Claude Code cleanroom 的最小 Web API。"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .app_service import (
    ClaudeCodeAppService,
    RuntimeUnavailableError,
    serialize_loop_result,
    serialize_session,
)
from .config import reproduction_root


class TaskRequest(BaseModel):
    task: str = Field(min_length=1)


def _normalized_task(raw_task: str) -> str:
    task = raw_task.strip()
    if not task:
        raise HTTPException(status_code=422, detail="task must not be empty")
    return task


def create_app(service: ClaudeCodeAppService | None = None) -> FastAPI:
    app_service = service or ClaudeCodeAppService.for_current_workspace()
    app = FastAPI(title="Claude Code Cleanroom UI")

    @app.get("/api/runtime/status")
    def runtime_status() -> dict[str, object]:
        return app_service.runtime_status().to_dict()

    @app.get("/api/sessions")
    def list_sessions() -> dict[str, object]:
        return {"sessions": app_service.list_sessions()}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, object]:
        try:
            record = app_service.get_session(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"session": serialize_session(record)}

    @app.post("/api/sessions")
    def create_session(request: TaskRequest) -> dict[str, object]:
        try:
            record, loop_result = app_service.create_and_run_live(_normalized_task(request.task))
        except RuntimeUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "session": serialize_session(record),
            "loop": serialize_loop_result(loop_result),
        }

    @app.post("/api/sessions/{session_id}/messages")
    def append_message(session_id: str, request: TaskRequest) -> dict[str, object]:
        try:
            record, loop_result = app_service.append_and_run_live(session_id, _normalized_task(request.task))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "session": serialize_session(record),
            "loop": serialize_loop_result(loop_result),
        }

    dist_dir = reproduction_root() / "ui" / "dist"
    if dist_dir.exists():
        _mount_static_routes(app, dist_dir)
    else:
        @app.get("/")
        def dev_hint() -> HTMLResponse:
            return HTMLResponse(
                """
                <html>
                  <body style="font-family: monospace; padding: 24px; background: #101010; color: #f1f1ef;">
                    <h1>Claude Code UI API</h1>
                    <p>Frontend build not found. Run the Vite app in <code>reproductions/claude-code/ui</code>.</p>
                  </body>
                </html>
                """
            )

    return app


def _mount_static_routes(app: FastAPI, dist_dir: Path) -> None:
    index_file = dist_dir / "index.html"

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        candidate = dist_dir / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


app = create_app()


def main() -> int:
    uvicorn.run(
        "claude_code.web:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
    return 0
