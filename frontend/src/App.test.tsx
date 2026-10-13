import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function response(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function signIn() {
  fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "operator" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery staple" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(null, 204)));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  document.documentElement.lang = "en";
});

describe("App", () => {
  it("starts with an accessible login and identifies the read-only V1 scope", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "AsterForge" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in to AsterForge" })).toBeInTheDocument();
    expect(screen.getByText(/Read-only toward Asterisk/)).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Language")).toHaveValue("en");
    expect(document.documentElement).toHaveAttribute("lang", "en");
  });

  it("switches between Spanish and English immediately and persists the preference", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Sign in to AsterForge" });
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    expect(screen.getByRole("heading", { name: "Iniciar sesión en AsterForge" })).toBeInTheDocument();
    expect(screen.getByLabelText("Idioma")).toHaveValue("es");
    expect(window.localStorage.getItem("asterforge.locale")).toBe("es");
    expect(document.documentElement).toHaveAttribute("lang", "es");

    fireEvent.change(screen.getByLabelText("Idioma"), { target: { value: "en" } });
    expect(screen.getByRole("heading", { name: "Sign in to AsterForge" })).toBeInTheDocument();
    expect(window.localStorage.getItem("asterforge.locale")).toBe("en");
  });

  it("restores a valid locale and falls back safely from an unsupported value", async () => {
    window.localStorage.setItem("asterforge.locale", "es");
    const view = render(<App />);
    expect(await screen.findByRole("heading", { name: "Iniciar sesión en AsterForge" })).toBeInTheDocument();
    view.unmount();

    window.localStorage.setItem("asterforge.locale", "not-a-locale");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in to AsterForge" })).toBeInTheDocument();
    expect(screen.getByLabelText("Language")).toHaveValue("en");
    expect(window.localStorage.getItem("asterforge.locale")).toBe("en");
  });

  it("restores a valid server-backed session during full-page bootstrap", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        access_token: "restored-memory-only",
        expires_at: "2099-01-01T00:00:00Z",
        must_change_password: false,
      }))
      .mockResolvedValueOnce(response({
        id: "u1",
        username: "operator",
        permissions: ["dashboard.read"],
        must_change_password: false,
      }))
      .mockResolvedValueOnce(response(null, 204));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("button", { name: "Global dashboard" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/auth/restore");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).credentials).toBe("include");
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).headers).toMatchObject({
      Authorization: "Bearer restored-memory-only",
    });
    expect(window.localStorage.getItem("asterforge.access_token")).toBeNull();
    expect(window.sessionStorage.getItem("asterforge.access_token")).toBeNull();
    const storedValues = Array.from({ length: window.localStorage.length }, (_, index) => {
      const key = window.localStorage.key(index);
      return key === null ? null : window.localStorage.getItem(key);
    });
    expect(storedValues).not.toContain("restored-memory-only");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to AsterForge" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("/auth/logout");
  });

  it("fails closed once when browser-session restoration is invalid", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ detail: {
      code: "authentication_required", message: "Authentication required",
    } }, 401));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByText("Your session ended. Sign in again to continue.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("restores directly into the backend-enforced password-change flow", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response({
        access_token: "restored-temporary",
        expires_at: "2099-01-01T00:00:00Z",
        must_change_password: true,
      }))
      .mockResolvedValueOnce(response({
        id: "u1",
        username: "operator",
        permissions: ["dashboard.read"],
        must_change_password: true,
      })));

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Set your permanent password." })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Global dashboard" })).not.toBeInTheDocument();
  });

  it("localizes authenticated navigation and identity UI while preserving technical values", async () => {
    window.localStorage.setItem("asterforge.locale", "es");
    const profile = { id: "p1", name: "NOC Operators", description: "Primary NOC", enabled: true, is_system: false, permission_codes: ["dashboard.read"] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read", "user.manage", "profile.manage"], must_change_password: false }))
      .mockResolvedValueOnce(response([profile]))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([profile]))
      .mockResolvedValueOnce(response([{ code: "dashboard.read", description: "View dashboard" }]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Usuario"), { target: { value: "operator" } });
    fireEvent.change(screen.getByLabelText("Contraseña"), { target: { value: "correct horse battery staple" } });
    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("button", { name: "Panel global" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Usuarios" }));
    expect(await screen.findByRole("heading", { name: "Usuarios" })).toBeInTheDocument();
    expect(screen.getByText("NOC Operators")).toBeInTheDocument();
    expect(screen.getByText("Primary NOC")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Matriz de acceso" }));
    expect(await screen.findByText("dashboard.read")).toBeInTheDocument();
  });

  it("localizes the forced-password-change experience", async () => {
    window.localStorage.setItem("asterforge.locale", "es");
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "temporary-session", expires_at: "2099-01-01T00:00:00Z", must_change_password: true }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read"], must_change_password: true })));
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Usuario"), { target: { value: "operator" } });
    fireEvent.change(screen.getByLabelText("Contraseña"), { target: { value: "temporary password" } });
    fireEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("heading", { name: "Establezca su contraseña permanente." })).toBeInTheDocument();
    expect(screen.getByLabelText("Contraseña temporal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Panel global" })).not.toBeInTheDocument();
  });

  it("shows a generic authentication error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: { message: "unknown user" } }, 401)));
    render(<App />); await signIn();
    expect(await screen.findByText("Unable to sign in with those credentials.")).toBeInTheDocument();
    expect(screen.queryByText("unknown user")).not.toBeInTheDocument();
  });

  it("builds navigation from effective permissions and signs out", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read", "call.read"] }))
      .mockResolvedValueOnce(response(null, 204));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    expect(await screen.findByRole("button", { name: "Global dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Live calls" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Call history" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Maintenance" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to AsterForge" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("/auth/logout");
  });

  it("places all classification destinations only in Infrastructure and in canonical order", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) return response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" });
      if (url.endsWith("/auth/me")) return response({ id: "u1", username: "operator", permissions: ["server.read", "audit.read"], must_change_password: false });
      if (url.includes("/servers?")) return response({ items: [], total: 0, limit: 200, offset: 0 });
      if (url.includes("/environments") || url.includes("/server-tags")) return response([]);
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();

    const infrastructure = await screen.findByRole("navigation", { name: "Infrastructure" });
    const administration = screen.getByRole("navigation", { name: "Administration" });
    expect(within(infrastructure).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "◇Server inventory", "◇Environments", "◇Tags",
    ]);
    expect(within(administration).getByRole("button", { name: "Audit" })).toBeInTheDocument();
    for (const label of ["Server inventory", "Environments", "Tags"]) {
      expect(within(administration).queryByRole("button", { name: label })).not.toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: label })).toHaveLength(1);
    }

    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    const infraestructura = screen.getByRole("navigation", { name: "Infraestructura" });
    expect(within(infraestructura).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "◇Inventario de servidores", "◇Entornos", "◇Etiquetas",
    ]);
    const administracion = screen.getByRole("navigation", { name: "Administración" });
    for (const label of ["Inventario de servidores", "Entornos", "Etiquetas"]) {
      expect(within(administracion).queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
  });

  it("renders correlated live calls and realtime freshness without call controls", async () => {
    class FakeWebSocket {
      static instance: FakeWebSocket;
      listeners = new Map<string, Array<(event: Event) => void>>();
      sent: string[] = [];
      constructor(readonly url: string) {
        FakeWebSocket.instance = this;
        queueMicrotask(() => this.emit("open", new Event("open")));
      }
      addEventListener(type: string, listener: (event: Event) => void) {
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
      }
      send(value: string) { this.sent.push(value); }
      close() { this.emit("close", new Event("close")); }
      emit(type: string, event: Event) {
        for (const listener of this.listeners.get(type) ?? []) listener(event);
      }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["call.read"], must_change_password: false }))
      .mockResolvedValueOnce(response({ servers: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Live calls" }));
    expect(await screen.findByRole("heading", { name: "No active calls" })).toBeInTheDocument();
    await waitFor(() => expect(FakeWebSocket.instance.sent).toContain(JSON.stringify({ type: "authenticate", token: "memory-only" })));
    expect(FakeWebSocket.instance.url).not.toContain("memory-only");

    FakeWebSocket.instance.emit("message", new MessageEvent("message", { data: JSON.stringify({
      type: "calls.snapshot", version: 1, timestamp: "2026-09-12T00:00:00Z", resource: "live_calls", server_id: "server-1",
      payload: { server_id: "server-1", freshness: "stale", observed_at: "2026-09-12T00:00:00Z", calls: [{
        server_id: "server-1", logical_id: "linked-1", state: "up", caller_number: "1001", connected_number: "2002",
        channel_count: 2, channel_ids: ["a", "b"], started_at: "2026-09-12T00:00:00Z", observed_at: "2026-09-12T00:00:00Z", freshness: "stale",
      }] },
    }) }));
    expect(await screen.findByText("linked-1")).toBeInTheDocument();
    expect(screen.getByText("Stale data")).toBeInTheDocument();
    expect(screen.getByText(/No originate, transfer or hangup controls/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /hangup/i })).not.toBeInTheDocument();
  });

  it("composes real monitoring read models and drills into a capability-aware server view", async () => {
    class DashboardWebSocket {
      listeners = new Map<string, Array<(event: Event) => void>>();
      constructor(readonly url: string) { queueMicrotask(() => this.emit("open", new Event("open"))); }
      addEventListener(type: string, listener: (event: Event) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]); }
      send() { this.emit("message", new MessageEvent("message", { data: JSON.stringify({ type: "calls.initial", version: 1, timestamp: "2026-09-13T01:00:00Z", resource: "live_calls", server_id: null, payload: { servers: [{ server_id: "s1", freshness: "fresh", observed_at: "2026-09-13T01:00:00Z", calls: [{ server_id: "s1", logical_id: "call-1", state: "up", caller_number: "1001", connected_number: "2002", channel_count: 2, channel_ids: ["a", "b"], started_at: "2026-09-13T00:59:00Z", observed_at: "2026-09-13T01:00:00Z", freshness: "fresh" }] }] } }) })); }
      close() { /* View cleanup. */ }
      emit(type: string, event: Event) { for (const listener of this.listeners.get(type) ?? []) listener(event); }
    }
    vi.stubGlobal("WebSocket", DashboardWebSocket);
    const server = { id: "s1", name: "PBX Lima", hostname: "pbx-lima.internal", environment: { id: "e1", code: "PROD", display_name: "Production", enabled: true }, location: "Lima", monitoring_enabled: true, tags: [] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) return response({ access_token: "monitor-token", expires_at: "2099-01-01T00:00:00Z" });
      if (url.endsWith("/auth/me")) return response({ id: "u1", username: "operator", permissions: ["dashboard.read", "server.read", "asterisk.read", "call.read", "log.read", "alert.read"], must_change_password: false });
      if (url.endsWith("/environments")) return response([server.environment]);
      if (url.includes("/servers?")) return response({ items: [server], total: 1, limit: 200, offset: 0 });
      if (url.endsWith("/asterisk/health") || url.endsWith("/asterisk/servers/s1/health")) return response(url.endsWith("/asterisk/health") ? [{ server_id: "s1", connection_state: "connected", freshness: "fresh", observed_at: "2026-09-13T01:00:00Z", stale_since: null, error_code: null, core: { system_name: "pbx-lima", asterisk_version: "22.5", current_calls: 1, max_calls: 100 } }] : { server_id: "s1", connection_state: "connected", freshness: "fresh", observed_at: "2026-09-13T01:00:00Z", stale_since: null, error_code: null, core: { system_name: "pbx-lima", asterisk_version: "22.5", current_calls: 1, max_calls: 100 } });
      if (url.includes("/asterisk/endpoints")) return response({ items: [{ id: "ep1", server_id: "s1", source_id: "1001", state: "AVAILABLE", active_channels: 1, contacts_count: 1, observed_at: "2026-09-13T01:00:00Z", stale: false }], total: 1, limit: 200, offset: 0 });
      if (url.includes("/asterisk/trunks")) return response({ items: [{ id: "tr1", server_id: "s1", source_id: "carrier", label: "Carrier A", provider: "ExampleTel", state: "REGISTERED", active_calls: null, failure_indicator: false, observed_at: "2026-09-13T01:00:00Z", stale: false }], total: 1, limit: 200, offset: 0 });
      if (url.endsWith("/calls/live")) return response({ servers: [] });
      if (url.endsWith("/host/telemetry") || url.endsWith("/host/servers/s1/telemetry")) { const telemetry = { server_id: "s1", freshness: "fresh", stale_since: null, error_code: null, sample: { capabilities: ["cpu", "memory", "recording_storage"], cpu_usage_percent: 31, memory_total_bytes: 16000000000, memory_available_bytes: 8000000000, load_1: 0.8, uptime_seconds: 1000, asterisk_service_state: "active", recording_storage: { mountpoint: "/recordings", total_bytes: 1000, available_bytes: 400, used_bytes: 600, growth_bytes: 20, growth_period_seconds: 60 }, observed_at: "2026-09-13T01:00:00Z" } }; return response(url.endsWith("/host/telemetry") ? [telemetry] : telemetry); }
      if (url.includes("/logs?")) return response({ items: [{ id: "l1", server_id: "s1", server_name: "PBX Lima", environment: "PROD", source: "asterisk/messages", level: "WARNING", module: null, message: "Registration latency increased", endpoint: null, trunk: "Carrier A", unique_id: null, linked_id: null, occurred_at: new Date().toISOString() }], offset: 0, limit: 20, has_more: false, from_time: new Date().toISOString(), to_time: new Date().toISOString() });
      if (url.includes("/alerts")) return response([{ id: "a1", rule_code: "host.cpu.high", server_id: "s1", resource_type: "host", resource_key: "s1", condition_scope: "cpu", severity: "WARNING", state: "OPEN", first_seen: new Date().toISOString(), last_seen: new Date().toISOString(), acknowledgement_comment: null, suppressed: false, maintenance_window_id: null }]);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    expect(await screen.findByRole("heading", { name: "Global monitoring dashboard" })).toBeInTheDocument();
    expect(await screen.findAllByText("PBX Lima")).not.toHaveLength(0);
    expect(screen.getByText("40%")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Investigation range"), { target: { value: "168" } });
    fireEvent.click(screen.getByRole("button", { name: "Inspect server" }));
    expect(await screen.findByRole("heading", { name: "Server monitoring" })).toBeInTheDocument();
    expect(screen.getByLabelText("Server")).toHaveValue("s1");
    expect(screen.getByLabelText("Investigation range")).toHaveValue("168");
    expect(await screen.findByText("Carrier A")).toBeInTheDocument();
    expect(screen.getByText("Registration latency increased")).toBeInTheDocument();
    expect(screen.getByText("host.cpu.high")).toBeInTheDocument();
    expect(screen.queryByText(/permission is required/i)).not.toBeInTheDocument();
  });

  it("shows explicit restricted dashboard signals without calling unauthorized APIs", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "limited", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "observer", permissions: ["dashboard.read"], must_change_password: false }));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    expect(await screen.findByRole("heading", { name: "Global monitoring dashboard" })).toBeInTheDocument();
    expect(await screen.findAllByText("Additional permission is required for this signal.")).toHaveLength(7);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("renders remote log messages as text and supports localized bounded search", async () => {
    const hostile = '<script aria-label="unsafe">window.pwned=true</script>';
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["log.read"], must_change_password: false }))
      .mockResolvedValueOnce(response({ items: [{ id: "l1", server_id: "s1", server_name: "PBX Lima", environment: "PROD", source: "asterisk/messages", level: "ERROR", module: "res_pjsip.c", message: hostile, endpoint: "1001", trunk: null, unique_id: "u1", linked_id: "l1", occurred_at: "2026-09-12T12:00:00Z" }], offset: 0, limit: 50, has_more: false, from_time: "2026-09-11T12:00:00Z", to_time: "2026-09-12T12:00:00Z" }))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Logs" }));
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(document.querySelector("script[aria-label='unsafe']")).toBeNull();
    expect(screen.getByRole("form", { name: "Log search filters" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    expect(screen.getByRole("heading", { name: "Registros centralizados" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Buscar registros" })).toBeInTheDocument();
  });

  it("renders deduplicated alerts and exposes acknowledgement only with permission", async () => {
    const alert = { id: "a1", rule_code: "trunk.registration.unavailable", server_id: "s1", resource_type: "trunk", resource_key: "carrier-a", condition_scope: "availability", severity: "CRITICAL", state: "OPEN", first_seen: "2026-09-12T12:00:00Z", last_seen: "2026-09-12T12:01:00Z", acknowledgement_comment: null, suppressed: true, maintenance_window_id: "m1" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "memory-only", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["alert.read", "alert.acknowledge"], must_change_password: false }))
      .mockResolvedValueOnce(response([alert]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Alerts" }));
    expect(await screen.findByText("trunk.registration.unavailable")).toBeInTheDocument();
    expect(screen.getByText("Notifications suppressed by maintenance")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    expect(screen.getByRole("heading", { name: "Alertas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconocer" })).toBeInTheDocument();
  });

  it("returns to login when a protected request reports an expired session", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "short", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "admin", permissions: ["dashboard.read", "user.manage"] }))
      .mockResolvedValueOnce(response({ detail: { message: "expired" } }, 401))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Users" }));
    await waitFor(() => expect(screen.getByText("Your session ended. Sign in again to continue.")).toBeInTheDocument());
  });

  it("creates users through the authorized identity workflow", async () => {
    const profile = { id: "p1", name: "Operators", description: "NOC access", enabled: true, is_system: false, permission_codes: ["dashboard.read"] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "admin-token", expires_at: "2099-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(response({ id: "u1", username: "admin", permissions: ["dashboard.read", "user.manage", "profile.manage"] }))
      .mockResolvedValueOnce(response([profile]))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response({ id: "u2", username: "alice", display_name: "Alice", enabled: true, profile_ids: ["p1"] }, 201))
      .mockResolvedValueOnce(response([profile]))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Users" }));
    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Alice" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "a-long-temporary-password" } });
    fireEvent.click(screen.getByLabelText(/Operators/));
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByText(/User created/)).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/users") && (call[1] as RequestInit | undefined)?.method === "POST");
    expect(createCall).toBeDefined();
    expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toMatchObject({ username: "alice", profile_ids: ["p1"] });
  });

  it("blocks navigation until the required password change succeeds", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "temporary-session", expires_at: "2099-01-01T00:00:00Z", must_change_password: true }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read"], must_change_password: true }))
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read"], must_change_password: false }));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();

    expect(await screen.findByRole("heading", { name: "Set your permanent password." })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Global dashboard" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "temporary password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a new secure password" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "does not match" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByText("New password and confirmation must match.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);

    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "a new secure password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("button", { name: "Global dashboard" })).toBeInTheDocument();
    expect(fetchMock.mock.calls[3]?.[0]).toContain("/auth/change-password");
    expect(JSON.parse(String((fetchMock.mock.calls[3]?.[1] as RequestInit).body))).toEqual({
      current_password: "temporary password", new_password: "a new secure password",
    });
  });

  it("keeps the forced-change screen on a sanitized backend validation error", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response({ access_token: "temporary-session", expires_at: "2099-01-01T00:00:00Z", must_change_password: true }))
      .mockResolvedValueOnce(response({ id: "u1", username: "operator", permissions: ["dashboard.read"], must_change_password: true }))
      .mockResolvedValueOnce(response({ detail: { code: "invalid_current_password", message: "Invalid current password" } }, 400));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    await screen.findByRole("heading", { name: "Set your permanent password." });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "wrong temporary password" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "a new secure password" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "a new secure password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Invalid current password")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Set your permanent password." })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Global dashboard" })).not.toBeInTheDocument();
  });

  it("searches authorized identifiers and renders bounded reports", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) return response({ access_token: "report-token", expires_at: "2099-01-01T00:00:00Z" });
      if (url.endsWith("/auth/me")) return response({ id: "u1", username: "analyst", permissions: ["report.read", "report.export", "call.read"], must_change_password: false });
      if (url.includes("/search?q=linked-1")) return response({ query: "linked-1", truncated: false, items: [{ entity_type: "linked_id", identifier: "linked-1", label: "linked-1 · PBX Lima", server_id: "s1", observed_at: "2026-09-13T01:00:00Z" }] });
      if (url.includes("/reports/calls?")) return response({ family: "calls", from_time: "2026-09-12T01:00:00Z", to_time: "2026-09-13T01:00:00Z", limit: 200, offset: 0, has_more: false, items: [{ occurred_at: "2026-09-13T00:00:00Z", server_id: "s1", server_name: "PBX Lima", values: { linked_id: "linked-1", disposition: "ANSWERED" } }] });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />); await signIn();
    fireEvent.click(await screen.findByRole("button", { name: "Reports & exports" }));
    expect(await screen.findByRole("heading", { name: "Reports, exports & global search" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Server, endpoint, trunk/), { target: { value: "linked-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("linked-1 · PBX Lima")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run report" }));
    expect(await screen.findByText("ANSWERED")).toBeInTheDocument();
    expect(screen.getByText("1 bounded report rows")).toBeInTheDocument();
  });
});
