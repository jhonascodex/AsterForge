export interface CurrentUser {
  id: string;
  username: string;
  permissions: string[];
  must_change_password: boolean;
}

export interface Profile {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  is_system: boolean;
  permission_codes: string[];
}

export interface Permission {
  code: string;
  description: string;
}

export interface User {
  id: string;
  username: string;
  display_name: string;
  enabled: boolean;
  profile_ids: string[];
}

export interface Environment { id: string; code: string; display_name: string; enabled: boolean }
export interface ServerTag { id: string; name: string }
export interface EnvironmentCatalogEntry extends Environment { server_count: number }
export interface ServerTagCatalogEntry extends ServerTag { server_count: number }
export interface EnvironmentCreatePayload { code: string; display_name: string }
export interface EnvironmentUpdatePayload { display_name?: string; enabled?: boolean }
export interface ServerTagPayload { name: string }
export interface Server {
  id: string; name: string; hostname: string; environment: Environment; location: string | null;
  ami_port: number; ami_tls: boolean; monitoring_enabled: boolean; tags: ServerTag[];
  ami_username_configured: boolean; ami_secret_configured: boolean;
  created_at: string; updated_at: string; archived_at: string | null;
}
export interface ServerListResponse { items: Server[]; total: number; limit: number; offset: number }
export interface ServerPayload {
  name: string; hostname: string; environment_id: string; location: string | null;
  ami_port: number; ami_tls: boolean; monitoring_enabled: boolean; tag_ids: string[];
  ami_username?: string; ami_secret?: string; clear_ami_credentials?: boolean;
}
export interface ServerFilters {
  search?: string; environment?: string; tag?: string; monitoringEnabled?: boolean;
  includeArchived?: boolean;
}

export interface AsteriskHealth {
  server_id: string; connection_state: string; freshness: string; observed_at: string | null;
  stale_since: string | null; error_code: string | null;
  core: { system_name: string | null; asterisk_version: string | null; current_calls: number | null; max_calls: number | null } | null;
}
export interface PjsipEndpoint { id: string; server_id: string; source_id: string; state: string; active_channels: number | null; contacts_count: number | null; observed_at: string; stale: boolean }
export interface SipTrunk { id: string; server_id: string; source_id: string; label: string; provider: string | null; state: string; active_calls: number | null; failure_indicator: boolean | null; observed_at: string; stale: boolean }
export interface PageResponse<T> { items: T[]; total: number; limit: number; offset: number }

export interface RecordingStorage { mountpoint: string; total_bytes: number | null; available_bytes: number | null; used_bytes: number | null; growth_bytes: number | null; growth_period_seconds: number | null }
export interface HostTelemetry {
  server_id: string; freshness: string; stale_since: string | null; error_code: string | null;
  sample: { capabilities: string[]; cpu_usage_percent: number | null; memory_total_bytes: number | null; memory_available_bytes: number | null; load_1: number | null; uptime_seconds: number | null; asterisk_service_state: string | null; recording_storage: RecordingStorage; observed_at: string } | null;
}

export type CallFreshness = "unknown" | "fresh" | "stale";

export interface LiveCall {
  server_id: string;
  logical_id: string;
  state: string;
  caller_number: string | null;
  connected_number: string | null;
  channel_count: number;
  channel_ids: string[];
  started_at: string;
  observed_at: string;
  freshness: CallFreshness;
}

export interface ServerCalls {
  server_id: string;
  freshness: CallFreshness;
  observed_at: string | null;
  calls: LiveCall[];
}

export interface LiveCallsResponse { servers: ServerCalls[] }

