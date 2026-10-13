import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiClient, ApiError, type CallRealtimeMessage, type ServerCalls } from "./api";
import { useI18n, type TranslationKey } from "./i18n";
import { PageState } from "./ui";

export function LiveCallsPage({ api }: { api: ApiClient }) {
  const { locale, t } = useI18n();
  const [servers, setServers] = useState<ServerCalls[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [connection, setConnection] = useState<"connecting" | "connected" | "disconnected">("connecting");

  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    try { setServers((await api.liveCalls()).servers); }
    catch (nextError) {
      setError(nextError instanceof ApiError && nextError.messageKey ? t(nextError.messageKey) : t("calls.unavailableBody"));
    } finally { setLoading(false); }
  }, [api, t]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => api.subscribeLiveCalls((message: CallRealtimeMessage) => {
    if (message.type === "calls.initial") { setServers(message.payload.servers); return; }
    setServers((current) => {
      const next = current.filter((item) => item.server_id !== message.server_id);
      next.push(message.payload);
      return next.sort((left, right) => left.server_id.localeCompare(right.server_id));
    });
  }, setConnection), [api]);

  const calls = useMemo(() => servers.flatMap((server) => server.calls), [servers]);
  const freshness = connection === "disconnected"
    ? "disconnected"
    : servers.some((server) => server.freshness === "stale")
      ? "stale"
      : servers.length > 0 && servers.every((server) => server.freshness === "fresh")
        ? "fresh"
        : "unknown";
  const freshnessKey: Record<typeof freshness, TranslationKey> = {
    disconnected: "calls.freshness.disconnected",
    fresh: "calls.freshness.fresh",
    stale: "calls.freshness.stale",
    unknown: "calls.freshness.unknown",
  };

  return <section aria-labelledby="calls-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.asterforgeV1")}</p><h2 id="calls-title">{t("calls.title")}</h2><p>{t("calls.description")}</p></div>
      <span className={`freshness freshness--${freshness}`}><span aria-hidden="true">●</span> {t(freshnessKey[freshness])}</span>
    </div>
    {loading && <PageState kind="loading" title={t("calls.loading")} />}
    {!loading && error && <PageState kind="error" title={t("calls.unavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState>}
    {!loading && !error && calls.length === 0 && <PageState kind="empty" title={t("calls.empty")}>{t("calls.emptyBody")}</PageState>}
    {!loading && !error && calls.length > 0 && <div className="panel table-wrap"><table><thead><tr>
      <th>{t("calls.logicalId")}</th><th>{t("calls.server")}</th><th>{t("calls.parties")}</th><th>{t("calls.state")}</th><th>{t("calls.channels")}</th><th>{t("calls.started")}</th>
    </tr></thead><tbody>{calls.map((call) => <tr key={`${call.server_id}:${call.logical_id}`}>
      <td><code>{call.logical_id}</code></td><td><code>{call.server_id}</code></td><td>{call.caller_number ?? t("calls.unknownParty")} → {call.connected_number ?? t("calls.unknownParty")}</td><td><span className={`badge ${call.freshness === "stale" ? "badge--muted" : "badge--ok"}`}>{call.state}</span></td><td>{call.channel_count}</td><td>{new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "medium" }).format(new Date(call.started_at))}</td>
    </tr>)}</tbody></table></div>}
    <p className="read-only-inline">{t("calls.readOnly")}</p>
  </section>;
}
