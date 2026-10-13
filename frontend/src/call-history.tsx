import { useEffect, useState, type FormEvent } from "react";

import {
  ApiClient,
  ApiError,
  type CallHistoryPage as CallHistoryResult,
  type CallTimelinePage,
  type HistoryCapabilities,
} from "./api";
import { useI18n } from "./i18n";
import { PageState } from "./ui";

const CDR_PAGE_SIZE = 50;
const CEL_PAGE_SIZE = 100;

function localDateTime(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function historyQuery(form: HTMLFormElement, offset: number) {
  const fields = new FormData(form);
  const query = new URLSearchParams({ limit: String(CDR_PAGE_SIZE), offset: String(offset) });
  for (const [name, value] of fields.entries()) {
    const text = String(value).trim();
    if (!text) continue;
    query.set(name, name.startsWith("started_") ? new Date(text).toISOString() : text);
  }
  return query;
}

function correlationId(item: CallHistoryResult["items"][number]) {
  return item.linked_id ?? item.unique_id;
}

export function CallHistoryPage({ api, canRead = true }: { api: ApiClient; canRead?: boolean }) {
  const { locale, t } = useI18n();
  const [capabilities, setCapabilities] = useState<HistoryCapabilities>();
  const [result, setResult] = useState<CallHistoryResult>();
  const [query, setQuery] = useState<URLSearchParams>();
  const [loading, setLoading] = useState(canRead);
  const [error, setError] = useState<string>();
  const [unauthorized, setUnauthorized] = useState(!canRead);
  const [timeline, setTimeline] = useState<CallTimelinePage>();
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string>();

  async function load(nextQuery: URLSearchParams, includeCapabilities = false) {
    setLoading(true); setError(undefined); setTimeline(undefined); setTimelineError(undefined); setQuery(nextQuery);
    try {
      const [nextResult, nextCapabilities] = await Promise.all([
        api.callHistory(nextQuery),
        includeCapabilities ? api.callHistoryCapabilities() : Promise.resolve(capabilities),
      ]);
      setResult(nextResult);
      if (nextCapabilities) setCapabilities(nextCapabilities);
    } catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 403) setUnauthorized(true);
      else setError(t("history.unavailableBody"));
    } finally { setLoading(false); }
  }

  useEffect(() => {
    if (!canRead) return;
    const initial = new URLSearchParams({
      started_from: new Date(Date.now() - 86_400_000).toISOString(),
      started_to: new Date().toISOString(),
      limit: String(CDR_PAGE_SIZE), offset: "0",
    });
    void load(initial, true);
  }, [api, canRead]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(historyQuery(event.currentTarget, 0));
  }

  function page(offset: number) {
    if (!query) return;
    const next = new URLSearchParams(query);
    next.set("offset", String(Math.max(0, offset)));
    void load(next);
  }

  async function inspectTimeline(item: CallHistoryResult["items"][number]) {
    const correlation = correlationId(item);
    if (!correlation || !query) return;
    const timelineQuery = new URLSearchParams({
      server_id: item.server_id,
      correlation_id: correlation,
      started_from: query.get("started_from") ?? "",
      started_to: query.get("started_to") ?? "",
      limit: String(CEL_PAGE_SIZE), offset: "0",
    });
    setTimelineLoading(true); setTimeline(undefined); setTimelineError(undefined);
    try { setTimeline(await api.callTimeline(timelineQuery)); }
    catch (nextError) {
      if (nextError instanceof ApiError && nextError.status === 403) setUnauthorized(true);
      else setTimelineError(t("history.timelineUnavailableBody"));
    } finally { setTimelineLoading(false); }
  }

  if (unauthorized) return <PageState kind="unauthorized" title={t("history.unauthorized")}>{t("history.unauthorizedBody")}</PageState>;

  return <section aria-labelledby="history-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="history-title">{t("history.title")}</h2><p>{t("history.description")}</p></div><span className="freshness"><span aria-hidden="true">●</span> {t("history.bounded")}</span></div>
    <form className="history-filters panel" aria-label={t("history.filters")} onSubmit={submit}>
      <label>{t("history.from")}<input name="started_from" type="datetime-local" defaultValue={localDateTime(new Date(Date.now() - 86_400_000))} required /></label>
      <label>{t("history.to")}<input name="started_to" type="datetime-local" defaultValue={localDateTime(new Date())} required /></label>
      <label>{t("history.serverId")}<input name="server_id" /></label>
      <label>{t("history.caller")}<input name="caller_number" maxLength={255} /></label>
      <label>{t("history.connected")}<input name="connected_number" maxLength={255} /></label>
      <label>{t("history.disposition")}<input name="disposition" maxLength={100} /></label>
      <label>{t("history.correlation")}<input name="correlation_id" maxLength={255} /></label>
      <button className="button--primary" disabled={loading}>{t(loading ? "history.searching" : "history.search")}</button>
    </form>
    {capabilities && <div className="history-capabilities" aria-label={t("history.sources")}>
      <span className={`badge ${capabilities.cdr.status === "available" ? "badge--ok" : "badge--muted"}`}>CDR · {t(capabilities.cdr.status === "available" ? "history.configured" : "history.unconfigured")}</span>
      <span className={`badge ${capabilities.cel.status === "available" ? "badge--ok" : "badge--muted"}`}>CEL · {t(capabilities.cel.status === "available" ? "history.configured" : "history.unconfigured")}</span>
    </div>}
    {loading && !result ? <PageState kind="loading" title={t("history.loading")} /> : error ? <PageState kind="error" title={t("history.unavailable")} action={query && <button onClick={() => void load(query, !capabilities)}>{t("common.retry")}</button>}>{error}</PageState> : result?.availability === "unconfigured" ? <PageState kind="empty" title={t("history.cdrUnconfigured")}>{t("history.cdrUnconfiguredBody")}</PageState> : result && result.items.length === 0 ? <PageState kind="empty" title={t("history.empty")}>{t("history.emptyBody")}</PageState> : result && <div className="panel table-wrap"><table><caption>{t("history.caption", { count: result.total })}</caption><thead><tr><th>{t("history.started")}</th><th>{t("history.server")}</th><th>{t("history.parties")}</th><th>{t("history.disposition")}</th><th>{t("history.identifiers")}</th><th>{t("history.duration")}</th><th>{t("history.timeline")}</th></tr></thead><tbody>{result.items.map((item) => <tr key={item.id}><td>{new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "medium" }).format(new Date(item.started_at))}</td><td><code>{item.server_id}</code></td><td>{item.caller_number ?? "—"} → {item.connected_number ?? "—"}</td><td>{item.disposition ?? "—"}</td><td><small>LinkedID</small><code>{item.linked_id ?? "—"}</code><small>UniqueID</small><code>{item.unique_id ?? "—"}</code></td><td>{item.duration_seconds ?? "—"}</td><td>{capabilities?.cel.status === "available" && correlationId(item) ? <button className="button--quiet" onClick={() => void inspectTimeline(item)}>{t("history.inspect")}</button> : <span>{t(capabilities?.cel.status === "unconfigured" ? "history.celUnconfiguredShort" : "history.noCorrelation")}</span>}</td></tr>)}</tbody></table><div className="log-pagination"><button className="button--quiet" disabled={result.offset === 0 || loading} onClick={() => page(result.offset - result.limit)}>{t("history.previous")}</button><span>{t("history.resultWindow", { start: result.offset + 1, end: result.offset + result.items.length, total: result.total })}</span><button className="button--quiet" disabled={result.offset + result.items.length >= result.total || loading} onClick={() => page(result.offset + result.limit)}>{t("history.next")}</button></div></div>}
    {capabilities?.cel.status === "unconfigured" && <PageState kind="empty" title={t("history.celUnconfigured")}>{t("history.celUnconfiguredBody")}</PageState>}
    {timelineLoading && <PageState kind="loading" title={t("history.timelineLoading")} />}
    {timelineError && <PageState kind="error" title={t("history.timelineUnavailable")}>{timelineError}</PageState>}
    {timeline?.availability === "unconfigured" && <PageState kind="empty" title={t("history.celUnconfigured")}>{t("history.celUnconfiguredBody")}</PageState>}
    {timeline?.availability === "available" && timeline.items.length === 0 && <PageState kind="empty" title={t("history.timelineEmpty")}>{t("history.timelineEmptyBody")}</PageState>}
    {timeline?.availability === "available" && timeline.items.length > 0 && <article className="panel history-timeline" aria-labelledby="history-timeline-title"><h3 id="history-timeline-title">{t("history.timelineFor", { correlation: timeline.correlation_id })}</h3><ol className="timeline-list">{timeline.items.map((event) => <li key={event.id}><strong>{event.event_type}</strong><span>{new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "medium" }).format(new Date(event.event_at))}</span><small>{[event.channel, event.peer, event.unique_id, event.linked_id].filter(Boolean).join(" · ")}</small></li>)}</ol></article>}
    <p className="read-only-inline">{t("history.readOnly")}</p>
  </section>;
}
