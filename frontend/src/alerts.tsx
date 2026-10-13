import { useEffect, useState, type FormEvent } from "react";

import {
  ApiClient,
  ApiError,
  type Alert,
  type Incident,
  type IncidentEvent,
  type MaintenanceWindow,
} from "./api";
import { useI18n } from "./i18n";
import { FieldError, PageState } from "./ui";

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

export function AlertsPage({ api, canAcknowledge }: { api: ApiClient; canAcknowledge: boolean }) {
  const { t } = useI18n();
  const [alerts, setAlerts] = useState<Alert[]>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string>();

  async function load() {
    setError(undefined);
    try { setAlerts(await api.alerts()); }
    catch (nextError) { setError(errorMessage(nextError, t("alerts.unavailableBody"))); }
  }

  useEffect(() => { void load(); }, [api]);

  async function acknowledge(alert: Alert) {
    const comment = window.prompt(t("alerts.commentPrompt"));
    if (!comment?.trim()) return;
    setBusy(alert.id);
    try { await api.acknowledgeAlert(alert.id, comment.trim()); await load(); }
    catch (nextError) { setError(errorMessage(nextError, t("alerts.unavailableBody"))); }
    finally { setBusy(undefined); }
  }

  return <section aria-labelledby="alerts-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="alerts-title">{t("alerts.title")}</h2><p>{t("alerts.description")}</p></div><span className="freshness"><span aria-hidden="true">●</span> {t("alerts.deduplicated")}</span></div>
    {!alerts && !error ? <PageState kind="loading" title={t("alerts.loading")} /> : error ? <PageState kind="error" title={t("alerts.unavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState> : alerts?.length === 0 ? <PageState kind="empty" title={t("alerts.empty")}>{t("alerts.emptyBody")}</PageState> : <div className="panel table-wrap"><table><thead><tr><th>{t("alerts.rule")}</th><th>{t("alerts.resource")}</th><th>{t("alerts.severity")}</th><th>{t("alerts.state")}</th><th>{t("alerts.lastSeen")}</th><th>{t("alerts.action")}</th></tr></thead><tbody>{alerts?.map((alert) => <tr key={alert.id}><td><strong>{alert.rule_code}</strong>{alert.suppressed && <small>{t("alerts.suppressed")}</small>}</td><td>{alert.resource_type}<small>{alert.resource_key}</small></td><td>{alert.severity}</td><td>{alert.state}</td><td>{new Date(alert.last_seen).toLocaleString()}</td><td>{canAcknowledge && alert.state === "OPEN" ? <button className="button--quiet" disabled={busy === alert.id} onClick={() => void acknowledge(alert)}>{t("alerts.acknowledge")}</button> : "—"}</td></tr>)}</tbody></table></div>}
  </section>;
}

export function IncidentsPage({ api }: { api: ApiClient }) {
  const { t } = useI18n();
  const [incidents, setIncidents] = useState<Incident[]>();
  const [timeline, setTimeline] = useState<IncidentEvent[]>();
  const [selected, setSelected] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    api.incidents().then(setIncidents).catch((nextError: unknown) => setError(errorMessage(nextError, t("incidents.unavailableBody"))));
  }, [api]);

  async function inspect(id: string) {
    setSelected(id); setTimeline(undefined); setError(undefined);
    try { setTimeline(await api.incidentTimeline(id)); }
    catch (nextError) { setError(errorMessage(nextError, t("incidents.unavailableBody"))); }
  }

  return <section aria-labelledby="incidents-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="incidents-title">{t("incidents.title")}</h2><p>{t("incidents.description")}</p></div></div>
    {!incidents && !error ? <PageState kind="loading" title={t("incidents.loading")} /> : error ? <PageState kind="error" title={t("incidents.unavailable")}>{error}</PageState> : incidents?.length === 0 ? <PageState kind="empty" title={t("incidents.empty")}>{t("incidents.emptyBody")}</PageState> : <div className="response-grid"><div className="panel table-wrap"><table><thead><tr><th>{t("incidents.incident")}</th><th>{t("incidents.state")}</th><th>{t("incidents.alerts")}</th></tr></thead><tbody>{incidents?.map((incident) => <tr key={incident.id}><td><button className="button--quiet" onClick={() => void inspect(incident.id)}>{incident.title}</button><small>{new Date(incident.opened_at).toLocaleString()}</small></td><td>{incident.state}</td><td>{incident.alert_ids.length}</td></tr>)}</tbody></table></div>{selected && <div className="panel"><h3>{t("incidents.timeline")}</h3>{!timeline ? <PageState kind="loading" title={t("incidents.loadingTimeline")} /> : <ol className="timeline-list">{timeline.map((event) => <li key={event.id}><strong>{event.event_type}</strong><small>{new Date(event.occurred_at).toLocaleString()}</small></li>)}</ol>}</div>}</div>}
  </section>;
}

function localDate(hoursFromNow: number) {
  const date = new Date(Date.now() + hoursFromNow * 60 * 60 * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60 * 1000).toISOString().slice(0, 16);
}

export function MaintenancePage({ api }: { api: ApiClient }) {
  const { t } = useI18n();
  const [windows, setWindows] = useState<MaintenanceWindow[]>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function load() {
    try { setWindows(await api.maintenanceWindows()); }
    catch (nextError) { setError(errorMessage(nextError, t("maintenance.unavailableBody"))); }
  }
  useEffect(() => { void load(); }, [api]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(undefined);
    const fields = new FormData(event.currentTarget);
    const resourceType = String(fields.get("resource_type") ?? "");
    const resourceKey = String(fields.get("resource_key") ?? "").trim();
    try {
      await api.createMaintenanceWindow({
        server_id: String(fields.get("server_id")).trim(),
        starts_at: new Date(String(fields.get("starts_at"))).toISOString(),
        ends_at: new Date(String(fields.get("ends_at"))).toISOString(),
        reason: String(fields.get("reason")).trim(),
        ...(resourceType && resourceKey ? { resource_type: resourceType, resource_key: resourceKey } : {}),
      });
      event.currentTarget.reset(); await load();
    } catch (nextError) { setError(errorMessage(nextError, t("maintenance.unavailableBody"))); }
    finally { setBusy(false); }
  }

  async function end(id: string) {
    setBusy(true); setError(undefined);
    try { await api.endMaintenanceWindow(id); await load(); }
    catch (nextError) { setError(errorMessage(nextError, t("maintenance.unavailableBody"))); }
    finally { setBusy(false); }
  }

  return <section aria-labelledby="maintenance-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="maintenance-title">{t("maintenance.title")}</h2><p>{t("maintenance.description")}</p></div><span className="freshness"><span aria-hidden="true">●</span> {t("maintenance.collectionContinues")}</span></div>
    <FieldError>{error}</FieldError>
    <div className="response-grid"><form className="panel form-panel" aria-label={t("maintenance.form")} onSubmit={submit}><h3>{t("maintenance.create")}</h3><label>{t("maintenance.serverId")}<input name="server_id" required /></label><label>{t("maintenance.scope")}<select name="resource_type"><option value="">{t("maintenance.wholeServer")}</option>{["asterisk", "calls", "endpoint", "filesystem", "host", "storage", "trunk"].map((value) => <option key={value}>{value}</option>)}</select></label><label>{t("maintenance.resourceKey")}<input name="resource_key" maxLength={255} /></label><label>{t("maintenance.starts")}<input name="starts_at" type="datetime-local" defaultValue={localDate(0)} required /></label><label>{t("maintenance.ends")}<input name="ends_at" type="datetime-local" defaultValue={localDate(1)} required /></label><label>{t("maintenance.reason")}<textarea name="reason" required maxLength={500} /></label><button className="button--primary" disabled={busy}>{t("maintenance.save")}</button></form><div className="panel table-wrap"><table><thead><tr><th>{t("maintenance.window")}</th><th>{t("maintenance.scope")}</th><th>{t("maintenance.status")}</th><th>{t("alerts.action")}</th></tr></thead><tbody>{windows?.map((item) => <tr key={item.id}><td><strong>{item.reason}</strong><small>{new Date(item.starts_at).toLocaleString()} – {new Date(item.ends_at).toLocaleString()}</small></td><td>{item.resource_type ?? t("maintenance.wholeServer")}<small>{item.resource_key}</small></td><td>{t(item.active ? "maintenance.active" : "maintenance.inactive")}</td><td>{item.active && <button className="button--quiet" disabled={busy} onClick={() => void end(item.id)}>{t("maintenance.end")}</button>}</td></tr>)}</tbody></table>{windows?.length === 0 && <PageState kind="empty" title={t("maintenance.empty")}>{t("maintenance.emptyBody")}</PageState>}</div></div>
  </section>;
}
