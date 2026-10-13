import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiClient, ApiError, type Environment, type Server, type ServerFilters,
  type ServerPayload, type ServerTag,
} from "./api";
import { useI18n, type Translate } from "./i18n";
import { FieldError, PageState, StatusBadge } from "./ui";

function messageFrom(error: unknown, t: Translate) {
  return error instanceof ApiError && error.messageKey
    ? t(error.messageKey)
    : error instanceof ApiError ? error.message : t("error.apiUnreachable");
}

export function InventoryPage({ api, canRead, canManage }: {
  api: ApiClient; canRead: boolean; canManage: boolean;
}) {
  const { t } = useI18n();
  const [servers, setServers] = useState<Server[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [tags, setTags] = useState<ServerTag[]>([]);
  const [filters, setFilters] = useState<ServerFilters>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [selected, setSelected] = useState<Server>();
  const [busy, setBusy] = useState(false);

  const loadServers = useCallback(async () => {
    if (!canRead) return;
    setLoading(true); setError(undefined);
    try { setServers(await api.allServers(filters)); }
    catch (nextError) { setError(messageFrom(nextError, t)); }
    finally { setLoading(false); }
  }, [api, canRead, filters, t]);

  const loadCatalogs = useCallback(async () => {
    if (!canRead) return;
    setCatalogLoading(true); setCatalogError(undefined);
    try {
      const [nextEnvironments, nextTags] = await Promise.all([
        api.environments(true), api.serverTags(),
      ]);
      setEnvironments(nextEnvironments); setTags(nextTags);
    } catch { setCatalogError(t("inventory.catalogsUnavailableBody")); }
    finally { setCatalogLoading(false); }
  }, [api, canRead, t]);

  useEffect(() => { void loadServers(); }, [loadServers]);
  useEffect(() => { void loadCatalogs(); }, [loadCatalogs]);

  if (!canRead) {
    return <PageState kind="unauthorized" title={t("inventory.unauthorized")}>
      {t("inventory.unauthorizedBody")}
    </PageState>;
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const state = String(fields.get("monitoring_state"));
    setFilters({
      search: String(fields.get("search")).trim() || undefined,
      environment: String(fields.get("environment")) || undefined,
      tag: String(fields.get("tag")) || undefined,
      monitoringEnabled: state === "enabled" ? true : state === "disabled" ? false : undefined,
      includeArchived: fields.get("include_archived") === "on",
    });
  }

  async function saveServer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const amiUsername = String(fields.get("ami_username")).trim();
    const amiSecret = String(fields.get("ami_secret"));
    const clearCredentials = fields.get("clear_ami_credentials") === "on";
    setFormError(undefined); setNotice(undefined);
    if ((amiUsername && !amiSecret) || (!amiUsername && amiSecret)) {
      setFormError(t("inventory.credentialsPair")); return;
    }
    const payload: ServerPayload = {
      name: String(fields.get("name")),
      hostname: String(fields.get("hostname")),
      environment_id: String(fields.get("environment_id")),
      location: String(fields.get("location")).trim() || null,
      ami_port: Number(fields.get("ami_port")),
      ami_tls: fields.get("ami_tls") === "on",
      monitoring_enabled: fields.get("monitoring_enabled") === "on",
      tag_ids: fields.getAll("tag_ids").map(String),
    };
    if (amiUsername && amiSecret) {
      payload.ami_username = amiUsername; payload.ami_secret = amiSecret;
    } else if (selected && clearCredentials) payload.clear_ami_credentials = true;

    setBusy(true);
    try {
      if (selected) await api.updateServer(selected.id, payload);
      else await api.createServer(payload);
      setNotice(t(selected ? "inventory.updated" : "inventory.created"));
      setSelected(undefined); form.reset(); await loadServers();
    } catch (nextError) { setFormError(messageFrom(nextError, t)); }
    finally { setBusy(false); }
  }

  async function toggleServer(server: Server) {
    setFormError(undefined); setNotice(undefined); setBusy(true);
    try {
      await api.updateServer(server.id, { monitoring_enabled: !server.monitoring_enabled });
      setNotice(t(server.monitoring_enabled ? "inventory.disabledNotice" : "inventory.enabledNotice"));
      await loadServers();
    } catch (nextError) { setFormError(messageFrom(nextError, t)); }
    finally { setBusy(false); }
  }

  async function archiveServer(server: Server) {
    if (!window.confirm(t("inventory.archiveConfirm", { name: server.name }))) return;
    setFormError(undefined); setNotice(undefined); setBusy(true);
    try {
      await api.archiveServer(server.id); setSelected(undefined);
      setNotice(t("inventory.archivedNotice")); await loadServers();
    } catch (nextError) { setFormError(messageFrom(nextError, t)); }
    finally { setBusy(false); }
  }

  const enabledEnvironmentCount = environments.filter((item) => item.enabled).length;

  return <section aria-labelledby="inventory-title">
    <div className="page-heading"><div><p className="eyebrow">{t("inventory.eyebrow")}</p>
      <h2 id="inventory-title">{t("inventory.title")}</h2><p>{t("inventory.description")}</p>
    </div><span className="freshness">{t("inventory.explicitOnboarding")}</span></div>

    {catalogLoading && <PageState kind="loading" title={t("inventory.catalogsLoading")} />}
    {catalogError && <PageState kind="error" title={t("inventory.catalogsUnavailable")} action={<button onClick={() => void loadCatalogs()}>{t("common.retry")}</button>}>{catalogError}</PageState>}

    <form className="panel inventory-filters" aria-label={t("inventory.filters")} onSubmit={applyFilters}>
      <label>{t("inventory.search")}<input name="search" type="search" /></label>
      <label>{t("inventory.environment")}<select name="environment"><option value="">{t("inventory.all")}</option>
        {environments.map((item) => <option key={item.id} value={item.code}>{item.code} · {item.display_name}</option>)}
      </select></label>
      <label>{t("inventory.tag")}<select name="tag"><option value="">{t("inventory.all")}</option>
        {tags.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}
      </select></label>
      <label>{t("inventory.monitoring")}<select name="monitoring_state"><option value="">{t("inventory.all")}</option>
        <option value="enabled">{t("common.enabled")}</option><option value="disabled">{t("common.disabled")}</option>
      </select></label>
      <label className="check check--inline"><input name="include_archived" type="checkbox" /> <span>{t("inventory.includeArchived")}</span></label>
      <button className="button--primary">{t("inventory.applyFilters")}</button>
    </form>
    <FieldError>{formError}</FieldError>{notice && <p className="notice" role="status">✓ {notice}</p>}
    <div className={canManage ? "two-column inventory-layout" : ""}><div className="panel"><h3>{t("inventory.directory")}</h3>
      {loading ? <PageState kind="loading" title={t("inventory.loading")} /> :
        error ? <PageState kind="error" title={t("inventory.unavailable")} action={<button onClick={() => void loadServers()}>{t("common.retry")}</button>}>{error}</PageState> :
        servers.length === 0 ? <PageState kind="empty" title={t("inventory.empty")}>{t("inventory.emptyBody")}</PageState> :
        <div className="table-wrap"><table><caption className="sr-only">{t("inventory.directory")}</caption><thead><tr>
          <th>{t("inventory.server")}</th><th>{t("inventory.environment")}</th><th>{t("inventory.monitoring")}</th>
          <th>{t("inventory.credentials")}</th><th><span className="sr-only">{t("common.actions")}</span></th>
        </tr></thead><tbody>{servers.map((server) => <tr key={server.id}>
          <td><strong>{server.name}</strong><small>{server.hostname}:{server.ami_port} · {server.location || t("inventory.noLocation")}</small>
            {server.tags.length > 0 && <small>{server.tags.map((tag) => tag.name).join(", ")}</small>}</td>
          <td>{server.environment.code}<small>{server.environment.display_name}</small></td>
          <td>{server.archived_at ? <span className="badge badge--muted">{t("inventory.archived")}</span> : <StatusBadge enabled={server.monitoring_enabled} />}</td>
          <td><span className={"badge " + (server.ami_username_configured && server.ami_secret_configured ? "badge--ok" : "badge--muted")}>
            {t(server.ami_username_configured && server.ami_secret_configured ? "inventory.configured" : "inventory.notConfigured")}
          </span><small>{server.ami_tls ? "AMI TLS" : "AMI TCP"}</small></td>
          <td className="row-actions">{canManage && !server.archived_at && <>
            <button type="button" className="button--quiet" onClick={() => setSelected(server)}>{t("common.edit")}</button>
            <button type="button" className="button--quiet" disabled={busy} onClick={() => void toggleServer(server)}>{t(server.monitoring_enabled ? "common.disable" : "common.enable")}</button>
            <button type="button" className="button--quiet button--danger" disabled={busy} onClick={() => void archiveServer(server)}>{t("inventory.archive")}</button>
          </>}</td>
        </tr>)}</tbody></table></div>}
    </div>{canManage && <form className="panel form-panel" key={selected?.id ?? "new"} onSubmit={(event) => void saveServer(event)}>
      <div className="form-title"><h3>{t(selected ? "inventory.edit" : "inventory.create")}</h3>{selected &&
        <button type="button" className="button--quiet" onClick={() => setSelected(undefined)}>{t("common.cancel")}</button>}</div>
      <label>{t("common.name")}<input name="name" required maxLength={100} defaultValue={selected?.name} /></label>
      <label>{t("inventory.hostname")}<input name="hostname" required maxLength={253} defaultValue={selected?.hostname} /></label>
      <label>{t("inventory.environment")}<select name="environment_id" required defaultValue={selected?.environment.id ?? ""} disabled={catalogLoading || Boolean(catalogError)}>
        <option value="" disabled>{t("inventory.chooseEnvironment")}</option>{environments.map((item) =>
          <option key={item.id} value={item.id} disabled={!item.enabled && item.id !== selected?.environment.id}>{item.code} · {item.display_name}</option>)}
      </select></label>
      {enabledEnvironmentCount === 0 && !catalogLoading && !catalogError && <p className="form-hint">{t("inventory.noEnabledEnvironments")}</p>}
      <label>{t("inventory.location")}<input name="location" maxLength={150} defaultValue={selected?.location ?? ""} /></label>
      <label>{t("inventory.amiPort")}<input name="ami_port" type="number" min={1} max={65535} required defaultValue={selected?.ami_port ?? 5038} /></label>
      <label className="check check--inline"><input name="ami_tls" type="checkbox" defaultChecked={selected?.ami_tls} /> <span>{t("inventory.amiTls")}</span></label>
      <label className="check check--inline"><input name="monitoring_enabled" type="checkbox" defaultChecked={selected?.monitoring_enabled ?? true} /> <span>{t("inventory.monitoringEnabled")}</span></label>
      {tags.length > 0 ? <fieldset><legend>{t("inventory.tags")}</legend><div className="permission-grid">{tags.map((tag) =>
        <label className="check" key={tag.id}><input name="tag_ids" type="checkbox" value={tag.id} defaultChecked={selected?.tags.some((item) => item.id === tag.id)} /><span>{tag.name}</span></label>)}</div></fieldset> :
        <div className="form-empty-state" role="note"><h4>{t("inventory.tags")}</h4><p>{t(catalogLoading ? "inventory.tagsLoading" : catalogError ? "inventory.tagsUnavailable" : "inventory.serverTagsEmpty")}</p></div>}
      <fieldset><legend>{t("inventory.amiCredentials")}</legend><p className="form-hint">{selected && selected.ami_username_configured ? t("inventory.credentialsConfiguredHint") : t("inventory.credentialsHint")}</p>
        <label>{t("inventory.amiUsername")}<input name="ami_username" type="password" maxLength={200} autoComplete="new-password" /></label>
        <label>{t("inventory.amiSecret")}<input name="ami_secret" type="password" maxLength={1024} autoComplete="new-password" /></label>
        {selected?.ami_username_configured && <label className="check"><input name="clear_ami_credentials" type="checkbox" /><span>{t("inventory.clearCredentials")}</span></label>}
      </fieldset>
      <button className="button--primary" disabled={busy || loading || catalogLoading || Boolean(catalogError) || enabledEnvironmentCount === 0}>{t(busy ? "common.saving" : selected ? "inventory.save" : "inventory.create")}</button>
    </form>}</div>
  </section>;
}
