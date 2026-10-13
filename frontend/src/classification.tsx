import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import {
  ApiClient,
  ApiError,
  type EnvironmentCatalogEntry,
  type ServerTagCatalogEntry,
} from "./api";
import { useI18n, type Translate } from "./i18n";
import { FieldError, PageState, StatusBadge } from "./ui";

function catalogMessage(error: unknown, t: Translate) {
  if (error instanceof ApiError && error.status === 409) return t("inventory.catalogConflict");
  if (error instanceof ApiError && error.status === 403) {
    return t("inventory.catalogManageUnauthorizedBody");
  }
  return error instanceof ApiError && error.messageKey
    ? t(error.messageKey)
    : t("inventory.catalogChangeFailed");
}

function ManageDenied() {
  const { t } = useI18n();
  return <PageState kind="unauthorized" title={t("inventory.catalogManageUnauthorized")}>
    {t("inventory.catalogManageUnauthorizedBody")}
  </PageState>;
}

export function EnvironmentsPage({ api, canRead, canManage }: {
  api: ApiClient; canRead: boolean; canManage: boolean;
}) {
  const { t } = useI18n();
  const [items, setItems] = useState<EnvironmentCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [selected, setSelected] = useState<EnvironmentCatalogEntry>();
  const [busy, setBusy] = useState(false);
  const codeInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!canRead) return;
    setLoading(true); setError(undefined);
    try { setItems(await api.environments(true)); }
    catch { setError(t("inventory.environmentsUnavailableBody")); }
    finally { setLoading(false); }
  }, [api, canRead, t]);

  useEffect(() => { void load(); }, [load]);

  if (!canRead) return <PageState kind="unauthorized" title={t("inventory.environmentsUnauthorized")}>
    {t("inventory.unauthorizedBody")}
  </PageState>;

  function beginCreate() {
    setSelected(undefined); setFormError(undefined); setNotice(undefined);
    window.requestAnimationFrame(() => codeInput.current?.focus());
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setBusy(true); setFormError(undefined); setNotice(undefined);
    try {
      if (selected) {
        await api.updateEnvironment(selected.id, {
          display_name: String(fields.get("display_name")),
          enabled: fields.get("enabled") === "on",
        });
      } else {
        await api.createEnvironment({
          code: String(fields.get("code")),
          display_name: String(fields.get("display_name")),
        });
      }
      setNotice(t(selected ? "inventory.environmentUpdated" : "inventory.environmentCreated"));
      setSelected(undefined); form.reset(); await load();
    } catch (nextError) { setFormError(catalogMessage(nextError, t)); }
    finally { setBusy(false); }
  }

  async function toggle(item: EnvironmentCatalogEntry) {
    setBusy(true); setFormError(undefined); setNotice(undefined);
    try {
      await api.updateEnvironment(item.id, { enabled: !item.enabled });
      setNotice(t(item.enabled ? "inventory.environmentDisabled" : "inventory.environmentEnabled"));
      if (selected?.id === item.id) setSelected(undefined);
      await load();
    } catch (nextError) { setFormError(catalogMessage(nextError, t)); }
    finally { setBusy(false); }
  }

  async function remove(item: EnvironmentCatalogEntry) {
    if (!window.confirm(t("inventory.deleteEnvironmentConfirm", { code: item.code }))) return;
    setBusy(true); setFormError(undefined); setNotice(undefined);
    try {
      await api.deleteEnvironment(item.id);
      setNotice(t("inventory.environmentDeleted"));
      if (selected?.id === item.id) setSelected(undefined);
      await load();
    } catch (nextError) { setFormError(catalogMessage(nextError, t)); }
    finally { setBusy(false); }
  }

  return <section aria-labelledby="environments-title">
    <div className="page-heading"><div><p className="eyebrow">{t("inventory.classificationEyebrow")}</p>
      <h2 id="environments-title">{t("inventory.environments")}</h2>
      <p>{t("inventory.environmentsDescription")}</p></div>
      <span className="freshness">{t("inventory.reusableCatalog")}</span></div>
    <FieldError>{formError}</FieldError>{notice && <p className="notice" role="status">✓ {notice}</p>}
    <div className="two-column catalog-layout"><div className="panel catalog-panel"><h3>{t("inventory.environmentsDirectory")}</h3>
      {loading ? <PageState kind="loading" title={t("inventory.environmentsLoading")} /> :
        error ? <PageState kind="error" title={t("inventory.environmentsUnavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState> :
        items.length === 0 ? <PageState kind="empty" title={t("inventory.environmentsEmpty")} action={canManage ? <button onClick={beginCreate}>{t("inventory.createEnvironment")}</button> : undefined}>{t(canManage ? "inventory.environmentsEmptyManageBody" : "inventory.environmentsEmptyReadOnlyBody")}</PageState> :
        <div className="table-wrap"><table><caption className="sr-only">{t("inventory.environmentsDirectory")}</caption><thead><tr>
          <th>{t("inventory.environmentCode")}</th><th>{t("inventory.environmentName")}</th>
          <th>{t("common.status")}</th><th>{t("inventory.associatedServers")}</th>
          <th><span className="sr-only">{t("common.actions")}</span></th>
        </tr></thead><tbody>{items.map((item) => <tr key={item.id}>
          <td><strong>{item.code}</strong></td><td>{item.display_name}</td><td><StatusBadge enabled={item.enabled} /></td>
          <td aria-label={t("inventory.associatedServerCount", { count: item.server_count })}>{item.server_count}</td>
          <td className="row-actions">{canManage && <><button className="button--quiet" onClick={() => setSelected(item)}>{t("common.edit")}</button>
            <button className="button--quiet" disabled={busy} onClick={() => void toggle(item)}>{t(item.enabled ? "common.disable" : "common.enable")}</button>
            <button className="button--quiet button--danger" disabled={busy} onClick={() => void remove(item)}>{t("common.delete")}</button></>}</td>
        </tr>)}</tbody></table></div>}
    </div>{canManage ? <form className="panel form-panel" key={selected?.id ?? "new"} aria-label={t(selected ? "inventory.editEnvironment" : "inventory.createEnvironment")} onSubmit={(event) => void save(event)}>
      <div className="form-title"><h3>{t(selected ? "inventory.editEnvironment" : "inventory.createEnvironment")}</h3>{selected ? <button type="button" className="button--quiet" onClick={beginCreate}>{t("common.cancel")}</button> : null}</div>
      <label>{t("inventory.environmentCode")}<input ref={codeInput} name="code" required maxLength={50} pattern="[A-Za-z0-9][A-Za-z0-9_-]*" defaultValue={selected?.code} disabled={Boolean(selected)} /></label>
      {selected && <p className="form-hint">{t("inventory.environmentCodeImmutable")}</p>}
      <label>{t("inventory.environmentName")}<input name="display_name" required maxLength={100} defaultValue={selected?.display_name} /></label>
      {selected && <label className="check check--inline"><input name="enabled" type="checkbox" defaultChecked={selected.enabled} /><span>{t("common.enabled")}</span></label>}
      <button className="button--primary" disabled={busy || loading || Boolean(error)}>{t(busy ? "common.saving" : selected ? "inventory.saveEnvironment" : "inventory.createEnvironment")}</button>
    </form> : <ManageDenied />}</div>
  </section>;
}

export function TagsPage({ api, canRead, canManage }: {
  api: ApiClient; canRead: boolean; canManage: boolean;
}) {
  const { t } = useI18n();
  const [items, setItems] = useState<ServerTagCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [selected, setSelected] = useState<ServerTagCatalogEntry>();
  const [busy, setBusy] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!canRead) return;
    setLoading(true); setError(undefined);
    try { setItems(await api.serverTags()); }
    catch { setError(t("inventory.tagsUnavailableBody")); }
    finally { setLoading(false); }
  }, [api, canRead, t]);

  useEffect(() => { void load(); }, [load]);

  if (!canRead) return <PageState kind="unauthorized" title={t("inventory.tagsUnauthorized")}>
    {t("inventory.unauthorizedBody")}
  </PageState>;

  function beginCreate() {
    setSelected(undefined); setFormError(undefined); setNotice(undefined);
    window.requestAnimationFrame(() => nameInput.current?.focus());
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = { name: String(new FormData(form).get("name")) };
    setBusy(true); setFormError(undefined); setNotice(undefined);
    try {
      if (selected) await api.updateServerTag(selected.id, payload);
      else await api.createServerTag(payload);
      setNotice(t(selected ? "inventory.tagUpdated" : "inventory.tagCreated"));
      setSelected(undefined); form.reset(); await load();
    } catch (nextError) { setFormError(catalogMessage(nextError, t)); }
    finally { setBusy(false); }
  }

  async function remove(item: ServerTagCatalogEntry) {
    if (!window.confirm(t("inventory.deleteTagConfirm", { name: item.name }))) return;
    setBusy(true); setFormError(undefined); setNotice(undefined);
    try {
      await api.deleteServerTag(item.id);
      setNotice(t("inventory.tagDeleted"));
      if (selected?.id === item.id) setSelected(undefined);
      await load();
    } catch (nextError) { setFormError(catalogMessage(nextError, t)); }
    finally { setBusy(false); }
  }

  return <section aria-labelledby="tags-title">
    <div className="page-heading"><div><p className="eyebrow">{t("inventory.classificationEyebrow")}</p>
      <h2 id="tags-title">{t("inventory.tags")}</h2><p>{t("inventory.tagsDescription")}</p></div>
      <span className="freshness">{t("inventory.reusableCatalog")}</span></div>
    <FieldError>{formError}</FieldError>{notice && <p className="notice" role="status">✓ {notice}</p>}
    <div className="two-column catalog-layout"><div className="panel catalog-panel"><h3>{t("inventory.tagsDirectory")}</h3>
      {loading ? <PageState kind="loading" title={t("inventory.tagsCatalogLoading")} /> :
        error ? <PageState kind="error" title={t("inventory.tagsCatalogUnavailable")} action={<button onClick={() => void load()}>{t("common.retry")}</button>}>{error}</PageState> :
        items.length === 0 ? <PageState kind="empty" title={t("inventory.tagsEmpty")} action={canManage ? <button onClick={beginCreate}>{t("inventory.createFirstTag")}</button> : undefined}>{t(canManage ? "inventory.tagsEmptyManageBody" : "inventory.tagsEmptyReadOnlyBody")}</PageState> :
        <div className="table-wrap"><table><caption className="sr-only">{t("inventory.tagsDirectory")}</caption><thead><tr>
          <th>{t("inventory.tagName")}</th><th>{t("inventory.associatedServers")}</th>
          <th><span className="sr-only">{t("common.actions")}</span></th>
        </tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.name}</strong></td>
          <td aria-label={t("inventory.associatedServerCount", { count: item.server_count })}>{item.server_count}</td>
          <td className="row-actions">{canManage && <><button className="button--quiet" onClick={() => setSelected(item)}>{t("common.edit")}</button>
            <button className="button--quiet button--danger" disabled={busy} onClick={() => void remove(item)}>{t("common.delete")}</button></>}</td>
        </tr>)}</tbody></table></div>}
    </div>{canManage ? <form className="panel form-panel" key={selected?.id ?? "new"} aria-label={t(selected ? "inventory.editTag" : "inventory.createTag")} onSubmit={(event) => void save(event)}>
      <div className="form-title"><h3>{t(selected ? "inventory.editTag" : "inventory.createTag")}</h3>{selected ? <button type="button" className="button--quiet" onClick={beginCreate}>{t("common.cancel")}</button> : null}</div>
      <label>{t("inventory.tagName")}<input ref={nameInput} name="name" required maxLength={50} defaultValue={selected?.name} /></label>
      <button className="button--primary" disabled={busy || loading || Boolean(error)}>{t(busy ? "common.saving" : selected ? "inventory.saveTag" : "inventory.createTag")}</button>
    </form> : <ManageDenied />}</div>
  </section>;
}
