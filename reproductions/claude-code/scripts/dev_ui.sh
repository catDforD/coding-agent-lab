#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="${APP_DIR}/ui"
RUNTIME_DIR="${APP_DIR}/.runtime"
BACKEND_LOG="${RUNTIME_DIR}/backend.log"
FRONTEND_LOG="${RUNTIME_DIR}/frontend.log"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
KILL_CONFLICTING_PORTS="${KILL_CONFLICTING_PORTS:-1}"

mkdir -p "${RUNTIME_DIR}"

port_in_use() {
  local port="$1"
  ss -ltnH "( sport = :${port} )" | grep -q .
}

pids_on_port() {
  local port="$1"
  ss -ltnpH "( sport = :${port} )" 2>/dev/null | grep -o 'pid=[0-9]\+' | cut -d= -f2 | sort -u
}

stop_port_processes() {
  local port="$1"
  local pids

  pids="$(pids_on_port "${port}" || true)"
  if [[ -z "${pids}" ]]; then
    return 0
  fi

  echo "[claude-code-ui] stopping processes on port ${port}: ${pids//$'\n'/ }"
  while IFS= read -r pid; do
    [[ -n "${pid}" ]] || continue
    kill "${pid}" 2>/dev/null || true
  done <<< "${pids}"

  for _ in 1 2 3 4 5; do
    if ! port_in_use "${port}"; then
      return 0
    fi
    sleep 1
  done

  pids="$(pids_on_port "${port}" || true)"
  if [[ -n "${pids}" ]]; then
    echo "[claude-code-ui] force killing processes on port ${port}: ${pids//$'\n'/ }"
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] || continue
      kill -9 "${pid}" 2>/dev/null || true
    done <<< "${pids}"
  fi

  sleep 1
  if port_in_use "${port}"; then
    echo "[claude-code-ui] failed to free port ${port}" >&2
    exit 1
  fi
}

cleanup() {
  local exit_code=$?
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill "${FRONTEND_PID}" 2>/dev/null || true
  fi
  exit "${exit_code}"
}

trap cleanup INT TERM EXIT

echo "[claude-code-ui] app dir: ${APP_DIR}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:${BACKEND_PORT}}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:${FRONTEND_PORT}}"

if [[ "${KILL_CONFLICTING_PORTS}" == "1" ]]; then
  if port_in_use "${BACKEND_PORT}"; then
    stop_port_processes "${BACKEND_PORT}"
  fi

  if port_in_use "${FRONTEND_PORT}"; then
    stop_port_processes "${FRONTEND_PORT}"
  fi
fi

if port_in_use "${BACKEND_PORT}" || port_in_use "${FRONTEND_PORT}"; then
  echo "[claude-code-ui] requested ports are still busy. Set KILL_CONFLICTING_PORTS=1 or free them manually." >&2
  exit 1
fi

echo "[claude-code-ui] ensuring Python dependencies"
(
  cd "${APP_DIR}"
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" uv sync >/dev/null
)

echo "[claude-code-ui] ensuring frontend dependencies"
(
  cd "${UI_DIR}"
  if [[ ! -d node_modules ]]; then
    npm install >/dev/null
  fi
)

echo "[claude-code-ui] starting backend on ${BACKEND_URL}"
(
  cd "${APP_DIR}"
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" \
    uv run uvicorn claude_code.web:app --host 127.0.0.1 --port "${BACKEND_PORT}" >"${BACKEND_LOG}" 2>&1
) &
BACKEND_PID=$!

echo "[claude-code-ui] starting frontend on ${FRONTEND_URL}"
(
  cd "${UI_DIR}"
  VITE_API_BASE="${BACKEND_URL}" npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" --strictPort >"${FRONTEND_LOG}" 2>&1
) &
FRONTEND_PID=$!

sleep 2

echo "[claude-code-ui] frontend: ${FRONTEND_URL}"
echo "[claude-code-ui] backend:  ${BACKEND_URL}"
echo "[claude-code-ui] logs:"
echo "  - ${BACKEND_LOG}"
echo "  - ${FRONTEND_LOG}"
echo "[claude-code-ui] Ctrl+C to stop both processes"

wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
