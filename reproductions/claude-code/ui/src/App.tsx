import { FormEvent, useEffect, useMemo, useState, useRef } from "react";
import { appendMessage, createSession, fetchRuntimeStatus, fetchSession, fetchSessions } from "./api";
import type {
  LoopSummary,
  RuntimeStatus,
  SessionEvent,
  SessionRecord,
  SessionSummary,
} from "./types";

interface ToolStep {
  id: string;
  call?: SessionEvent;
  result?: SessionEvent;
}

const EMPTY_STATUS: RuntimeStatus = {
  ready: false,
  model: null,
  base_url: null,
  state_dir: "",
  missing_config: [],
};

function formatDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function extractText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value == null) {
    return "";
  }
  return JSON.stringify(value, null, 2);
}

function groupToolSteps(events: SessionEvent[]): ToolStep[] {
  const steps = new Map<string, ToolStep>();

  for (const event of events) {
    if (event.kind !== "tool_call" && event.kind !== "tool_result") {
      continue;
    }

    const callId = String(event.payload.call_id ?? "");
    const stepIndex = String(event.payload.step_index ?? "");
    const toolName = String(event.payload.tool_name ?? "tool");
    const key = callId || `${stepIndex}:${toolName}`;
    const current = steps.get(key) ?? { id: key };

    if (event.kind === "tool_call") {
      current.call = event;
    } else {
      current.result = event;
    }
    steps.set(key, current);
  }

  return Array.from(steps.values());
}

