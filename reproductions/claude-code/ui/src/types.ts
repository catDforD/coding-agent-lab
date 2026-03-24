export type SessionEventKind = "user_message" | "tool_call" | "tool_result" | "model_response";

export interface SessionEvent {
  event_id: string;
  kind: SessionEventKind;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface SessionRecord {
  session_id: string;
  created_at: string;
  updated_at: string;
  events: SessionEvent[];
}

export interface SessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  latest_task: string;
  event_count: number;
  last_response_excerpt: string;
}

export interface LoopSummary {
  mode: string;
  step_count: number;
  executed_tools: string[];
  finish_reason: string;
  verify_status: string;
  verify_summary: string;
  assistant_response: string;
}

export interface RuntimeStatus {
  ready: boolean;
  model: string | null;
  base_url: string | null;
  state_dir: string;
  missing_config: string[];
}

export interface SessionListResponse {
  sessions: SessionSummary[];
}

export interface SessionDetailResponse {
  session: SessionRecord;
}

export interface RunResponse {
  session: SessionRecord;
  loop: LoopSummary;
}
