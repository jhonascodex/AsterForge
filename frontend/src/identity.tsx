import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiClient, ApiError, type Permission, type Profile, type User } from "./api";
import { useI18n, type Translate } from "./i18n";
import { FieldError, PageState, StatusBadge } from "./ui";

function messageFrom(error: unknown, t: Translate) {
  return error instanceof ApiError && error.messageKey ? t(error.messageKey) : error instanceof ApiError ? error.message : t("error.apiUnreachable");
}

function useIdentityData(api: ApiClient, includeUsers = false, includeProfiles = true, includePermissions = true) {
  const { t } = useI18n();
  const [profiles, setProfiles] = useState<Profile[]>([]); const [permissions, setPermissions] = useState<Permission[]>([]);
  const [users, setUsers] = useState<User[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState<string>();
  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    try {
      const [nextProfiles, nextPermissions, nextUsers] = await Promise.all([
        includeProfiles ? api.profiles() : Promise.resolve([]), includePermissions ? api.permissions() : Promise.resolve([]), includeUsers ? api.users() : Promise.resolve([]),
      ]);
      setProfiles(nextProfiles); setPermissions(nextPermissions); setUsers(nextUsers);
    } catch (nextError) { setError(messageFrom(nextError, t)); }
    finally { setLoading(false); }
  }, [api, includePermissions, includeProfiles, includeUsers, t]);
  useEffect(() => { void load(); }, [load]);
  return { profiles, permissions, users, loading, error, reload: load };
}