export type HistoryAvailability = "available" | "unconfigured";
export interface HistorySourceCapability { status: HistoryAvailability; source: "postgres" | null }
export interface HistoryCapabilities { cdr: HistorySourceCapability; cel: HistorySourceCapability }
export interface CallDetailRecord {
  id: string; server_id: string; source_id: string; unique_id: string | null; linked_id: string | null;
  caller_number: string | null; connected_number: string | null; disposition: string | null;
  started_at: string; answered_at: string | null; ended_at: string | null;
  duration_seconds: number | null; billable_seconds: number | null;
}
export interface CallHistoryPage {
  availability: HistoryAvailability; source: "postgres" | null; items: CallDetailRecord[];
  total: number; limit: number; offset: number;
}
export interface ChannelEventRecord {
  id: string; server_id: string; source_id: string; event_type: string; event_at: string;
  unique_id: string | null; linked_id: string | null; channel: string | null; peer: string | null;
  caller_number: string | null; connected_number: string | null;
}
export interface CallTimelinePage {
  availability: HistoryAvailability; source: "postgres" | null; server_id: string; correlation_id: string;
  items: ChannelEventRecord[]; total: number; limit: number; offset: number;
}

export type CallRealtimeMessage =
  | { type: "calls.initial"; version: 1; timestamp: string; resource: "live_calls"; server_id: null; payload: LiveCallsResponse }
  | { type: "calls.snapshot"; version: 1; timestamp: string; resource: "live_calls"; server_id: string; payload: ServerCalls };

export type OperationalResource = "asterisk_health" | "pjsip" | "host_telemetry" | "alerts" | "incidents";
export type OperationalEventType = "asterisk.health.changed" | "pjsip.snapshot.changed" | "host.telemetry.changed" | "alerts.changed" | "incidents.changed";
export type RealtimeConnectionState = "connecting" | "resyncing" | "connected" | "disconnected";
export type OperationalRealtimeMessage =
  | {
      type: "operations.resync_required"; version: 1; timestamp: string; resource: "operations";
      server_id: null; stream_id: string; sequence: number;
      payload: { reason: "initial_subscription" | "buffer_overflow"; resources: OperationalResource[] };
    }
  | {
      type: OperationalEventType; version: 1; timestamp: string; resource: OperationalResource;
      server_id: string; stream_id: string; sequence: number; payload: { resync: "rest" };
    };

export interface LogEvent {
  id: string;
  server_id: string;
  server_name: string;
  environment: string;
  source: string;
  level: string;
  module: string | null;
  message: string;
  endpoint: string | null;
  trunk: string | null;
  unique_id: string | null;
  linked_id: string | null;
  occurred_at: string;
}

export interface LogSearchResponse {
  items: LogEvent[];
  offset: number;
  limit: number;
  has_more: boolean;
  from_time: string;
  to_time: string;
}

export interface LogSourceStatus {
  server_id: string;
  source: string;
  available: boolean;
  checked_at: string;
  error_code: string | null;
}

export interface Alert {
  id: string;
  rule_code: string;
  server_id: string;
  resource_type: string;
  resource_key: string;
  condition_scope: string;
  severity: string;
  state: string;
  first_seen: string;
  last_seen: string;
  acknowledgement_comment: string | null;
  suppressed: boolean;
  maintenance_window_id: string | null;
}

export interface Incident {
  id: string;
  server_id: string;
  correlation_key: string;
  title: string;
  state: string;
  opened_at: string;
  resolved_at: string | null;
  alert_ids: string[];
}

export interface IncidentEvent {
  id: string;
  alert_id: string | null;
  event_type: string;
  occurred_at: string;
  actor_user_id: string | null;
  metadata: Record<string, unknown>;
}

export interface MaintenanceWindow {
  id: string;
  server_id: string;
  resource_type: string | null;
  resource_key: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  active: boolean;
}

export interface AuditEvent {
  id: string; occurred_at: string; actor_user_id: string | null; action: string;
  target_type: string; target_id: string; outcome: "success" | "failure";
  source: string; request_id: string | null; metadata: Record<string, unknown>;
}
export interface AuditEventPage { items: AuditEvent[]; total: number; limit: number; offset: number }
export interface AlertRule {
  id: string; code: string; condition_kind: string; severity: "INFO" | "WARNING" | "CRITICAL";
  threshold: number | null; clear_threshold: number | null; duration_seconds: number;
  debounce_seconds: number; enabled: boolean;
}
export interface RetentionPolicy {
  category: "operational_logs"; retention_days: number; batch_size: number;
  updated_at: string; updated_by_user_id: string | null;
}

