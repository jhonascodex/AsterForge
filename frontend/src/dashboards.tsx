import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  ApiClient,
  type Alert,
  type AsteriskHealth,
  type CallRealtimeMessage,
  type Environment,
  type HostTelemetry,
  type LiveCallsResponse,
  type LogEvent,
  type OperationalRealtimeMessage,
  type OperationalResource,
  type PjsipEndpoint,
  type RealtimeConnectionState,
  type Server,
  type SipTrunk,
} from "./api";
import { useI18n, type TranslationKey } from "./i18n";
import { PageState } from "./ui";

export interface InvestigationFilters { environment: string; serverId: string; hours: number }
type Connection = RealtimeConnectionState;

interface MonitoringData {
  environments: Environment[];
  servers: Server[];
  health: AsteriskHealth[];
  endpoints: PjsipEndpoint[];
  trunks: SipTrunk[];
  calls: LiveCallsResponse;
  alerts: Alert[];
  telemetry: HostTelemetry[];
}

const emptyData: MonitoringData = { environments: [], servers: [], health: [], endpoints: [], trunks: [], calls: { servers: [] }, alerts: [], telemetry: [] };

function isHealthyState(value: string) {
  return ["AVAILABLE", "REGISTERED", "UP", "CONNECTED", "RUNNING", "fresh"].includes(value.toUpperCase()) || value === "fresh";
}