export function UsersPage({ api, currentUserId, canManageProfiles }: { api: ApiClient; currentUserId: string; canManageProfiles: boolean }) {
  const { t } = useI18n(); const data = useIdentityData(api, true, canManageProfiles, false);
  const [notice, setNotice] = useState<string>(); const [formError, setFormError] = useState<string>();
  const [busy, setBusy] = useState(false); const [selected, setSelected] = useState<User>();

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; setBusy(true); setFormError(undefined); setNotice(undefined); const fields = new FormData(form);
    try {
      const display_name = String(fields.get("display_name")); const profile_ids = fields.getAll("profile_ids").map(String);
      if (selected) await api.updateUser(selected.id, selected.id === currentUserId || !canManageProfiles ? { display_name } : { display_name, profile_ids });
      else await api.createUser({ username: String(fields.get("username")), display_name, password: String(fields.get("password")), profile_ids });
      form.reset(); setNotice(t(selected ? "users.updated" : "users.created")); setSelected(undefined); await data.reload();
    } catch (error) { setFormError(messageFrom(error, t)); }
    finally { setBusy(false); }
  }

  async function toggle(user: User) {
    setFormError(undefined);
    try { await api.updateUser(user.id, { enabled: !user.enabled }); setNotice(t("users.toggled", { username: user.username, state: t(user.enabled ? "common.disabled" : "common.enabled").toLocaleLowerCase() })); await data.reload(); }
    catch (error) { setFormError(messageFrom(error, t)); }
  }

  async function reset(user: User) {
    const password = window.prompt(t("users.resetPrompt", { username: user.username })); if (password === null) return;
    if (password.length < 12) { setFormError(t("users.passwordLength")); return; }
    try { await api.resetPassword(user.id, password); setNotice(t("users.passwordReset", { username: user.username })); }
    catch (error) { setFormError(messageFrom(error, t)); }
  }

  return <section aria-labelledby="users-title">
    <div className="page-heading"><div><p className="eyebrow">{t("brand.identity")}</p><h2 id="users-title">{t("users.title")}</h2><p>{t("users.description")}</p></div></div>
    <FieldError>{formError}</FieldError>{notice && <p className="notice" role="status">✓ {notice}</p>}
    <div className="two-column"><div className="panel"><h3>{t("users.directory")}</h3>
      {data.loading ? <PageState kind="loading" title={t("users.loading")} /> : data.error ? <PageState kind="error" title={t("users.unavailable")} action={<button onClick={() => void data.reload()}>{t("common.retry")}</button>}>{data.error}</PageState> : data.users.length === 0 ? <PageState kind="empty" title={t("users.empty")}>{t("users.emptyBody")}</PageState> :
        <div className="table-wrap"><table><thead><tr><th>{t("users.user")}</th><th>{t("users.profiles")}</th><th>{t("users.status")}</th><th><span className="sr-only">{t("common.actions")}</span></th></tr></thead><tbody>{data.users.map((user) => <tr key={user.id}><td><strong>{user.display_name}</strong><small>@{user.username}</small></td><td>{user.profile_ids.length}</td><td><StatusBadge enabled={user.enabled} /></td><td className="row-actions"><button className="button--quiet" onClick={() => setSelected(user)}>{t("common.edit")}</button><button className="button--quiet" disabled={user.id === currentUserId} onClick={() => void toggle(user)}>{t(user.enabled ? "common.disable" : "common.enable")}</button><button className="button--quiet" onClick={() => void reset(user)}>{t("users.resetPassword")}</button></td></tr>)}</tbody></table></div>}
    </div>
    <form className="panel form-panel" key={selected?.id ?? "new"} onSubmit={(event) => void save(event)}><div className="form-title"><h3>{t(selected ? "users.edit" : "users.create")}</h3>{selected && <button type="button" className="button--quiet" onClick={() => setSelected(undefined)}>{t("common.cancel")}</button>}</div>
      <label>{t("login.username")}<input name="username" required={!selected} disabled={Boolean(selected)} minLength={3} maxLength={100} pattern="[A-Za-z0-9_.-]+" autoComplete="off" defaultValue={selected?.username} /></label>
      <label>{t("users.displayName")}<input name="display_name" required maxLength={150} autoComplete="off" defaultValue={selected?.display_name} /></label>
      {!selected && <label>{t("users.temporaryPassword")}<input name="password" required minLength={12} maxLength={256} type="password" autoComplete="new-password" /></label>}
      {canManageProfiles ? <fieldset disabled={selected?.id === currentUserId}><legend>{t("users.profiles")}</legend>{data.profiles.map((profile) => <label className="check" key={profile.id}><input type="checkbox" name="profile_ids" value={profile.id} disabled={!profile.enabled} defaultChecked={selected?.profile_ids.includes(profile.id)} /> <span>{profile.name}<small>{profile.description}</small></span></label>)}</fieldset> : <p className="form-hint">{t("users.profileHint")}</p>}
      <button className="button--primary" disabled={busy || data.loading}>{t(busy ? "common.saving" : selected ? "users.save" : "users.create")}</button>
    </form></div>
  </section>;
}

