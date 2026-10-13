import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "./api";
import { CallHistoryPage } from "./call-history";
import { I18nProvider, LanguageSelector } from "./i18n";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function view(canRead = true) {
  const api = new ApiClient("memory-only", vi.fn());
  return render(<I18nProvider><LanguageSelector /><CallHistoryPage api={api} canRead={canRead} /></I18nProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("CallHistoryPage", () => {
  it("renders an unauthorized state without requesting protected history", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    view(false);
    expect(screen.getByRole("heading", { name: "Call history access not granted" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("distinguishes unconfigured CDR and CEL sources from configured empty results", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/calls/history/capabilities")) return response({ cdr: { status: "unconfigured", source: null }, cel: { status: "unconfigured", source: null } });
      return response({ availability: "unconfigured", source: null, items: [], total: 0, limit: 50, offset: 0 });
    }));
    const rendered = view();
    expect(screen.getByRole("heading", { name: "Loading call history" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "CDR source not configured" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "CEL source not configured" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No matching CEL events" })).not.toBeInTheDocument();
    rendered.unmount();

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/calls/history/capabilities")) return response({ cdr: { status: "available", source: "postgres" }, cel: { status: "available", source: "postgres" } });
      return response({ availability: "available", source: "postgres", items: [], total: 0, limit: 50, offset: 0 });
    }));
    view();
    expect(await screen.findByRole("heading", { name: "No matching CDR records" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /source not configured/i })).not.toBeInTheDocument();
  });

  it("shows a request failure separately from an empty or unconfigured source", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: { message: "upstream unavailable" } }, 503)));
    view();
    expect(await screen.findByRole("heading", { name: "Call history unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /No matching|not configured/ })).not.toBeInTheDocument();
  });

  it("preserves bounded filters, pagination, correlation and backend timeline order in EN and ES", async () => {
    const cdr = {
      id: "cdr-1", server_id: "server-1", source_id: "source-cdr-1", unique_id: "unique-1", linked_id: "linked-1",
      caller_number: "1001", connected_number: "2002", disposition: "ANSWERED", started_at: "2026-09-13T10:00:00Z",
      answered_at: "2026-09-13T10:00:01Z", ended_at: "2026-09-13T10:01:00Z", duration_seconds: 60, billable_seconds: 59,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/calls/history/capabilities")) return response({ cdr: { status: "available", source: "postgres" }, cel: { status: "available", source: "postgres" } });
      if (url.includes("/calls/timeline?")) return response({
        availability: "available", source: "postgres", server_id: "server-1", correlation_id: "linked-1", total: 2, limit: 100, offset: 0,
        items: [
          { id: "event-1", server_id: "server-1", source_id: "a", event_type: "ANSWER", event_at: "2026-09-13T10:00:01Z", unique_id: "unique-1", linked_id: "linked-1", channel: "PJSIP/1001-a", peer: null, caller_number: "1001", connected_number: "2002" },
          { id: "event-2", server_id: "server-1", source_id: "b", event_type: "BRIDGE_ENTER", event_at: "2026-09-13T10:00:01Z", unique_id: "unique-1", linked_id: "linked-1", channel: "PJSIP/1001-a", peer: "PJSIP/2002-b", caller_number: "1001", connected_number: "2002" },
        ],
      });
      const offset = new URL(url).searchParams.get("offset") ?? "0";
      return response({ availability: "available", source: "postgres", items: [cdr], total: 51, limit: 50, offset: Number(offset) });
    });
    vi.stubGlobal("fetch", fetchMock);
    view();
    expect(await screen.findByText("ANSWERED")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Server ID"), { target: { value: "server-1" } });
    fireEvent.change(screen.getByLabelText("Caller number"), { target: { value: "1001" } });
    fireEvent.change(screen.getByLabelText("UniqueID or LinkedID"), { target: { value: "linked-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Search call history" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = new URL(String(input));
      return url.pathname.endsWith("/calls/history") && url.searchParams.get("server_id") === "server-1" &&
        url.searchParams.get("caller_number") === "1001" && url.searchParams.get("correlation_id") === "linked-1" &&
        url.searchParams.get("limit") === "50" && url.searchParams.get("started_from")?.endsWith("Z");
    })).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => new URL(String(input)).searchParams.get("offset") === "50")).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Inspect timeline" }));
    const answer = await screen.findByText("ANSWER");
    const bridge = screen.getByText("BRIDGE_ENTER");
    expect(answer.compareDocumentPosition(bridge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const timelineRequest = fetchMock.mock.calls.find(([input]) => String(input).includes("/calls/timeline?"));
    expect(new URL(String(timelineRequest?.[0])).searchParams.get("correlation_id")).toBe("linked-1");

    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    expect(screen.getByRole("heading", { name: "Historial de llamadas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Inspeccionar línea de tiempo" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("source-cdr-1-secret");
  });
});
