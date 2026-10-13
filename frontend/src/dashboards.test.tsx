import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  type OperationalRealtimeMessage,
  type RealtimeConnectionState,
} from "./api";
import { DashboardPage, ServersPage } from "./dashboards";
import { I18nProvider } from "./i18n";

const server = {
  id: "s1", name: "PBX Lima", hostname: "pbx-lima.internal",
  environment: { id: "e1", code: "PROD", display_name: "Production", enabled: true },
  location: "Lima", ami_port: 5039, ami_tls: true, monitoring_enabled: true, tags: [],
  ami_username_configured: true, ami_secret_configured: true,
  created_at: "2026-09-13T00:00:00Z", updated_at: "2026-09-13T00:00:00Z", archived_at: null,
};

function changed(type: "asterisk.health.changed" | "pjsip.snapshot.changed" | "host.telemetry.changed" | "alerts.changed", resource: "asterisk_health" | "pjsip" | "host_telemetry" | "alerts", sequence: number, serverId = "s1"): OperationalRealtimeMessage {
  return { type, version: 1, timestamp: "2026-09-14T00:00:00Z", resource, server_id: serverId, stream_id: "stream-1", sequence, payload: { resync: "rest" } };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("operational dashboard realtime", () => {
  it("requires an explicit REST resync boundary on initial connection and reconnect, and rejects duplicate sequences", async () => {
    vi.useFakeTimers();
    class FakeWebSocket {
      static instances: FakeWebSocket[] = [];
      listeners = new Map<string, Array<(event: Event) => void>>();
      sent: string[] = [];
      constructor(readonly url: string) { FakeWebSocket.instances.push(this); }
      addEventListener(type: string, listener: (event: Event) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]); }
      send(value: string) { this.sent.push(value); }
      close(code = 1000) { this.emit("close", new CloseEvent("close", { code })); }
      emit(type: string, event: Event) { for (const listener of this.listeners.get(type) ?? []) listener(event); }
      message(value: OperationalRealtimeMessage) { this.emit("message", new MessageEvent("message", { data: JSON.stringify(value) })); }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const states: RealtimeConnectionState[] = [];
    const messages: OperationalRealtimeMessage[] = [];
    let releaseResync: (() => void) | undefined;
    let resyncCount = 0;
    let markConnected: (() => void) | undefined;
    const connected = new Promise<void>((resolve) => { markConnected = resolve; });
    let markAlertDelivered: (() => void) | undefined;
    const alertDelivered = new Promise<void>((resolve) => { markAlertDelivered = resolve; });
    let markHostStarted: (() => void) | undefined;
    const hostStarted = new Promise<void>((resolve) => { markHostStarted = resolve; });
    let releaseHost: (() => void) | undefined;
    let markDrained: (() => void) | undefined;
    const drained = new Promise<void>((resolve) => { markDrained = resolve; });
    let markReconnected: (() => void) | undefined;
    const reconnected = new Promise<void>((resolve) => { markReconnected = resolve; });
    let connectedCount = 0;
    const client = new ApiClient("memory-only", vi.fn());
    const stop = client.subscribeOperationalUpdates(async (message) => {
      messages.push(message);
      if (message.type === "operations.resync_required" && ++resyncCount === 1) await new Promise<void>((resolve) => { releaseResync = resolve; });
      if (message.type === "host.telemetry.changed") { markHostStarted?.(); await new Promise<void>((resolve) => { releaseHost = resolve; }); }
      if (message.type === "alerts.changed") markAlertDelivered?.();
    }, (state) => { states.push(state); if (state === "connected") { connectedCount += 1; markConnected?.(); if (connectedCount === 2) markDrained?.(); if (connectedCount === 3) markReconnected?.(); } }, ["s1"]);

    const first = FakeWebSocket.instances[0];
    if (!first) throw new Error("Expected the initial operational socket");
    first.emit("open", new Event("open"));
    const authentication = first.sent[0];
    if (!authentication) throw new Error("Expected an authentication frame");
    expect(JSON.parse(authentication)).toEqual({ type: "authenticate", token: "memory-only", server_ids: ["s1"] });
    first.message(changed("alerts.changed", "alerts", 2));
    expect(messages).toHaveLength(0);
    first.message({ type: "operations.resync_required", version: 1, timestamp: "2026-09-14T00:00:00Z", resource: "operations", server_id: null, stream_id: "stream-1", sequence: 2, payload: { reason: "initial_subscription", resources: ["asterisk_health", "alerts"] } });
    expect(states.at(-1)).toBe("resyncing");
    await Promise.resolve();
    expect(releaseResync).toBeDefined();
    releaseResync?.();
    await connected;
    expect(states.at(-1)).toBe("connected");
    first.message(changed("host.telemetry.changed", "host_telemetry", 3));
    first.message(changed("alerts.changed", "alerts", 4));
    first.message(changed("alerts.changed", "alerts", 4));
    await hostStarted;
    expect(states.at(-1)).toBe("resyncing");
    releaseHost?.();
    await alertDelivered;
    await drained;
    expect(messages.filter((item) => item.type === "alerts.changed")).toHaveLength(1);

    first.close(1006);
    expect(states.at(-1)).toBe("disconnected");
    await vi.advanceTimersByTimeAsync(1_000);
    const second = FakeWebSocket.instances[1];
    if (!second) throw new Error("Expected a reconnected operational socket");
    second.emit("open", new Event("open"));
    const hostsBeforeBoundary = messages.filter((item) => item.type === "host.telemetry.changed").length;
    second.message(changed("host.telemetry.changed", "host_telemetry", 5));
    await vi.runAllTicks();
    expect(messages.filter((item) => item.type === "host.telemetry.changed")).toHaveLength(hostsBeforeBoundary);
    second.message({ type: "operations.resync_required", version: 1, timestamp: "2026-09-14T00:01:00Z", resource: "operations", server_id: null, stream_id: "stream-2", sequence: 5, payload: { reason: "initial_subscription", resources: ["host_telemetry"] } });
    await reconnected;
    expect(states.at(-1)).toBe("connected");
    stop();
  });

  it("refreshes only authorized in-scope REST read models for Asterisk, PJSIP, host and alert updates", async () => {
    let onOperational: ((message: OperationalRealtimeMessage) => void | Promise<void>) | undefined;
    let healthState = "connected";
    let trunkState = "REGISTERED";
    let availableBytes = 400;
    let alertItems: Array<Record<string, unknown>> = [];
    const asteriskHealth = vi.fn(async () => [{ server_id: "s1", connection_state: healthState, freshness: "fresh", observed_at: "2026-09-14T00:00:00Z", stale_since: null, error_code: null, core: null }]);
    const allTrunks = vi.fn(async () => [{ id: "tr1", server_id: "s1", source_id: "carrier", label: "Carrier A", provider: null, state: trunkState, active_calls: null, failure_indicator: false, observed_at: "2026-09-14T00:00:00Z", stale: false }]);
    const hostTelemetry = vi.fn(async () => [{ server_id: "s1", freshness: "fresh", stale_since: null, error_code: null, sample: { capabilities: ["recording_storage"], cpu_usage_percent: null, memory_total_bytes: null, memory_available_bytes: null, load_1: null, uptime_seconds: null, asterisk_service_state: null, recording_storage: { mountpoint: "/recordings", total_bytes: 1000, available_bytes: availableBytes, used_bytes: 1000 - availableBytes, growth_bytes: null, growth_period_seconds: null }, observed_at: "2026-09-14T00:00:00Z" } }]);
    const alerts = vi.fn(async () => alertItems);
    const api = {
      environments: vi.fn(async () => [server.environment]), allServers: vi.fn(async () => [server]),
      asteriskHealth, allEndpoints: vi.fn(async () => []), allTrunks, hostTelemetry, alerts,
      subscribeOperationalUpdates: vi.fn((callback: typeof onOperational, onState: (state: RealtimeConnectionState) => void) => { onOperational = callback; onState("connected"); return vi.fn(); }),
    } as unknown as ApiClient;

    render(<I18nProvider><DashboardPage api={api} permissions={["dashboard.read", "server.read", "asterisk.read", "alert.read"]} filters={{ environment: "", serverId: "s1", hours: 24 }} onFilters={vi.fn()} onInspect={vi.fn()} /></I18nProvider>);
    expect(await screen.findByText("40%")).toBeInTheDocument();
    await waitFor(() => expect(onOperational).toBeDefined());

    healthState = "disconnected";
    await act(async () => { await onOperational?.(changed("asterisk.health.changed", "asterisk_health", 1)); });
    expect(await screen.findByText("disconnected")).toBeInTheDocument();
    trunkState = "UNREGISTERED";
    await act(async () => { await onOperational?.(changed("pjsip.snapshot.changed", "pjsip", 2)); });
    const trunksCard = screen.getByText("Trunks").closest("article");
    await waitFor(() => expect(trunksCard && within(trunksCard).getByText("1 unhealthy or stale")).toBeInTheDocument());
    availableBytes = 50;
    await act(async () => { await onOperational?.(changed("host.telemetry.changed", "host_telemetry", 3)); });
    expect(await screen.findByText("5%")).toBeInTheDocument();
    alertItems = [{ id: "a1", rule_code: "host.cpu.high", server_id: "s1", resource_type: "host", resource_key: "s1", condition_scope: "cpu", severity: "CRITICAL", state: "OPEN", first_seen: new Date().toISOString(), last_seen: new Date().toISOString(), acknowledgement_comment: null, suppressed: false, maintenance_window_id: null }];
    await act(async () => { await onOperational?.(changed("alerts.changed", "alerts", 4)); });
    const alertsCard = screen.getAllByText("Alerts")[0]?.closest("article");
    expect(alertsCard && within(alertsCard).getByText("1")).toBeInTheDocument();

    const before = asteriskHealth.mock.calls.length;
    await act(async () => { await onOperational?.(changed("asterisk.health.changed", "asterisk_health", 5, "s2")); });
    expect(asteriskHealth).toHaveBeenCalledTimes(before);
  });

  it("scopes server-detail subscriptions and completes authoritative REST resync before returning current", async () => {
    let onOperational: ((message: OperationalRealtimeMessage) => void | Promise<void>) | undefined;
    let setOperationalState: ((state: RealtimeConnectionState) => void) | undefined;
    let subscribedServerIds: string[] | undefined;
    let trunkState = "REGISTERED";
    const serverAsteriskHealth = vi.fn(async () => ({ server_id: "s1", connection_state: "connected", freshness: "fresh", observed_at: "2026-09-14T00:00:00Z", stale_since: null, error_code: null, core: null }));
    const allEndpoints = vi.fn(async () => []);
    const allTrunks = vi.fn(async () => [{ id: "tr1", server_id: "s1", source_id: "carrier", label: "Carrier A", provider: null, state: trunkState, active_calls: null, failure_indicator: false, observed_at: "2026-09-14T00:00:00Z", stale: false }]);
    const serverHostTelemetry = vi.fn(async () => ({ server_id: "s1", freshness: "fresh", stale_since: null, error_code: null, sample: null }));
    const alerts = vi.fn(async () => []);
    const api = {
      environments: vi.fn(async () => [server.environment]), allServers: vi.fn(async () => [server]),
      serverAsteriskHealth, allEndpoints, allTrunks, serverHostTelemetry, alerts,
      subscribeOperationalUpdates: vi.fn((callback: typeof onOperational, onState: typeof setOperationalState, serverIds?: string[]) => { onOperational = callback; setOperationalState = onState; subscribedServerIds = serverIds; onState?.("connected"); return vi.fn(); }),
    } as unknown as ApiClient;

    render(<I18nProvider><ServersPage api={api} permissions={["server.read", "asterisk.read", "alert.read"]} filters={{ environment: "", serverId: "s1", hours: 24 }} onFilters={vi.fn()} /></I18nProvider>);
    expect(await screen.findByRole("heading", { name: "Server monitoring" })).toBeInTheDocument();
    await waitFor(() => expect(subscribedServerIds).toEqual(["s1"]));
    const initialCalls = { health: serverAsteriskHealth.mock.calls.length, endpoints: allEndpoints.mock.calls.length, trunks: allTrunks.mock.calls.length, host: serverHostTelemetry.mock.calls.length, alerts: alerts.mock.calls.length };
    act(() => setOperationalState?.("resyncing"));
    expect(screen.getByText("Resynchronizing")).toBeInTheDocument();
    trunkState = "UNREGISTERED";
    await act(async () => { await onOperational?.({ type: "operations.resync_required", version: 1, timestamp: "2026-09-14T00:00:00Z", resource: "operations", server_id: null, stream_id: "stream-2", sequence: 9, payload: { reason: "initial_subscription", resources: ["asterisk_health", "pjsip", "host_telemetry", "alerts", "incidents"] } }); });
    act(() => setOperationalState?.("connected"));
    expect(serverAsteriskHealth).toHaveBeenCalledTimes(initialCalls.health + 1);
    expect(allEndpoints).toHaveBeenCalledTimes(initialCalls.endpoints + 1);
    expect(allTrunks).toHaveBeenCalledTimes(initialCalls.trunks + 1);
    expect(serverHostTelemetry).toHaveBeenCalledTimes(initialCalls.host + 1);
    expect(alerts).toHaveBeenCalledTimes(initialCalls.alerts + 1);
    expect(await screen.findByText("UNREGISTERED")).toBeInTheDocument();
    expect(screen.getByText("Current data")).toBeInTheDocument();
  });
});
