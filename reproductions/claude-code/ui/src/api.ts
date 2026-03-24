import type {
  RunResponse,
  RuntimeStatus,
  SessionDetailResponse,
  SessionListResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        detail = payload.detail;
      }
    } catch {
      // Ignore JSON parsing failures for non-JSON responses.
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

export function fetchRuntimeStatus(): Promise<RuntimeStatus> {
  return request<RuntimeStatus>("/api/runtime/status");
}

export function fetchSessions(): Promise<SessionListResponse> {
  return request<SessionListResponse>("/api/sessions");
}

export function fetchSession(sessionId: string): Promise<SessionDetailResponse> {
  return request<SessionDetailResponse>(`/api/sessions/${sessionId}`);
}

export function createSession(task: string): Promise<RunResponse> {
  return request<RunResponse>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ task }),
  });
}

export function appendMessage(sessionId: string, task: string): Promise<RunResponse> {
  return request<RunResponse>(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ task }),
  });
}
