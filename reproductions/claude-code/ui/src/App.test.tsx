import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const fetchMock = vi.fn();

vi.stubGlobal("fetch", fetchMock);

describe("App", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    fetchMock.mockReset();
  });

  it("renders a blocked banner when runtime is unavailable", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ready: false,
          model: null,
          base_url: null,
          state_dir: "/tmp/state",
          missing_config: ["missing OPENAI_API_KEY"],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ sessions: [] }),
      });

    render(<App />);

    expect(await screen.findByText(/Live runtime unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/missing OPENAI_API_KEY/i)).toBeInTheDocument();
  });

  it("loads and renders the first session entry", async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ready: true,
          model: "gpt-5.4",
          base_url: null,
          state_dir: "/tmp/state",
          missing_config: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          sessions: [
            {
              session_id: "abc123",
              created_at: "2026-03-24T00:00:00+00:00",
              updated_at: "2026-03-24T00:00:00+00:00",
              latest_task: "summarize runtime",
              event_count: 2,
              last_response_excerpt: "runtime summary",
            },
          ],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          session: {
            session_id: "abc123",
            created_at: "2026-03-24T00:00:00+00:00",
            updated_at: "2026-03-24T00:00:00+00:00",
            events: [
              {
                event_id: "event-1",
                kind: "user_message",
                created_at: "2026-03-24T00:00:00+00:00",
                payload: { content: "summarize runtime" },
              },
              {
                event_id: "event-2",
                kind: "model_response",
                created_at: "2026-03-24T00:00:10+00:00",
                payload: { content: "runtime summary", model: "gpt-5.4", finish_reason: "completed" },
              },
            ],
          },
        }),
      });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("summarize runtime").length).toBeGreaterThanOrEqual(2);
      expect(screen.getAllByText("runtime summary").length).toBeGreaterThanOrEqual(2);
    });
  });
});
