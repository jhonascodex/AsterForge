import { useMemo, useState, type FormEvent } from "react";

import {
  ApiClient,
  type ReportFamily,
  type ReportResponse,
  type SearchResponse,
} from "./api";
import { useI18n, type TranslationKey } from "./i18n";
import { PageState } from "./ui";

const families: Array<{ value: ReportFamily; label: TranslationKey }> = [
  { value: "server-availability", label: "reports.family.server" },
  { value: "calls", label: "reports.family.calls" },
  { value: "failures", label: "reports.family.failures" },
  { value: "trunk-availability", label: "reports.family.trunks" },
  { value: "endpoint-availability", label: "reports.family.endpoints" },
  { value: "incidents", label: "reports.family.incidents" },
  { value: "host-storage", label: "reports.family.hostStorage" },
];

const searchTypeLabels: Record<SearchResponse["items"][number]["entity_type"], TranslationKey> = {
  server: "reports.type.server", endpoint: "reports.type.endpoint", trunk: "reports.type.trunk",
  phone_number: "reports.type.phone", unique_id: "reports.type.uniqueId",
  linked_id: "reports.type.linkedId",
};

const columnLabels: Record<string, TranslationKey> = {
  event: "reports.column.event", state: "reports.column.state",
  last_seen: "reports.column.lastSeen", resolved_at: "reports.column.resolvedAt",
  source_id: "reports.column.sourceId", unique_id: "reports.column.uniqueId",
  linked_id: "reports.column.linkedId", caller_number: "reports.column.caller",
  connected_number: "reports.column.connected", disposition: "reports.column.disposition",
  duration_seconds: "reports.column.duration", trunk: "reports.column.trunk",
  from_state: "reports.column.fromState", to_state: "reports.column.toState",
  endpoint: "reports.column.endpoint", contacts_count: "reports.column.contacts",
  stale: "reports.column.stale", incident_id: "reports.column.incidentId",
  title: "reports.column.title", cpu_usage_percent: "reports.column.cpu",
  memory_total_bytes: "reports.column.memoryTotal",
  memory_available_bytes: "reports.column.memoryAvailable", load_1: "reports.column.load",
  recording_total_bytes: "reports.column.storageTotal",
  recording_used_bytes: "reports.column.storageUsed",
  recording_available_bytes: "reports.column.storageAvailable",
};

function localDateTime(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function queryFor(from: string, to: string) {
  return new URLSearchParams({
    from_time: new Date(from).toISOString(),
    to_time: new Date(to).toISOString(),
    limit: "200",
  });
}

function localizedColumn(column: string, translate: (key: TranslationKey) => string) {
  const key = columnLabels[column];
  return key === undefined ? column : translate(key);
}

export function ReportsPage({ api, canExport }: { api: ApiClient; canExport: boolean }) {
  const { locale, t } = useI18n();
  const [family, setFamily] = useState<ReportFamily>("calls");
  const [from, setFrom] = useState(() => localDateTime(new Date(Date.now() - 86_400_000)));
  const [to, setTo] = useState(() => localDateTime(new Date()));
  const [report, setReport] = useState<ReportResponse>();
  const [search, setSearch] = useState<SearchResponse>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const columns = useMemo(
    () => [...new Set(report?.items.flatMap((item) => Object.keys(item.values)) ?? [])],
    [report],
  );

  async function runReport(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(undefined);
    try { setReport(await api.report(family, queryFor(from, to))); }
    catch { setError(t("reports.unavailable")); }
    finally { setBusy(false); }
  }

  async function runSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined);
    const query = String(new FormData(event.currentTarget).get("query"));
    try { setSearch(await api.search(query)); }
    catch { setError(t("reports.searchUnavailable")); }
  }

  async function download() {
    setBusy(true); setError(undefined);
    try {
      const blob = await api.exportReport(family, queryFor(from, to));
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `asterforge-${family}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch { setError(t("reports.exportUnavailable")); }
    finally { setBusy(false); }
  }

  return <section aria-labelledby="reports-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="reports-title">{t("reports.title")}</h2><p>{t("reports.description")}</p></div><span className="freshness"><span aria-hidden="true">●</span> {t("reports.bounded")}</span></div>
    <form className="panel report-search" aria-label={t("reports.searchForm")} onSubmit={(event) => void runSearch(event)}><label>{t("reports.searchLabel")}<input name="query" minLength={2} maxLength={100} required /></label><button className="button--quiet">{t("reports.search")}</button></form>
    {search && <article className="panel report-results"><h3>{t("reports.searchResults")}</h3>{search.items.length === 0 ? <p className="empty-inline">{t("reports.noSearchResults")}</p> : <ul>{search.items.map((item) => <li key={`${item.entity_type}-${item.server_id}-${item.identifier}`}><strong>{item.label}</strong><small>{t(searchTypeLabels[item.entity_type])} · {item.identifier}</small></li>)}</ul>}{search.truncated && <small>{t("reports.searchTruncated")}</small>}</article>}
    <form className="panel report-filters" aria-label={t("reports.filters")} onSubmit={(event) => void runReport(event)}><label>{t("reports.family")}<select value={family} onChange={(event) => setFamily(event.target.value as ReportFamily)}>{families.map((item) => <option key={item.value} value={item.value}>{t(item.label)}</option>)}</select></label><label>{t("reports.from")}<input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} required /></label><label>{t("reports.to")}<input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} required /></label><button className="button--primary" disabled={busy}>{t(busy ? "reports.loading" : "reports.run")}</button>{canExport && <button type="button" className="button--quiet" disabled={busy} onClick={() => void download()}>{t("reports.export")}</button>}</form>
    {error && <p className="session-message" role="alert">{error}</p>}
    {report && report.items.length === 0 && <PageState kind="empty" title={t("reports.empty")}>{t("reports.emptyBody")}</PageState>}
    {report && report.items.length > 0 && <div className="panel table-wrap report-table"><table><caption>{t("reports.caption", { count: report.items.length })}</caption><thead><tr><th>{t("reports.time")}</th><th>{t("reports.server")}</th>{columns.map((column) => <th key={column}>{localizedColumn(column, t)}</th>)}</tr></thead><tbody>{report.items.map((item, index) => <tr key={`${item.server_id}-${item.occurred_at}-${index}`}><td>{new Date(item.occurred_at).toLocaleString(locale)}</td><td>{item.server_name}</td>{columns.map((column) => <td key={column}>{String(item.values[column] ?? "—")}</td>)}</tr>)}</tbody></table>{report.has_more && <p className="report-limit">{t("reports.moreAvailable")}</p>}</div>}
  </section>;
}