function formatPercent(value: number | null) { return value === null ? "—" : `${Math.round(value)}%`; }
function formatBytes(value: number | null) {
  if (value === null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value; let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit < 2 ? 0 : 1)} ${units[unit]}`;
}

function Signal({ state, children }: { state: "ok" | "warning" | "danger" | "muted"; children: ReactNode }) {
  const symbol = state === "ok" ? "●" : state === "warning" ? "▲" : state === "danger" ? "!" : "○";
  return <span className={`signal signal--${state}`}><span aria-hidden="true">{symbol}</span>{children}</span>;
}

function MetricCard({ label, value, detail, state = "muted" }: { label: string; value: string | number; detail: string; state?: "ok" | "warning" | "danger" | "muted" }) {
  return <article className={`metric-card metric-card--${state}`}><p>{label}</p><strong>{value}</strong><small>{detail}</small></article>;
}

function Filters({ value, environments, servers, onChange }: { value: InvestigationFilters; environments: Environment[]; servers: Server[]; onChange: (value: InvestigationFilters) => void }) {
  const { t } = useI18n();
  const availableServers = value.environment ? servers.filter((server) => server.environment.code === value.environment) : servers;
  return <div className="monitoring-filters panel" aria-label={t("dashboard.filters")} role="group">
    <label>{t("dashboard.environment")}<select value={value.environment} onChange={(event) => onChange({ ...value, environment: event.target.value, serverId: "" })}><option value="">{t("dashboard.allEnvironments")}</option>{environments.map((item) => <option key={item.id} value={item.code}>{item.display_name} ({item.code})</option>)}</select></label>
    <label>{t("dashboard.server")}<select value={value.serverId} onChange={(event) => onChange({ ...value, serverId: event.target.value })}><option value="">{t("dashboard.allServers")}</option>{availableServers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>{t("dashboard.timeRange")}<select value={value.hours} onChange={(event) => onChange({ ...value, hours: Number(event.target.value) })}><option value={1}>{t("dashboard.lastHour")}</option><option value={24}>{t("dashboard.lastDay")}</option><option value={168}>{t("dashboard.lastWeek")}</option></select></label>
  </div>;
}

function RestrictedMetric({ label }: { label: string }) {
  const { t } = useI18n();
  return <MetricCard label={label} value="—" detail={t("dashboard.restricted")} />;
}

function overallFreshness(data: MonitoringData, connections: Connection[]) {
  if (connections.includes("disconnected")) return "disconnected";
  if (data.health.some((item) => item.freshness === "stale") || data.endpoints.some((item) => item.stale) || data.trunks.some((item) => item.stale) || data.telemetry.some((item) => item.freshness === "stale")) return "stale";
  if (connections.some((item) => item !== "connected")) return "syncing";
  return "current";
}

export function DashboardPage({ api, permissions, filters, onFilters, onInspect }: { api: ApiClient; permissions: string[]; filters: InvestigationFilters; onFilters: (value: InvestigationFilters) => void; onInspect: (serverId: string) => void }) {
  const { t } = useI18n();
  const can = useCallback((permission: string) => permissions.includes(permission), [permissions]);
  const [data, setData] = useState<MonitoringData>(emptyData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [callConnection, setCallConnection] = useState<Connection>(can("call.read") ? "connecting" : "connected");
  const operationalResources = useMemo<OperationalResource[]>(() => [
    ...(can("asterisk.read") ? ["asterisk_health" as const, "pjsip" as const] : []),
    ...(can("server.read") ? ["host_telemetry" as const] : []),
    ...(can("alert.read") ? ["alerts" as const] : []),
  ], [can]);
  const [operationalConnection, setOperationalConnection] = useState<Connection>(operationalResources.length ? "connecting" : "connected");

  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    const requests: Array<Promise<void>> = [];
    const next: MonitoringData = { ...emptyData, calls: { servers: [] } };
    if (can("server.read")) requests.push(api.environments().then((items) => { next.environments = items; }), api.allServers().then((items) => { next.servers = items; }), api.hostTelemetry().then((items) => { next.telemetry = items; }));
    if (can("asterisk.read")) requests.push(api.asteriskHealth().then((items) => { next.health = items; }), api.allEndpoints().then((items) => { next.endpoints = items; }), api.allTrunks().then((items) => { next.trunks = items; }));
    if (can("call.read")) requests.push(api.liveCalls().then((result) => { next.calls = result; }));
    if (can("alert.read")) requests.push(api.alerts().then((items) => { next.alerts = items; }));
    const results = await Promise.allSettled(requests);
    setData(next);
    if (results.some((result) => result.status === "rejected")) setError(t("dashboard.partialError"));
    setLoading(false);
  }, [api, can, t]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!can("call.read")) return;
    return api.subscribeLiveCalls((event: CallRealtimeMessage) => setData((current) => {
      if (event.type === "calls.initial") return { ...current, calls: event.payload };
      return { ...current, calls: { servers: [...current.calls.servers.filter((item) => item.server_id !== event.server_id), event.payload] } };
    }), setCallConnection);
  }, [api, can]);

  const operationScope = useMemo(() => {
    if (filters.serverId) return [filters.serverId];
    if (filters.environment) return data.servers.filter((server) => server.environment.code === filters.environment).map((server) => server.id);
    return undefined;
  }, [data.servers, filters.environment, filters.serverId]);
  const operationScopeKey = operationScope?.join(",") ?? "*";
  const refreshOperational = useCallback(async (message: OperationalRealtimeMessage) => {
    if (message.type !== "operations.resync_required" && operationScope && !operationScope.includes(message.server_id)) return;
    const requested = message.type === "operations.resync_required" ? message.payload.resources : [message.resource];
    const allowed = new Set(operationalResources);
    const resources = requested.filter((resource) => allowed.has(resource));
    const requests: Promise<void>[] = [];
    if (resources.includes("asterisk_health")) requests.push(api.asteriskHealth().then((health) => setData((current) => ({ ...current, health }))));
    if (resources.includes("pjsip")) requests.push(Promise.all([api.allEndpoints(), api.allTrunks()]).then(([endpoints, trunks]) => setData((current) => ({ ...current, endpoints, trunks }))));
    if (resources.includes("host_telemetry")) requests.push(api.hostTelemetry().then((telemetry) => setData((current) => ({ ...current, telemetry }))));
    if (resources.includes("alerts")) requests.push(api.alerts().then((alerts) => setData((current) => ({ ...current, alerts }))));
    const results = await Promise.allSettled(requests);
    if (results.some((result) => result.status === "rejected")) {
      setError(t("dashboard.partialError"));
      throw new Error("Operational REST resynchronization failed");
    }
  }, [api, operationScopeKey, operationalResources, t]);
  useEffect(() => {
    if (!operationalResources.length) { setOperationalConnection("connected"); return; }
    return api.subscribeOperationalUpdates(refreshOperational, setOperationalConnection, operationScope);
  }, [api, operationScopeKey, operationalResources, refreshOperational]);

  const selectedServers = useMemo(() => data.servers.filter((server) => (!filters.environment || server.environment.code === filters.environment) && (!filters.serverId || server.id === filters.serverId)), [data.servers, filters]);
  const ids = useMemo(() => new Set(selectedServers.map((server) => server.id)), [selectedServers]);
  const inScope = useCallback((serverId: string) => !can("server.read") || ids.has(serverId), [can, ids]);
  const scoped = useMemo(() => ({
    health: data.health.filter((item) => inScope(item.server_id)), endpoints: data.endpoints.filter((item) => inScope(item.server_id)), trunks: data.trunks.filter((item) => inScope(item.server_id)),
    calls: data.calls.servers.filter((item) => inScope(item.server_id)).flatMap((item) => item.calls), alerts: data.alerts.filter((item) => inScope(item.server_id) && Date.parse(item.last_seen) >= Date.now() - filters.hours * 3_600_000), telemetry: data.telemetry.filter((item) => inScope(item.server_id)),
  }), [data, inScope, filters.hours]);
  const freshness = overallFreshness(data, [callConnection, operationalConnection]);
  const freshnessKey: Record<typeof freshness, TranslationKey> = { current: "dashboard.current", syncing: "dashboard.syncing", stale: "dashboard.stale", disconnected: "dashboard.disconnected" };
  const unhealthyTrunks = scoped.trunks.filter((item) => !isHealthyState(item.state) || item.stale).length;
  const unhealthyEndpoints = scoped.endpoints.filter((item) => !isHealthyState(item.state) || item.stale).length;
  const freshHosts = scoped.telemetry.filter((item) => item.freshness === "fresh").length;
  const storageFree = scoped.telemetry.flatMap((item) => {
    const storage = item.sample?.recording_storage;
    return storage?.total_bytes && storage.available_bytes !== null ? [100 * storage.available_bytes / storage.total_bytes] : [];
  });
  const lowestFree = storageFree.length ? Math.min(...storageFree) : null;

  return <section aria-labelledby="dashboard-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="dashboard-title">{t("dashboard.title")}</h2><p>{t("dashboard.description")}</p></div><Signal state={freshness === "current" ? "ok" : freshness === "syncing" ? "muted" : "warning"}>{t(freshnessKey[freshness])}</Signal></div>
    {can("server.read") && <Filters value={filters} environments={data.environments} servers={data.servers} onChange={onFilters} />}
    {loading && <PageState kind="loading" title={t("dashboard.loading")} />}
    {!loading && error && <p className="session-message" role="status">{error} <button className="button--quiet" onClick={() => void load()}>{t("common.retry")}</button></p>}
    {!loading && <div className="metric-grid">
      {can("server.read") ? <MetricCard label={t("dashboard.servers")} value={selectedServers.length} detail={t("dashboard.monitored", { count: selectedServers.filter((item) => item.monitoring_enabled).length })} state={selectedServers.length ? "ok" : "muted"} /> : <RestrictedMetric label={t("dashboard.servers")} />}
      {can("asterisk.read") ? <MetricCard label={t("dashboard.trunks")} value={scoped.trunks.length} detail={t("dashboard.unhealthy", { count: unhealthyTrunks })} state={unhealthyTrunks ? "danger" : "ok"} /> : <RestrictedMetric label={t("dashboard.trunks")} />}
      {can("asterisk.read") ? <MetricCard label={t("dashboard.endpoints")} value={scoped.endpoints.length} detail={t("dashboard.unavailableCount", { count: unhealthyEndpoints })} state={unhealthyEndpoints ? "warning" : "ok"} /> : <RestrictedMetric label={t("dashboard.endpoints")} />}
      {can("call.read") ? <MetricCard label={t("dashboard.calls")} value={scoped.calls.length} detail={t("dashboard.activeNow")} state={callConnection === "disconnected" ? "warning" : "ok"} /> : <RestrictedMetric label={t("dashboard.calls")} />}
      {can("alert.read") ? <MetricCard label={t("dashboard.alerts")} value={scoped.alerts.filter((item) => item.state !== "RESOLVED").length} detail={t("dashboard.inRange")} state={scoped.alerts.some((item) => item.severity === "CRITICAL" && item.state !== "RESOLVED") ? "danger" : "ok"} /> : <RestrictedMetric label={t("dashboard.alerts")} />}
      {can("server.read") ? <MetricCard label={t("dashboard.hosts")} value={`${freshHosts}/${scoped.telemetry.length}`} detail={t("dashboard.freshHosts")} state={freshHosts === scoped.telemetry.length && scoped.telemetry.length ? "ok" : "warning"} /> : <RestrictedMetric label={t("dashboard.hosts")} />}
      {can("server.read") ? <MetricCard label={t("dashboard.storage")} value={formatPercent(lowestFree)} detail={t("dashboard.lowestFree")} state={lowestFree === null ? "muted" : lowestFree < 10 ? "danger" : lowestFree < 20 ? "warning" : "ok"} /> : <RestrictedMetric label={t("dashboard.storage")} />}
    </div>}
    {!loading && can("server.read") && selectedServers.length === 0 && <PageState kind="empty" title={t("dashboard.empty")}>{t("dashboard.emptyBody")}</PageState>}
    {!loading && selectedServers.length > 0 && <div className="panel table-wrap"><table><caption className="sr-only">{t("dashboard.fleetTable")}</caption><thead><tr><th>{t("dashboard.server")}</th><th>{t("dashboard.environment")}</th><th>{t("dashboard.asterisk")}</th><th>{t("dashboard.calls")}</th><th>{t("dashboard.alerts")}</th><th>{t("common.actions")}</th></tr></thead><tbody>{selectedServers.map((server) => {
      const health = data.health.find((item) => item.server_id === server.id); const calls = data.calls.servers.find((item) => item.server_id === server.id); const alerts = data.alerts.filter((item) => item.server_id === server.id && item.state !== "RESOLVED");
      return <tr key={server.id}><td><strong>{server.name}</strong><small>{server.hostname}</small></td><td>{server.environment.code}</td><td>{health ? <Signal state={health.freshness === "fresh" && health.connection_state === "connected" ? "ok" : "warning"}>{health.connection_state}</Signal> : "—"}</td><td>{calls?.calls.length ?? "—"}</td><td>{can("alert.read") ? alerts.length : "—"}</td><td><button className="button--quiet" onClick={() => onInspect(server.id)}>{t("dashboard.inspect")}</button></td></tr>;
    })}</tbody></table></div>}
  </section>;
}

export function ServersPage({ api, permissions, filters, onFilters }: { api: ApiClient; permissions: string[]; filters: InvestigationFilters; onFilters: (value: InvestigationFilters) => void }) {
  const { locale, t } = useI18n();
  const can = useCallback((permission: string) => permissions.includes(permission), [permissions]);
  const [environments, setEnvironments] = useState<Environment[]>([]); const [servers, setServers] = useState<Server[]>([]);
  const [health, setHealth] = useState<AsteriskHealth>(); const [endpoints, setEndpoints] = useState<PjsipEndpoint[]>([]); const [trunks, setTrunks] = useState<SipTrunk[]>([]);
  const [calls, setCalls] = useState<LiveCallsResponse>({ servers: [] }); const [telemetry, setTelemetry] = useState<HostTelemetry>(); const [logs, setLogs] = useState<LogEvent[]>([]); const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string>(); const [callConnection, setCallConnection] = useState<Connection>(can("call.read") ? "connecting" : "connected");
  const operationalResources = useMemo<OperationalResource[]>(() => [
    ...(can("asterisk.read") ? ["asterisk_health" as const, "pjsip" as const] : []),
    ...(can("server.read") ? ["host_telemetry" as const] : []),
    ...(can("alert.read") ? ["alerts" as const] : []),
  ], [can]);
  const [operationalConnection, setOperationalConnection] = useState<Connection>(operationalResources.length ? "connecting" : "connected");

  useEffect(() => { Promise.all([api.environments(), api.allServers()]).then(([nextEnvironments, items]) => { setEnvironments(nextEnvironments); setServers(items); const first = items[0]; if (!filters.serverId && first) onFilters({ ...filters, serverId: first.id }); }).catch(() => setError(t("serverDetail.unavailable"))); }, [api]);
  const selected = servers.find((item) => item.id === filters.serverId);
  const load = useCallback(async () => {
    if (!filters.serverId) { setLoading(false); return; }
    setLoading(true); setError(undefined); const requests: Array<Promise<void>> = [];
    if (can("asterisk.read")) requests.push(api.serverAsteriskHealth(filters.serverId).then(setHealth), api.allEndpoints(filters.serverId).then(setEndpoints), api.allTrunks(filters.serverId).then(setTrunks));
    if (can("server.read")) requests.push(api.serverHostTelemetry(filters.serverId).then(setTelemetry));
    if (can("call.read")) requests.push(api.liveCalls().then(setCalls));
    if (can("alert.read")) requests.push(api.alerts(undefined, filters.serverId).then(setAlerts));
    if (can("log.read")) { const query = new URLSearchParams({ server_id: filters.serverId, from_time: new Date(Date.now() - filters.hours * 3_600_000).toISOString(), to_time: new Date().toISOString(), limit: "20", offset: "0" }); requests.push(api.logs(query).then((value) => setLogs(value.items))); }
    const results = await Promise.allSettled(requests); if (results.some((result) => result.status === "rejected")) setError(t("dashboard.partialError")); setLoading(false);
  }, [api, can, filters.serverId, filters.hours, t]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (!can("call.read")) return; return api.subscribeLiveCalls((event) => { if (event.type === "calls.initial") setCalls(event.payload); else setCalls((current) => ({ servers: [...current.servers.filter((item) => item.server_id !== event.server_id), event.payload] })); }, setCallConnection); }, [api, can]);
  const refreshOperational = useCallback(async (message: OperationalRealtimeMessage) => {
    if (!filters.serverId || (message.type !== "operations.resync_required" && message.server_id !== filters.serverId)) return;
    const requested = message.type === "operations.resync_required" ? message.payload.resources : [message.resource];
    const allowed = new Set(operationalResources);
    const resources = requested.filter((resource) => allowed.has(resource));
    const requests: Promise<void>[] = [];
    if (resources.includes("asterisk_health")) requests.push(api.serverAsteriskHealth(filters.serverId).then(setHealth));
    if (resources.includes("pjsip")) requests.push(Promise.all([api.allEndpoints(filters.serverId), api.allTrunks(filters.serverId)]).then(([nextEndpoints, nextTrunks]) => { setEndpoints(nextEndpoints); setTrunks(nextTrunks); }));
    if (resources.includes("host_telemetry")) requests.push(api.serverHostTelemetry(filters.serverId).then(setTelemetry));
    if (resources.includes("alerts")) requests.push(api.alerts(undefined, filters.serverId).then(setAlerts));
    const results = await Promise.allSettled(requests);
    if (results.some((result) => result.status === "rejected")) {
      setError(t("dashboard.partialError"));
      throw new Error("Operational REST resynchronization failed");
    }
  }, [api, filters.serverId, operationalResources, t]);
  useEffect(() => {
    if (!operationalResources.length || !filters.serverId) { setOperationalConnection(operationalResources.length ? "connecting" : "connected"); return; }
    return api.subscribeOperationalUpdates(refreshOperational, setOperationalConnection, [filters.serverId]);
  }, [api, filters.serverId, operationalResources, refreshOperational]);
  const currentCalls = calls.servers.find((item) => item.server_id === filters.serverId)?.calls ?? [];
  const sample = telemetry?.sample; const storage = sample?.recording_storage;
  const status = callConnection === "disconnected" || operationalConnection === "disconnected" ? "disconnected" : health?.freshness === "stale" || telemetry?.freshness === "stale" || endpoints.some((item) => item.stale) || trunks.some((item) => item.stale) ? "stale" : loading || callConnection !== "connected" || operationalConnection !== "connected" ? "syncing" : "current";
  const statusKey: Record<typeof status, TranslationKey> = { current: "dashboard.current", syncing: "dashboard.syncing", stale: "dashboard.stale", disconnected: "dashboard.disconnected" };

  return <section aria-labelledby="servers-title"><div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="servers-title">{t("serverDetail.title")}</h2><p>{t("serverDetail.description")}</p></div><Signal state={status === "current" ? "ok" : "warning"}>{t(statusKey[status])}</Signal></div>
    <Filters value={filters} environments={environments} servers={servers} onChange={onFilters} />
    {loading && <PageState kind="loading" title={t("serverDetail.loading")} />}{error && <p className="session-message" role="status">{error} <button className="button--quiet" onClick={() => void load()}>{t("common.retry")}</button></p>}
    {!loading && !selected && <PageState kind="empty" title={t("serverDetail.select")}>{t("serverDetail.selectBody")}</PageState>}
    {!loading && selected && <><div className="server-identity panel"><div><p className="eyebrow">{selected.environment.code}</p><h3>{selected.name}</h3><p>{selected.hostname}{selected.location ? ` · ${selected.location}` : ""}</p></div><Signal state={selected.monitoring_enabled ? "ok" : "muted"}>{t(selected.monitoring_enabled ? "dashboard.monitoringOn" : "dashboard.monitoringOff")}</Signal></div>
      <div className="detail-grid">
        {can("asterisk.read") ? <article className="panel detail-card"><h3>{t("serverDetail.health")}</h3><dl><div><dt>{t("serverDetail.connection")}</dt><dd>{health?.connection_state ?? "—"}</dd></div><div><dt>{t("serverDetail.version")}</dt><dd>{health?.core?.asterisk_version ?? "—"}</dd></div><div><dt>{t("serverDetail.currentCalls")}</dt><dd>{health?.core?.current_calls ?? currentCalls.length}</dd></div><div><dt>{t("serverDetail.observed")}</dt><dd>{health?.observed_at ? new Date(health.observed_at).toLocaleString(locale) : "—"}</dd></div></dl></article> : <RestrictedPanel title={t("serverDetail.health")} />}
        {can("server.read") ? <article className="panel detail-card"><h3>{t("serverDetail.telemetry")}</h3><dl><div><dt>CPU</dt><dd>{formatPercent(sample?.cpu_usage_percent ?? null)}</dd></div><div><dt>{t("serverDetail.load")}</dt><dd>{sample?.load_1 ?? "—"}</dd></div><div><dt>{t("serverDetail.memoryAvailable")}</dt><dd>{formatBytes(sample?.memory_available_bytes ?? null)}</dd></div><div><dt>{t("serverDetail.service")}</dt><dd>{sample?.asterisk_service_state ?? "—"}</dd></div></dl>{!sample && <small>{t("serverDetail.capabilityUnavailable")}</small>}</article> : <RestrictedPanel title={t("serverDetail.telemetry")} />}
        {can("server.read") ? <article className="panel detail-card"><h3>{t("serverDetail.storage")}</h3><dl><div><dt>{t("serverDetail.mountpoint")}</dt><dd>{storage?.mountpoint ?? "—"}</dd></div><div><dt>{t("serverDetail.used")}</dt><dd>{formatBytes(storage?.used_bytes ?? null)}</dd></div><div><dt>{t("serverDetail.available")}</dt><dd>{formatBytes(storage?.available_bytes ?? null)}</dd></div><div><dt>{t("serverDetail.growth")}</dt><dd>{formatBytes(storage?.growth_bytes ?? null)}</dd></div></dl>{!storage?.total_bytes && <small>{t("serverDetail.capabilityUnavailable")}</small>}</article> : <RestrictedPanel title={t("serverDetail.storage")} />}
      </div>
      {can("asterisk.read") ? <div className="response-grid"><ResourceTable title={t("serverDetail.trunks")} nameHeading={t("dashboard.trunks")} items={trunks.map((item) => ({ id: item.id, name: item.label, state: item.state, detail: item.provider ?? item.source_id, stale: item.stale }))} /><ResourceTable title={t("serverDetail.endpoints")} nameHeading={t("dashboard.endpoints")} items={endpoints.map((item) => ({ id: item.id, name: item.source_id, state: item.state, detail: t("serverDetail.contacts", { count: item.contacts_count ?? 0 }), stale: item.stale }))} /></div> : <RestrictedPanel title={t("serverDetail.pjsip")} />}
      {can("call.read") ? <ResourceTable title={t("serverDetail.calls")} nameHeading={t("serverDetail.logicalCall")} items={currentCalls.map((item) => ({ id: item.logical_id, name: item.logical_id, state: item.state, detail: `${item.caller_number ?? "—"} → ${item.connected_number ?? "—"}`, stale: item.freshness === "stale" }))} /> : <RestrictedPanel title={t("serverDetail.calls")} />}
      <div className="response-grid">{can("log.read") ? <ActivityList title={t("serverDetail.logs")} empty={t("serverDetail.noLogs")} items={logs.map((item) => ({ id: item.id, title: item.message, meta: `${item.level} · ${new Date(item.occurred_at).toLocaleString(locale)}` }))} /> : <RestrictedPanel title={t("serverDetail.logs")} />}{can("alert.read") ? <ActivityList title={t("serverDetail.alerts")} empty={t("serverDetail.noAlerts")} items={alerts.filter((item) => Date.parse(item.last_seen) >= Date.now() - filters.hours * 3_600_000).map((item) => ({ id: item.id, title: item.rule_code, meta: `${item.severity} · ${item.state}` }))} /> : <RestrictedPanel title={t("serverDetail.alerts")} />}</div>
    </>}
  </section>;
}

function RestrictedPanel({ title }: { title: string }) { const { t } = useI18n(); return <article className="panel detail-card"><h3>{title}</h3><p className="restricted-copy">{t("dashboard.restricted")}</p></article>; }
function ResourceTable({ title, nameHeading, items }: { title: string; nameHeading: string; items: Array<{ id: string; name: string; state: string; detail: string; stale: boolean }> }) { const { t } = useI18n(); return <article className="panel resource-panel"><h3>{title}</h3>{items.length === 0 ? <p className="empty-inline">{t("serverDetail.noResources")}</p> : <div className="table-wrap"><table><thead><tr><th>{nameHeading}</th><th>{t("serverDetail.state")}</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.detail}</small></td><td><Signal state={item.stale ? "warning" : isHealthyState(item.state) ? "ok" : "danger"}>{item.state}{item.stale ? ` · ${t("dashboard.staleShort")}` : ""}</Signal></td></tr>)}</tbody></table></div>}</article>; }
function ActivityList({ title, empty, items }: { title: string; empty: string; items: Array<{ id: string; title: string; meta: string }> }) { return <article className="panel activity-panel"><h3>{title}</h3>{items.length === 0 ? <p className="empty-inline">{empty}</p> : <ul>{items.map((item) => <li key={item.id}><strong>{item.title}</strong><small>{item.meta}</small></li>)}</ul>}</article>; }