export function ProfilesPage({ api }: { api: ApiClient }) {
  const { t } = useI18n(); const data = useIdentityData(api);
  const [selected, setSelected] = useState<Profile>(); const [error, setError] = useState<string>(); const [notice, setNotice] = useState<string>();
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(undefined); const form = event.currentTarget; const fields = new FormData(form);
    const payload = { name: String(fields.get("name")), description: String(fields.get("description")), permission_codes: fields.getAll("permission_codes").map(String) };
    try { if (selected) await api.updateProfile(selected.id, payload); else await api.createProfile(payload); setNotice(t(selected ? "profiles.updated" : "profiles.created")); setSelected(undefined); form.reset(); await data.reload(); }
    catch (nextError) { setError(messageFrom(nextError, t)); }
  }
  async function toggle(profile: Profile) { try { await api.updateProfile(profile.id, { enabled: !profile.enabled }); await data.reload(); } catch (nextError) { setError(messageFrom(nextError, t)); } }
  if (data.loading) return <PageState kind="loading" title={t("profiles.loading")} />;
  if (data.error) return <PageState kind="error" title={t("profiles.unavailable")} action={<button onClick={() => void data.reload()}>{t("common.retry")}</button>}>{data.error}</PageState>;
  return <section aria-labelledby="profiles-title"><div className="page-heading"><div><p className="eyebrow">{t("brand.identity")}</p><h2 id="profiles-title">{t("profiles.title")}</h2><p>{t("profiles.description")}</p></div></div>
    <FieldError>{error}</FieldError>{notice && <p className="notice" role="status">✓ {notice}</p>}
    <div className="two-column"><div className="panel"><h3>{t("profiles.available")}</h3>{data.profiles.length === 0 ? <PageState kind="empty" title={t("profiles.empty")} /> : <div className="card-list">{data.profiles.map((profile) => <article key={profile.id} className="profile-card"><div><strong>{profile.name}</strong><p>{profile.description || t("common.noDescription")}</p><StatusBadge enabled={profile.enabled} /> <span className="permission-count">{t("shell.permissionCount", { count: profile.permission_codes.length })}</span></div><div className="row-actions"><button className="button--quiet" disabled={profile.is_system} onClick={() => setSelected(profile)}>{t("common.edit")}</button><button className="button--quiet" disabled={profile.is_system} onClick={() => void toggle(profile)}>{t(profile.enabled ? "common.disable" : "common.enable")}</button></div></article>)}</div>}</div>
      <form className="panel form-panel" key={selected?.id ?? "new"} onSubmit={(event) => void save(event)}><div className="form-title"><h3>{t(selected ? "profiles.edit" : "profiles.create")}</h3>{selected && <button type="button" className="button--quiet" onClick={() => setSelected(undefined)}>{t("common.cancel")}</button>}</div>
        <label>{t("common.name")}<input name="name" required maxLength={100} defaultValue={selected?.name} pattern="[A-Za-z0-9][A-Za-z0-9 _.-]*" /></label>
        <label>{t("common.description")}<input name="description" maxLength={255} defaultValue={selected?.description} /></label>
        <fieldset><legend>{t("profiles.permissions")}</legend><div className="permission-grid">{data.permissions.map((permission) => <label className="check" key={permission.code}><input type="checkbox" name="permission_codes" value={permission.code} defaultChecked={selected?.permission_codes.includes(permission.code)} /><span>{permission.description}<small>{permission.code}</small></span></label>)}</div></fieldset>
        <button className="button--primary">{t(selected ? "profiles.save" : "profiles.create")}</button>
      </form></div>
  </section>;
}

export function AccessPage({ api }: { api: ApiClient }) {
  const { t } = useI18n(); const data = useIdentityData(api);
  if (data.loading) return <PageState kind="loading" title={t("access.loading")} />;
  if (data.error) return <PageState kind="error" title={t("access.unavailable")} action={<button onClick={() => void data.reload()}>{t("common.retry")}</button>}>{data.error}</PageState>;
  if (data.profiles.length === 0) return <PageState kind="empty" title={t("access.empty")}>{t("access.emptyBody")}</PageState>;
  return <section aria-labelledby="access-title"><div className="page-heading"><div><p className="eyebrow">{t("brand.identity")}</p><h2 id="access-title">{t("access.title")}</h2><p>{t("access.description")}</p></div></div><div className="panel table-wrap"><table className="matrix"><thead><tr><th>{t("access.permission")}</th>{data.profiles.map((profile) => <th key={profile.id}>{profile.name}<small>{t(profile.enabled ? "common.active" : "common.disabled")}</small></th>)}</tr></thead><tbody>{data.permissions.map((permission) => <tr key={permission.code}><td><strong>{permission.description}</strong><small>{permission.code}</small></td>{data.profiles.map((profile) => { const granted = profile.permission_codes.includes(permission.code) && profile.enabled; return <td key={profile.id} aria-label={t(granted ? "access.cellGranted" : "access.cellNotGranted", { profile: profile.name })}>{granted ? <span className="grant">✓ <span className="sr-only">{t("access.granted")}</span></span> : <span className="no-grant">— <span className="sr-only">{t("access.notGranted")}</span></span>}</td>; })}</tr>)}</tbody></table></div></section>;
}