export default function App() {
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus>(EMPTY_STATUS);
  const [sessionList, setSessionList] = useState<SessionSummary[]>([]);
  const [selectedSession, setSelectedSession] = useState<SessionRecord | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loopSummary, setLoopSummary] = useState<LoopSummary | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void refreshWorkbench();
  }, []);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [selectedSession?.events.length, isSubmitting]);

  async function refreshWorkbench() {
    setErrorMessage(null);
    try {
      const [status, sessions] = await Promise.all([fetchRuntimeStatus(), fetchSessions()]);
      setRuntimeStatus(status);
      setSessionList(sessions.sessions);

      if (selectedSessionId) {
        const detail = await fetchSession(selectedSessionId);
        setSelectedSession(detail.session);
        return;
      }

      if (sessions.sessions[0]) {
        const detail = await fetchSession(sessions.sessions[0].session_id);
        setSelectedSessionId(detail.session.session_id);
        setSelectedSession(detail.session);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load workbench.");
    }
  }

  async function handleSelectSession(sessionId: string) {
    setSelectedSessionId(sessionId);
    setLoopSummary(null);
    setErrorMessage(null);
    try {
      const detail = await fetchSession(sessionId);
      setSelectedSession(detail.session);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load session.");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const task = composerValue.trim();
    if (!task || isSubmitting || !runtimeStatus.ready) {
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const response = selectedSessionId
        ? await appendMessage(selectedSessionId, task)
        : await createSession(task);
      setSelectedSession(response.session);
      setSelectedSessionId(response.session.session_id);
      setLoopSummary(response.loop);
      setComposerValue("");

      const sessions = await fetchSessions();
      setSessionList(sessions.sessions);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Request failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  const toolSteps = useMemo(
    () => groupToolSteps(selectedSession?.events ?? []),
    [selectedSession],
  );

  const latestAssistant = useMemo(() => {
    const events = selectedSession?.events ?? [];
    const modelEvents = events.filter((item) => item.kind === "model_response");
    return modelEvents.length > 0 ? modelEvents[modelEvents.length - 1] : null;
  }, [selectedSession]);

  const messageEvents = useMemo(
    () => (selectedSession?.events ?? []).filter((event) => event.kind !== "tool_call" && event.kind !== "tool_result"),
    [selectedSession],
  );

  const latestUserMessage = useMemo(() => {
    const userMessages = messageEvents.filter((event) => event.kind === "user_message");
    const latest = userMessages.length > 0 ? userMessages[userMessages.length - 1] : null;
    return latest ? extractText(latest.payload.content) : "";
  }, [messageEvents]);

  const conversationTitle = selectedSession
    ? sessionList.find((item) => item.session_id === selectedSession.session_id)?.latest_task ||
      latestUserMessage ||
      "Current session"
    : "Start a new conversation";

  return (
    <div className="shell">
      <div className="shell__grain" />
      <header className="shell__header">
        <div>
          <p className="eyebrow">Cleanroom Interface</p>
          <h1>Claude Code Workbench</h1>
        </div>
        <div className="header__meta">
          <span className={`status-pill ${runtimeStatus.ready ? "status-pill--ready" : "status-pill--blocked"}`}>
            {runtimeStatus.ready ? "live ready" : "live blocked"}
          </span>
          <span className="header__model">{runtimeStatus.model ?? "no model"}</span>
        </div>
      </header>

      {!runtimeStatus.ready && (
        <section className="banner">
          <strong>Live runtime unavailable.</strong>
          <span>{runtimeStatus.missing_config.join(" ") || "Missing OpenAI configuration."}</span>
        </section>
      )}

      {errorMessage && (
        <section className="banner banner--error" role="alert">
          <strong>Request failed.</strong>
          <span>{errorMessage}</span>
        </section>
      )}

      <main className="workbench">
        <aside className="panel panel--sessions">
          <div className="panel__heading">
            <div>
              <p className="eyebrow">Sessions</p>
              <h2>Rail</h2>
            </div>
            <button
              className="ghost-button"
              type="button"
              onClick={() => {
                setSelectedSession(null);
                setSelectedSessionId(null);
                setLoopSummary(null);
              }}
            >
              New
            </button>
          </div>
          <div className="session-list">
            {sessionList.length === 0 && <p className="muted">No local sessions yet.</p>}
            {sessionList.map((session) => (
              <button
                key={session.session_id}
                className={`session-card ${selectedSessionId === session.session_id ? "session-card--active" : ""}`}
                type="button"
                onClick={() => void handleSelectSession(session.session_id)}
              >
                <span className="session-card__task">{session.latest_task || "Untitled session"}</span>
                <span className="session-card__time">{formatDate(session.updated_at)}</span>
                <span className="session-card__excerpt">{session.last_response_excerpt || "No model response yet."}</span>
              </button>
            ))}
          </div>
        </aside>

        <section className="panel panel--conversation">
          <div className="panel__heading panel__heading--conversation">
            <div>
              <p className="eyebrow">Conversation</p>
              <h2>Stage</h2>
            </div>
            <div className="conversation__meta">
              <span>{selectedSession ? `${selectedSession.events.length} events` : "Fresh thread"}</span>
              <span>{selectedSession ? selectedSession.session_id.slice(0, 8) : "new"}</span>
            </div>
          </div>
          <div className="conversation-shell">
            <div className="conversation-ribbon">
              <div>
                <p className="eyebrow">Thread</p>
                <h3>{conversationTitle}</h3>
              </div>
              <p className="conversation-ribbon__hint">
                Chat-first layout. Tool traces stay in the inspector so the center column can behave like a conversation.
              </p>
            </div>

            <div className="timeline">
              {!selectedSession && (
                <div className="welcome-card">
                  <p className="eyebrow">V1 Scope</p>
                  <h3>Launch a read-only agent turn.</h3>
                  <p>
                    Start with a repo question. The workbench will run a full live turn, then reveal the answer and tool
                    trace once the loop completes.
                  </p>
                </div>
              )}

              {selectedSession && (
                <div className="thread">
                  {messageEvents.map((event, index) => {
                    const isAssistant = event.kind === "model_response";
                    return (
                      <div
                        key={event.event_id}
                        className={`message-row ${isAssistant ? "message-row--assistant" : "message-row--user"}`}
                        style={{ animationDelay: `${index * 40}ms` }}
                      >
                        <article
                          className={`message-card ${isAssistant ? "message-card--assistant" : "message-card--user"}`}
                        >
                          <div className="message-card__meta">
                            <span>{isAssistant ? "assistant" : "user"}</span>
                            <time>{formatDate(event.created_at)}</time>
                          </div>
                          <pre>{extractText(event.payload.content)}</pre>
                        </article>
                      </div>
                    );
                  })}

                  {isSubmitting && (
                    <div className="message-row message-row--assistant">
                      <div className="loading-card">
                        <span className="loading-card__dot" />
                        <div>
                          <strong>Running live turn</strong>
                          <p>Waiting for the current gather → act → verify cycle to finish.</p>
                        </div>
                      </div>
                    </div>
                  )}

                  <div ref={threadEndRef} />
                </div>
              )}
            </div>

            <form className="composer" onSubmit={handleSubmit}>
              <label className="composer__label" htmlFor="task-input">
                Task
              </label>
              <textarea
                id="task-input"
                value={composerValue}
                onChange={(event) => setComposerValue(event.target.value)}
                placeholder="Ask about the repository, inspect a file, or continue the current session…"
                rows={4}
                disabled={!runtimeStatus.ready || isSubmitting}
              />
              <div className="composer__actions">
                <span className="muted">
                  {selectedSessionId ? "Submitting to current session" : "Submitting as a new session"}
                </span>
                <button className="submit-button" type="submit" disabled={!runtimeStatus.ready || isSubmitting}>
                  {isSubmitting ? "Running…" : selectedSessionId ? "Continue Session" : "Start Session"}
                </button>
              </div>
            </form>
          </div>
        </section>

        <aside className="panel panel--inspector">
          <div className="panel__heading">
            <div>
              <p className="eyebrow">Run</p>
              <h2>Inspector</h2>
            </div>
          </div>

          <div className="inspector-content">
            <section className="inspector-block">
              <h3>Loop summary</h3>
              {loopSummary ? (
                <dl className="stat-grid">
                  <div>
                    <dt>Mode</dt>
                    <dd>{loopSummary.mode}</dd>
                  </div>
                  <div>
                    <dt>Steps</dt>
                    <dd>{loopSummary.step_count}</dd>
                  </div>
                  <div>
                    <dt>Finish</dt>
                    <dd>{loopSummary.finish_reason}</dd>
                  </div>
                  <div>
                    <dt>Verify</dt>
                    <dd>{loopSummary.verify_status}</dd>
                  </div>
                </dl>
              ) : (
                <p className="muted">Run a new turn to inspect its gather/act/verify result.</p>
              )}
            </section>

            <section className="inspector-block">
              <h3>Tool steps</h3>
              {toolSteps.length === 0 && <p className="muted">No tool activity for this session yet.</p>}
              <div className="tool-stack">
                {toolSteps.map((step) => (
                  <details key={step.id} className="tool-card">
                    <summary>
                      <span>{String(step.call?.payload.tool_name ?? step.result?.payload.tool_name ?? "tool")}</span>
                      <span>
                        step {String(step.call?.payload.step_index ?? step.result?.payload.step_index ?? "—")}
                      </span>
                    </summary>
                    <div className="tool-card__body">
                      <div>
                        <p className="eyebrow">Input</p>
                        <pre>{extractText(step.call?.payload.tool_input)}</pre>
                      </div>
                      <div>
                        <p className="eyebrow">Output</p>
                        <pre>{extractText(step.result?.payload.tool_output)}</pre>
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            </section>

            <section className="inspector-block">
              <h3>Latest assistant event</h3>
              {latestAssistant ? (
                <div className="assistant-meta">
                  <p>{String(latestAssistant.payload.model ?? "unknown model")}</p>
                  <p>{String(latestAssistant.payload.finish_reason ?? "n/a")}</p>
                  <p>{formatDate(latestAssistant.created_at)}</p>
                </div>
              ) : (
                <p className="muted">No assistant event yet.</p>
              )}
            </section>
          </div>
        </aside>
      </main>
    </div>
  );
}