export type ReportFamily = "server-availability" | "calls" | "failures" |
  "trunk-availability" | "endpoint-availability" | "incidents" | "host-storage";
export interface SearchResult {
  entity_type: "server" | "endpoint" | "trunk" | "phone_number" | "unique_id" | "linked_id";
  identifier: string; label: string; server_id: string; observed_at: string | null;
}
export interface SearchResponse { query: string; items: SearchResult[]; truncated: boolean }
export interface ReportRow {
  occurred_at: string; server_id: string; server_name: string;
  values: Record<string, string | number | boolean | null>;
}
export interface ReportResponse {
  family: ReportFamily; from_time: string; to_time: string; limit: number; offset: number;
  has_more: boolean; items: ReportRow[];
}

export interface TokenResponse {
  access_token: string;
  expires_at: string;
  must_change_password: boolean;
}

interface ErrorBody {
  detail?: { message?: string } | string;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000/api/v1";
const WS_BASE = API_BASE.replace(/^http/, "ws");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly messageKey?: TranslationKey,
  ) {
    super(message);
  }
}

async function parseError(response: Response): Promise<{ message: string; messageKey?: TranslationKey }> {
  try {
    const body = (await response.json()) as ErrorBody;
    if (typeof body.detail === "string") return { message: body.detail };
    if (body.detail?.message) return { message: body.detail.message };
    return { message: "The request could not be completed.", messageKey: "error.request" };
  } catch {
    return { message: "The service returned an unexpected response.", messageKey: "error.unexpectedResponse" };
  }
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    // Deliberately generic so the UI never reveals whether an account exists.
    throw new ApiError(
      response.status === 429
        ? "Login is temporarily unavailable. Please wait and try again."
        : "Unable to sign in with those credentials.",
      response.status,
      response.status === 429 ? "login.throttled" : "login.invalid",
    );
  }
  return response.json() as Promise<TokenResponse>;
}

