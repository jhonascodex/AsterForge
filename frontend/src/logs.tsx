import { useEffect, useState, type FormEvent } from "react";

import { ApiClient, ApiError, type LogSearchResponse, type LogSourceStatus } from "./api";
import { useI18n } from "./i18n";
import { PageState } from "./ui";

const PAGE_SIZE = 50;

function utcInput(hoursAgo = 0) {
  const date = new Date(Date.now() - hoursAgo * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

function queryFrom(form: HTMLFormElement, offset: number) {
  const fields = new FormData(form);
  const query = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
  for (const [name, value] of fields.entries()) {
    const text = String(value).trim();
    if (!text) continue;
    query.set(name, name.endsWith("_time") ? new Date(text).toISOString() : text);
  }
  return query;
}

export function LogsPage({ api }: { api: ApiClient }) {
  const { t } = useI18n();
  const [result, setResult] = useState<LogSearchResponse>();
  const [sources, setSources] = useState<LogSourceStatus[]>([]);
  const [query, setQuery] = useState<URLSearchParams>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  async function load(nextQuery: URLSearchParams) {
    setLoading(true);
    setError(undefined);
    try {
      const [logs, statuses] = await Promise.all([api.logs(nextQuery), api.logSources()]);
      setResult(logs);
      setSources(statuses);
      setQuery(nextQuery);
    } catch (nextError) {
      setError(nextError instanceof ApiError ? nextError.message : t("logs.unavailableBody"));
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(queryFrom(event.currentTarget, 0));
  }

  useEffect(() => {
    const initial = new URLSearchParams({
      from_time: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      to_time: new Date().toISOString(),
      offset: "0",
      limit: String(PAGE_SIZE),
    });
    void load(initial);
  }, [api]);

  function page(offset: number) {
    if (!query) return;
    const next = new URLSearchParams(query);
    next.set("offset", String(Math.max(0, offset)));
    void load(next);
  }

  return <section aria-labelledby="logs-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="logs-title">{t("logs.title")}</h2><p>{t("logs.description")}</p></div><span className="freshness"><span aria-hidden="true">●</span> {t("logs.bounded")}</span></div>
    {sources.some((source) => !source.available) && <p className="session-message" role="status">{t("logs.sourceUnavailable", { count: sources.filter((source) => !source.available).length })}</p>}
    <form className="log-filters panel" aria-label={t("logs.filters")} onSubmit={submit}>
      <label>{t("logs.from")}<input name="from_time" type="datetime-local" defaultValue={utcInput(24)} required /></label>
      <label>{t("logs.to")}<input name="to_time" type="datetime-local" defaultValue={utcInput()} required /></label>
      <label>{t("logs.environment")}<input name="environment" maxLength={50} /></label>
      <label>{t("logs.serverId")}<input name="server_id" /></label>
      <label>{t("logs.level")}<select name="level"><option value="">{t("logs.any")}</option>{["DEBUG", "NOTICE", "INFO", "WARNING", "ERROR", "CRITICAL"].map((level) => <option key={level}>{level}</option>)}</select></label>
      <label>{t("logs.source")}<input name="source" maxLength={100} /></label>
      <label>{t("logs.module")}<input name="module" maxLength={255} /></label>
      <label>{t("logs.endpoint")}<input name="endpoint" maxLength={255} /></label>
      <label>{t("logs.trunk")}<input name="trunk" maxLength={255} /></label>
      <label>{t("logs.uniqueId")}<input name="unique_id" maxLength={255} /></label>
      <label>{t("logs.linkedId")}<input name="linked_id" maxLength={255} /></label>
      <label>{t("logs.text")}<input name="q" maxLength={200} /></label>
      <button className="button--primary" disabled={loading}>{t(loading ? "logs.searching" : "logs.search")}</button>
    </form>
    {loading && !result ? <PageState kind="loading" title={t("logs.loading")} /> : error ? <PageState kind="error" title={t("logs.unavailable")} action={query && <button onClick={() => void load(query)}>{t("common.retry")}</button>}>{error}</PageState> : result?.items.length === 0 ? <PageState kind="empty" title={t("logs.empty")}>{t("logs.emptyBody")}</PageState> : result && <div className="panel log-results"><div className="table-wrap"><table><thead><tr><th>{t("logs.time")}</th><th>{t("logs.server")}</th><th>{t("logs.level")}</th><th>{t("logs.source")}</th><th>{t("logs.message")}</th></tr></thead><tbody>{result.items.map((item) => <tr key={item.id}><td>{new Date(item.occurred_at).toLocaleString()}</td><td><strong>{item.server_name}</strong><small>{item.environment}</small></td><td><span className={`log-level log-level--${item.level.toLowerCase()}`}>{item.level}</span></td><td>{item.source}<small>{item.module}</small></td><td><code className="log-message">{item.message}</code><small>{[item.endpoint, item.trunk, item.unique_id, item.linked_id].filter(Boolean).join(" · ")}</small></td></tr>)}</tbody></table></div><div className="log-pagination"><button className="button--quiet" disabled={result.offset === 0 || loading} onClick={() => page(result.offset - result.limit)}>{t("logs.previous")}</button><span>{t("logs.resultWindow", { start: result.offset + 1, end: result.offset + result.items.length })}</span><button className="button--quiet" disabled={!result.has_more || loading} onClick={() => page(result.offset + result.limit)}>{t("logs.next")}</button></div></div>}
  </section>;
}
