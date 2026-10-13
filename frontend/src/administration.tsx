import { useEffect, useState, type FormEvent } from "react";

import { ApiClient, type AlertRule, type AuditEvent, type RetentionPolicy } from "./api";
import { useI18n } from "./i18n";
import { FieldError, PageState } from "./ui";

const message = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

export function AuditPage({ api }: { api: ApiClient }) {
  const { t } = useI18n();
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState(new URLSearchParams());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  async function load(query = filters, nextOffset = offset) {
    setLoading(true); setError(undefined);
    const request = new URLSearchParams(query); request.set("offset", String(nextOffset));
    try { const page = await api.auditEvents(request); setItems(page.items); setTotal(page.total); setOffset(page.offset); }
    catch (next) { setError(message(next, t("audit.unavailableBody"))); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const fields = new FormData(event.currentTarget); const query = new URLSearchParams();
    for (const key of ["action", "target_type", "outcome"]) { const value = String(fields.get(key) ?? "").trim(); if (value) query.set(key, value); }
    setFilters(query); setOffset(0); void load(query, 0);
  }
  return <section aria-labelledby="audit-title"><div className="page-heading"><div><p className="eyebrow">{t("audit.eyebrow")}</p><h2 id="audit-title">{t("audit.title")}</h2><p>{t("audit.description")}</p></div><span className="freshness freshness--fresh"><span aria-hidden="true">●</span> {t("audit.appendOnly")}</span></div>
    <form className="panel audit-filters" aria-label={t("audit.filters")} onSubmit={filter}><label>{t("audit.action")}<input name="action" maxLength={100} /></label><label>{t("audit.targetType")}<input name="target_type" maxLength={50} /></label><label>{t("audit.outcome")}<select name="outcome" defaultValue=""><option value="">{t("audit.anyOutcome")}</option><option value="success">{t("audit.success")}</option><option value="failure">{t("audit.failure")}</option></select></label><button className="button--primary">{t("audit.applyFilters")}</button></form>
    {loading ? <PageState kind="loading" title={t("audit.loading")} /> : error ? <PageState kind="error" title={t("audit.unavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState> : items.length === 0 ? <PageState kind="empty" title={t("audit.empty")}>{t("audit.emptyBody")}</PageState> : <div className="panel table-wrap"><table><caption>{t("audit.caption", { count: total })}</caption><thead><tr><th>{t("audit.time")}</th><th>{t("audit.action")}</th><th>{t("audit.actor")}</th><th>{t("audit.target")}</th><th>{t("audit.outcome")}</th><th>{t("audit.source")}</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{new Date(item.occurred_at).toLocaleString()}</td><td><code>{item.action}</code></td><td><code>{item.actor_user_id ?? t("audit.system")}</code></td><td>{item.target_type}<small>{item.target_id}</small></td><td><span className={`signal ${item.outcome === "success" ? "signal--ok" : "signal--danger"}`}>{t(item.outcome === "success" ? "audit.success" : "audit.failure")}</span></td><td>{item.source}</td></tr>)}</tbody></table><div className="log-pagination"><button className="button--quiet" disabled={offset === 0} onClick={() => void load(filters, Math.max(0, offset - 100))}>{t("logs.previous")}</button><span>{t("logs.resultWindow", { start: offset + 1, end: Math.min(total, offset + items.length) })}</span><button className="button--quiet" disabled={offset + items.length >= total} onClick={() => void load(filters, offset + 100)}>{t("logs.next")}</button></div></div>}
  </section>;
}

function RuleForm({ api, rule, onSaved }: { api: ApiClient; rule: AlertRule; onSaved: (value: AlertRule) => void }) {
  const { t } = useI18n(); const numeric = rule.condition_kind.startsWith("numeric_");
  const thresholdMaximum = rule.code === "recording.generation.failures" ? 10000 : 100;
  const [error, setError] = useState<string>(); const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError(undefined); const fields = new FormData(event.currentTarget);
    try { onSaved(await api.updateAlertRule(rule.id, { severity: String(fields.get("severity")) as AlertRule["severity"], threshold: numeric ? Number(fields.get("threshold")) : null, clear_threshold: numeric ? Number(fields.get("clear_threshold")) : null, duration_seconds: Number(fields.get("duration_seconds")), debounce_seconds: Number(fields.get("debounce_seconds")), enabled: fields.get("enabled") === "on" })); }
    catch (next) { setError(message(next, t("settings.saveFailed"))); }
    finally { setSaving(false); }
  }
  return <form className="panel settings-card" onSubmit={(event) => void submit(event)}><h3><code>{rule.code}</code></h3><FieldError>{error}</FieldError><div className="settings-grid"><label>{t("settings.severity")}<select name="severity" defaultValue={rule.severity}><option>INFO</option><option>WARNING</option><option>CRITICAL</option></select></label>{numeric && <><label>{t("settings.threshold")}<input name="threshold" type="number" min="0" max={thresholdMaximum} step="any" required defaultValue={rule.threshold ?? ""} /></label><label>{t("settings.clearThreshold")}<input name="clear_threshold" type="number" min="0" max={thresholdMaximum} step="any" required defaultValue={rule.clear_threshold ?? ""} /></label></>}<label>{t("settings.duration")}<input name="duration_seconds" type="number" min="0" max="86400" required defaultValue={rule.duration_seconds} /></label><label>{t("settings.debounce")}<input name="debounce_seconds" type="number" min="0" max="86400" required defaultValue={rule.debounce_seconds} /></label></div><label className="check"><input name="enabled" type="checkbox" defaultChecked={rule.enabled} />{t("settings.ruleEnabled")}</label><button className="button--primary" disabled={saving}>{t(saving ? "common.saving" : "settings.saveRule")}</button></form>;
}

export function MonitoringSettingsPage({ api }: { api: ApiClient }) {
  const { t } = useI18n(); const [rules, setRules] = useState<AlertRule[]>([]); const [policy, setPolicy] = useState<RetentionPolicy>();
  const [loading, setLoading] = useState(true); const [error, setError] = useState<string>(); const [notice, setNotice] = useState<string>();
  async function load() {
    setLoading(true); setError(undefined);
    try { const [nextRules, policies] = await Promise.all([api.alertRules(), api.retentionPolicies()]); setRules(nextRules); setPolicy(policies.find((item) => item.category === "operational_logs")); }
    catch (next) { setError(message(next, t("settings.unavailableBody"))); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  async function saveRetention(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const fields = new FormData(event.currentTarget); setError(undefined); setNotice(undefined);
    try { setPolicy(await api.updateRetentionPolicy("operational_logs", { retention_days: Number(fields.get("retention_days")), batch_size: Number(fields.get("batch_size")) })); setNotice(t("settings.retentionSaved")); }
    catch (next) { setError(message(next, t("settings.saveFailed"))); }
  }
  if (loading) return <PageState kind="loading" title={t("settings.loading")} />;
  if (error && rules.length === 0) return <PageState kind="error" title={t("settings.unavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState>;
  return <section aria-labelledby="settings-title"><div className="page-heading"><div><p className="eyebrow">{t("settings.eyebrow")}</p><h2 id="settings-title">{t("settings.title")}</h2><p>{t("settings.description")}</p></div></div><FieldError>{error}</FieldError>{notice && <p className="notice" role="status">{notice}</p>}<div className="section-heading"><div><h3>{t("settings.alertRules")}</h3><p>{t("settings.alertRulesBody")}</p></div></div>{rules.length === 0 ? <PageState kind="empty" title={t("settings.noRules")} /> : <div className="settings-list">{rules.map((rule) => <RuleForm key={`${rule.id}-${JSON.stringify(rule)}`} api={api} rule={rule} onSaved={(saved) => setRules((current) => current.map((item) => item.id === saved.id ? saved : item))} />)}</div>}<div className="section-heading settings-section"><div><h3>{t("settings.retention")}</h3><p>{t("settings.retentionBody")}</p></div></div>{policy ? <form className="panel settings-card" onSubmit={(event) => void saveRetention(event)}><h3>{t("settings.operationalLogs")}</h3><div className="settings-grid"><label>{t("settings.retentionDays")}<input name="retention_days" type="number" min="1" max="3650" required defaultValue={policy.retention_days} /></label><label>{t("settings.batchSize")}<input name="batch_size" type="number" min="1" max="10000" required defaultValue={policy.batch_size} /></label></div><p className="form-hint form-hint--flush">{t("settings.retentionSafety")}</p><button className="button--primary">{t("settings.saveRetention")}</button></form> : <PageState kind="empty" title={t("settings.noRetention")} />}</section>;
}