export async function restoreSession(): Promise<{
  session: TokenResponse | null;
  invalid: boolean;
}> {
  const response = await fetch(`${API_BASE}/auth/restore`, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (response.status === 204) return { session: null, invalid: false };
  if (response.status === 401) return { session: null, invalid: true };
  if (!response.ok) {
    const error = await parseError(response);
    throw new ApiError(error.message, response.status, error.messageKey);
  }
  return { session: await response.json() as TokenResponse, invalid: false };
}

export class ApiClient {
  constructor(
    private readonly token: string,
    private readonly onUnauthorized: () => void,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${this.token}`,
        ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });
    if (response.status === 401) {
      this.onUnauthorized();
      throw new ApiError("Your session has expired. Please sign in again.", 401, "error.sessionExpired");
    }
    if (!response.ok) {
      const error = await parseError(response);
      throw new ApiError(error.message, response.status, error.messageKey);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  private async requestBlob(path: string): Promise<Blob> {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: { Accept: "text/csv", Authorization: `Bearer ${this.token}` },
    });
    if (response.status === 401) {
      this.onUnauthorized();
      throw new ApiError("Your session has expired. Please sign in again.", 401, "error.sessionExpired");
    }
    if (!response.ok) {
      const error = await parseError(response);
      throw new ApiError(error.message, response.status, error.messageKey);
    }
    return response.blob();
  }

  private async allPages<T>(load: (offset: number) => Promise<PageResponse<T>>) {
    const items: T[] = [];
    let total = 1;
    while (items.length < total) {
      const page = await load(items.length);
      items.push(...page.items);
      total = page.total;
      if (page.items.length === 0) break;
    }
    return items;
  }

  me = () => this.request<CurrentUser>("/auth/me");
  logout = () => this.request<undefined>("/auth/logout", { method: "POST" });
  changePassword = (currentPassword: string, newPassword: string) =>
    this.request<undefined>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
  users = () => this.request<User[]>("/users");
  profiles = () => this.request<Profile[]>("/profiles");
  permissions = () => this.request<Permission[]>("/permissions");
  environments = (includeDisabled = false) => this.request<EnvironmentCatalogEntry[]>(`/environments${includeDisabled ? "?include_disabled=true" : ""}`);
  createEnvironment = (payload: EnvironmentCreatePayload) => this.request<Environment>("/environments", { method: "POST", body: JSON.stringify(payload) });
  updateEnvironment = (id: string, payload: EnvironmentUpdatePayload) => this.request<Environment>(`/environments/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  deleteEnvironment = (id: string) => this.request<undefined>(`/environments/${id}`, { method: "DELETE" });
  serverTags = () => this.request<ServerTagCatalogEntry[]>("/server-tags");
  createServerTag = (payload: ServerTagPayload) => this.request<ServerTag>("/server-tags", { method: "POST", body: JSON.stringify(payload) });
  updateServerTag = (id: string, payload: ServerTagPayload) => this.request<ServerTag>(`/server-tags/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  deleteServerTag = (id: string) => this.request<undefined>(`/server-tags/${id}`, { method: "DELETE" });
  servers = (filters: ServerFilters = {}, offset = 0) => {
    const query = new URLSearchParams({ limit: "200", offset: String(offset) });
    if (filters.search) query.set("search", filters.search);
    if (filters.environment) query.set("environment", filters.environment);
    if (filters.tag) query.set("tag", filters.tag);
    if (filters.monitoringEnabled !== undefined) query.set("monitoring_enabled", String(filters.monitoringEnabled));
    if (filters.includeArchived) query.set("include_archived", "true");
    return this.request<ServerListResponse>(`/servers?${query}`);
  };
  allServers = (filters: ServerFilters = {}) => this.allPages((offset) => this.servers(filters, offset));
  server = (id: string) => this.request<Server>(`/servers/${id}`);
  createServer = (payload: ServerPayload) => this.request<Server>("/servers", { method: "POST", body: JSON.stringify(payload) });
  updateServer = (id: string, payload: Partial<ServerPayload>) => this.request<Server>(`/servers/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  archiveServer = (id: string) => this.request<undefined>(`/servers/${id}`, { method: "DELETE" });
  asteriskHealth = () => this.request<AsteriskHealth[]>("/asterisk/health");
  serverAsteriskHealth = (id: string) => this.request<AsteriskHealth>(`/asterisk/servers/${id}/health`);
  endpoints = (serverId?: string, offset = 0) => this.request<PageResponse<PjsipEndpoint>>(`/asterisk/endpoints?limit=200&offset=${offset}${serverId ? `&server_id=${encodeURIComponent(serverId)}` : ""}`);
  allEndpoints = (serverId?: string) => this.allPages((offset) => this.endpoints(serverId, offset));
  trunks = (serverId?: string, offset = 0) => this.request<PageResponse<SipTrunk>>(`/asterisk/trunks?limit=200&offset=${offset}${serverId ? `&server_id=${encodeURIComponent(serverId)}` : ""}`);
  allTrunks = (serverId?: string) => this.allPages((offset) => this.trunks(serverId, offset));
  hostTelemetry = () => this.request<HostTelemetry[]>("/host/telemetry");
  serverHostTelemetry = (id: string) => this.request<HostTelemetry>(`/host/servers/${id}/telemetry`);
  liveCalls = () => this.request<LiveCallsResponse>("/calls/live");
  callHistoryCapabilities = () => this.request<HistoryCapabilities>("/calls/history/capabilities");
  callHistory = (query: URLSearchParams) => this.request<CallHistoryPage>(`/calls/history?${query}`);
  callTimeline = (query: URLSearchParams) => this.request<CallTimelinePage>(`/calls/timeline?${query}`);
  logs = (query: URLSearchParams) => this.request<LogSearchResponse>(`/logs?${query}`);
  logSources = () => this.request<LogSourceStatus[]>("/logs/sources");
  alerts = (state?: string, serverId?: string) => {
    const query = new URLSearchParams();
    if (state) query.set("state", state);
    if (serverId) query.set("server_id", serverId);
    return this.request<Alert[]>(`/alerts${query.size ? `?${query}` : ""}`);
  };
  acknowledgeAlert = (id: string, comment: string) =>
    this.request<Alert>(`/alerts/${id}/acknowledge`, {
      method: "POST", body: JSON.stringify({ comment }),
    });
  incidents = () => this.request<Incident[]>("/incidents");
  incidentTimeline = (id: string) => this.request<IncidentEvent[]>(`/incidents/${id}/timeline`);
  maintenanceWindows = () => this.request<MaintenanceWindow[]>("/maintenance-windows");
  createMaintenanceWindow = (payload: {
    server_id: string;
    resource_type?: string;
    resource_key?: string;
    starts_at: string;
    ends_at: string;
    reason: string;
  }) => this.request<MaintenanceWindow>("/maintenance-windows", {
    method: "POST", body: JSON.stringify(payload),
  });
  endMaintenanceWindow = (id: string) =>
    this.request<MaintenanceWindow>(`/maintenance-windows/${id}/end`, { method: "PATCH" });
  auditEvents = (query = new URLSearchParams()) =>
    this.request<AuditEventPage>(`/audit-events${query.size ? `?${query}` : ""}`);
  alertRules = () => this.request<AlertRule[]>("/monitoring-config/alert-rules");
  updateAlertRule = (id: string, payload: Omit<AlertRule, "id" | "code" | "condition_kind">) =>
    this.request<AlertRule>(`/monitoring-config/alert-rules/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
  retentionPolicies = () => this.request<RetentionPolicy[]>("/monitoring-config/retention-policies");
  updateRetentionPolicy = (category: RetentionPolicy["category"], payload: Pick<RetentionPolicy, "retention_days" | "batch_size">) =>
    this.request<RetentionPolicy>(`/monitoring-config/retention-policies/${category}`, { method: "PATCH", body: JSON.stringify(payload) });
  search = (query: string) => this.request<SearchResponse>(`/search?q=${encodeURIComponent(query)}`);
  report = (family: ReportFamily, query: URLSearchParams) =>
    this.request<ReportResponse>(`/reports/${family}?${query}`);
  exportReport = (family: ReportFamily, query: URLSearchParams) =>
    this.requestBlob(`/reports/${family}/export?${query}`);

  subscribeLiveCalls(
    onMessage: (message: CallRealtimeMessage) => void,
    onState: (state: "connecting" | "connected" | "disconnected") => void,
  ) {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    let attempts = 0;
    const connect = () => {
      if (stopped) return;
      onState("connecting");
      socket = new WebSocket(`${WS_BASE}/ws/calls`);
      socket.addEventListener("open", () => socket?.send(JSON.stringify({ type: "authenticate", token: this.token })));
      socket.addEventListener("message", (event) => {
        try {
          const message = JSON.parse(String(event.data)) as CallRealtimeMessage;
          if (message.version === 1 && message.resource === "live_calls") {
            if (message.type === "calls.initial") { attempts = 0; onState("connected"); }
            onMessage(message);
          }
        } catch {
          // Ignore malformed realtime frames; the latest REST/initial snapshot stays authoritative.
        }
      });
      socket.addEventListener("close", (event) => {
        if (stopped) return;
        onState("disconnected");
        if (event.code === 4401) { stopped = true; this.onUnauthorized(); return; }
        if (event.code === 4403) { stopped = true; return; }
        const delay = Math.min(30_000, 1_000 * 2 ** Math.min(attempts++, 5));
        retryTimer = window.setTimeout(connect, delay);
      });
      socket.addEventListener("error", () => socket?.close());
    };
    connect();
    return () => {
      stopped = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close(1000, "View closed");
    };
  }

  subscribeOperationalUpdates(
    onMessage: (message: OperationalRealtimeMessage) => void | Promise<void>,
    onState: (state: RealtimeConnectionState) => void,
    serverIds?: string[],
  ) {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    let attempts = 0;
    let streamId: string | undefined;
    let highestSequence = -1;
    let generation = 0;
    const eventTypes = new Set<OperationalEventType>([
      "asterisk.health.changed", "pjsip.snapshot.changed", "host.telemetry.changed", "alerts.changed", "incidents.changed",
    ]);
    const resources = new Set<OperationalResource>(["asterisk_health", "pjsip", "host_telemetry", "alerts", "incidents"]);

    const isMessage = (value: unknown): value is OperationalRealtimeMessage => {
      if (typeof value !== "object" || value === null) return false;
      const message = value as Record<string, unknown>;
      if (message.version !== 1 || typeof message.stream_id !== "string" || typeof message.sequence !== "number") return false;
      if (message.type === "operations.resync_required") {
        const payload = message.payload as Record<string, unknown> | undefined;
        return message.resource === "operations" && message.server_id === null && Array.isArray(payload?.resources) && payload.resources.every((item) => typeof item === "string" && resources.has(item as OperationalResource));
      }
      return typeof message.type === "string" && eventTypes.has(message.type as OperationalEventType) && typeof message.server_id === "string" && typeof message.resource === "string" && resources.has(message.resource as OperationalResource);
    };

    const connect = () => {
      if (stopped) return;
      streamId = undefined;
      highestSequence = -1;
      const connectionGeneration = ++generation;
      let pending = 0;
      let processing = Promise.resolve();
      onState("connecting");
      const currentSocket = new WebSocket(`${WS_BASE}/ws/operations`);
      socket = currentSocket;
      currentSocket.addEventListener("open", () => currentSocket.send(JSON.stringify({ type: "authenticate", token: this.token, ...(serverIds ? { server_ids: serverIds } : {}) })));
      currentSocket.addEventListener("message", (event) => {
        if (stopped || generation !== connectionGeneration) return;
        try {
          const message: unknown = JSON.parse(String(event.data));
          if (!isMessage(message)) return;
          if (message.type === "operations.resync_required") {
            streamId = message.stream_id;
            highestSequence = message.sequence;
          } else {
            if (streamId === undefined || message.stream_id !== streamId || message.sequence <= highestSequence) return;
            highestSequence = message.sequence;
          }
          pending += 1;
          onState("resyncing");
          processing = processing.then(() => onMessage(message)).then(() => {
            pending -= 1;
            attempts = 0;
            if (!stopped && generation === connectionGeneration && pending === 0) onState("connected");
          }).catch(() => {
            pending = 0;
            if (!stopped && generation === connectionGeneration) currentSocket.close(1011, "REST resynchronization failed");
          });
        } catch {
          // Ignore malformed frames; REST state is kept and the feed is not marked current.
        }
      });
      currentSocket.addEventListener("close", (event) => {
        if (stopped || generation !== connectionGeneration) return;
        onState("disconnected");
        if (event.code === 4401) { stopped = true; this.onUnauthorized(); return; }
        if (event.code === 4403) { stopped = true; return; }
        const delay = Math.min(30_000, 1_000 * 2 ** Math.min(attempts++, 5));
        retryTimer = window.setTimeout(connect, delay);
      });
      currentSocket.addEventListener("error", () => currentSocket.close());
    };
    connect();
    return () => {
      stopped = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close(1000, "View closed");
    };
  }

  createUser = (payload: {
    username: string; display_name: string; password: string; profile_ids: string[];
  }) => this.request<User>("/users", { method: "POST", body: JSON.stringify(payload) });

  updateUser = (id: string, payload: Partial<Pick<User, "display_name" | "enabled" | "profile_ids">>) =>
    this.request<User>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) });

  resetPassword = (id: string, password: string) =>
    this.request<undefined>(`/users/${id}/password`, {
      method: "POST", body: JSON.stringify({ password }),
    });

  createProfile = (payload: { name: string; description: string; permission_codes: string[] }) =>
    this.request<Profile>("/profiles", { method: "POST", body: JSON.stringify(payload) });

  updateProfile = (
    id: string,
    payload: Partial<Pick<Profile, "name" | "description" | "enabled" | "permission_codes">>,
  ) => this.request<Profile>(`/profiles/${id}`, {
    method: "PATCH", body: JSON.stringify(payload),
  });
}
import type { TranslationKey } from "./i18n";
