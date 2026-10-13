import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ApiClient, ApiError, login, restoreSession, type CurrentUser } from "./api";
import { AuditPage, MonitoringSettingsPage } from "./administration";
import { AlertsPage, IncidentsPage, MaintenancePage } from "./alerts";
import { CallHistoryPage } from "./call-history";
import { LiveCallsPage } from "./calls";
import { EnvironmentsPage, TagsPage } from "./classification";
import { DashboardPage, ServersPage, type InvestigationFilters } from "./dashboards";
import { I18nProvider, LanguageSelector, useI18n, type TranslationKey } from "./i18n";
import { AccessPage, ProfilesPage, UsersPage } from "./identity";
import { InventoryPage } from "./inventory";
import { LogsPage } from "./logs";
import { ReportsPage } from "./reporting";
import { FieldError, PageState } from "./ui";

type PageKey = "dashboard" | "servers" | "calls" | "history" |
  "logs" | "alerts" | "incidents" | "maintenance" | "reports" | "users" | "profiles" |
  "access" | "inventory" | "environments" | "tags" | "audit" | "settings";
type NavGroup = "monitor" | "investigate" | "respond" | "infrastructure" | "administration";

interface NavItem { key: PageKey; labelKey: TranslationKey; group: NavGroup; permission: string; descriptionKey: TranslationKey }

const navigation: NavItem[] = [
  { key: "dashboard", labelKey: "nav.dashboard", group: "monitor", permission: "dashboard.read", descriptionKey: "nav.dashboard.description" },
  { key: "servers", labelKey: "nav.servers", group: "monitor", permission: "server.read", descriptionKey: "nav.servers.description" },
  { key: "calls", labelKey: "nav.calls", group: "investigate", permission: "call.read", descriptionKey: "nav.calls.description" },
  { key: "history", labelKey: "nav.history", group: "investigate", permission: "call.read", descriptionKey: "nav.history.description" },
  { key: "logs", labelKey: "nav.logs", group: "investigate", permission: "log.read", descriptionKey: "nav.logs.description" },
  { key: "alerts", labelKey: "nav.alerts", group: "respond", permission: "alert.read", descriptionKey: "nav.alerts.description" },
  { key: "incidents", labelKey: "nav.incidents", group: "respond", permission: "incident.read", descriptionKey: "nav.incidents.description" },
  { key: "maintenance", labelKey: "nav.maintenance", group: "respond", permission: "maintenance.manage", descriptionKey: "nav.maintenance.description" },
  { key: "reports", labelKey: "nav.reports", group: "investigate", permission: "report.read", descriptionKey: "nav.reports.description" },
  { key: "inventory", labelKey: "nav.inventory", group: "infrastructure", permission: "server.read", descriptionKey: "nav.inventory.description" },
  { key: "environments", labelKey: "nav.environments", group: "infrastructure", permission: "server.read", descriptionKey: "nav.environments.description" },
  { key: "tags", labelKey: "nav.tags", group: "infrastructure", permission: "server.read", descriptionKey: "nav.tags.description" },
  { key: "users", labelKey: "nav.users", group: "administration", permission: "user.manage", descriptionKey: "nav.users.description" },
  { key: "profiles", labelKey: "nav.profiles", group: "administration", permission: "profile.manage", descriptionKey: "nav.profiles.description" },
  { key: "access", labelKey: "nav.access", group: "administration", permission: "profile.manage", descriptionKey: "nav.access.description" },
  { key: "audit", labelKey: "nav.audit", group: "administration", permission: "audit.read", descriptionKey: "nav.audit.description" },
  { key: "settings", labelKey: "nav.settings", group: "administration", permission: "monitoring_config.manage", descriptionKey: "nav.settings.description" },
];

function LoginScreen({ expired, onAuthenticated }: { expired: boolean; onAuthenticated: (token: string, expiresAt: string) => Promise<void> }) {
  const { t } = useI18n();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(undefined);
    const fields = new FormData(event.currentTarget);
    try {
      const result = await login(String(fields.get("username")), String(fields.get("password")));
      await onAuthenticated(result.access_token, result.expires_at);
    } catch (nextError) {
      setError(nextError instanceof ApiError && nextError.messageKey ? t(nextError.messageKey) : t("error.apiUnavailable"));
    } finally { setBusy(false); }
  }

  const [pitchFirst, pitchSecond] = t("login.pitchTitle").split("\n");
  return <main className="login-layout">
    <section className="login-brand" aria-labelledby="product-name">
      <div className="brand"><span className="brand-mark" aria-hidden="true">AF</span><div><p className="eyebrow">{t("brand.observability")}</p><h1 id="product-name">AsterForge</h1></div></div>
      <div className="login-pitch"><p className="eyebrow">{t("brand.v1")}</p><h2>{pitchFirst}<br />{pitchSecond}</h2><p>{t("login.pitchBody")}</p></div>
      <p className="scope-note"><span aria-hidden="true">◉</span> {t("login.scope")}</p>
    </section>
    <section className="login-panel" aria-labelledby="sign-in-title"><LanguageSelector className="language-selector--auth" /><form className="login-form" onSubmit={(event) => void submit(event)}>
      <p className="eyebrow">{t("brand.secureAccess")}</p><h2 id="sign-in-title">{t("login.title")}</h2><p>{t("login.instructions")}</p>
      {expired && <p className="session-message" role="alert">{t("login.sessionEnded")}</p>}<FieldError>{error}</FieldError>
      <label>{t("login.username")}<input name="username" required autoComplete="username" autoFocus /></label>
      <label>{t("login.password")}<input name="password" type="password" required autoComplete="current-password" /></label>
      <button className="button--primary button--large" disabled={busy}>{t(busy ? "login.busy" : "login.submit")}</button>
      <p className="security-note">{t("login.security")}</p>
    </form></section>
  </main>;
}

function PasswordChangeScreen({ username, onChangePassword, onLogout }: { username: string; onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>; onLogout: () => Promise<void> }) {
  const { t } = useI18n();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const currentPassword = String(fields.get("current_password"));
    const newPassword = String(fields.get("new_password"));
    const confirmation = String(fields.get("confirmation"));
    setError(undefined);
    if (newPassword !== confirmation) { setError(t("password.mismatch")); return; }
    if (currentPassword === newPassword) { setError(t("password.same")); return; }
    setBusy(true);
    try { await onChangePassword(currentPassword, newPassword); }
    catch (nextError) { setError(nextError instanceof ApiError && nextError.messageKey ? t(nextError.messageKey) : nextError instanceof ApiError ? nextError.message : t("error.apiUnavailable")); }
    finally { setBusy(false); }
  }

  return <main className="password-layout">
    <section className="password-context" aria-labelledby="password-change-title">
      <div className="brand"><span className="brand-mark" aria-hidden="true">AF</span><div><p className="eyebrow">{t("brand.observability")}</p><h1>AsterForge</h1></div></div>
      <div className="password-message"><p className="eyebrow">{t("brand.securityCheckpoint")}</p><h2 id="password-change-title">{t("password.title")}</h2><p>{t("password.intro")}</p><ul><li>{t("password.ruleLength")}</li><li>{t("password.ruleDifferent")}</li><li>{t("password.ruleRevoke")}</li></ul></div>
      <p className="scope-note"><span aria-hidden="true">◉</span> {t("password.scope")}</p>
    </section>
    <section className="login-panel" aria-label={t("password.formLabel")}><LanguageSelector className="language-selector--auth" /><form className="login-form" onSubmit={(event) => void submit(event)}>
      <p className="eyebrow">{t("brand.signedInAs")}</p><h2>{username}</h2><p className="password-required"><span aria-hidden="true">!</span> {t("password.required")}</p><FieldError>{error}</FieldError>
      <label>{t("password.temporary")}<input name="current_password" type="password" required maxLength={256} autoComplete="current-password" autoFocus /></label>
      <label>{t("password.new")}<input name="new_password" type="password" required minLength={12} maxLength={256} autoComplete="new-password" /></label>
      <label>{t("password.confirm")}<input name="confirmation" type="password" required minLength={12} maxLength={256} autoComplete="new-password" /></label>
      <button className="button--primary button--large" disabled={busy}>{t(busy ? "password.busy" : "password.submit")}</button>
      <button type="button" className="button--quiet button--logout" disabled={busy} onClick={() => void onLogout()}>{t("password.logout")}</button><p className="security-note">{t("password.security")}</p>
    </form></section>
  </main>;
}

function AppShell({ user, api, onLogout }: { user: CurrentUser; api: ApiClient; onLogout: () => Promise<void> }) {
  const { t } = useI18n();
  const allowed = useMemo(() => navigation.filter((item) => user.permissions.includes(item.permission)), [user.permissions]);
  const [page, setPage] = useState<PageKey>(allowed[0]?.key ?? "dashboard");
  const [investigation, setInvestigation] = useState<InvestigationFilters>({ environment: "", serverId: "", hours: 24 });
  const [mobileOpen, setMobileOpen] = useState(false);
  const permitted = allowed.some((item) => item.key === page);
  const groups = [...new Set(allowed.map((item) => item.group))];
  function navigate(key: PageKey) { setPage(key); setMobileOpen(false); document.querySelector<HTMLElement>("#main-content")?.focus(); }

  let content;
  if (!permitted) content = <PageState kind="unauthorized" title={t("shell.accessDenied")}>{t("shell.accessDeniedBody")}</PageState>;
  else if (page === "dashboard") content = <DashboardPage api={api} permissions={user.permissions} filters={investigation} onFilters={setInvestigation} onInspect={(serverId) => { setInvestigation((current) => ({ ...current, serverId })); navigate("servers"); }} />;
  else if (page === "servers") content = <ServersPage api={api} permissions={user.permissions} filters={investigation} onFilters={setInvestigation} />;
  else if (page === "users") content = <UsersPage api={api} currentUserId={user.id} canManageProfiles={user.permissions.includes("profile.manage")} />;
  else if (page === "profiles") content = <ProfilesPage api={api} />;
  else if (page === "access") content = <AccessPage api={api} />;
  else if (page === "inventory") content = <InventoryPage api={api} canRead={user.permissions.includes("server.read")} canManage={user.permissions.includes("server.manage")} />;
  else if (page === "environments") content = <EnvironmentsPage api={api} canRead={user.permissions.includes("server.read")} canManage={user.permissions.includes("server.manage")} />;
  else if (page === "tags") content = <TagsPage api={api} canRead={user.permissions.includes("server.read")} canManage={user.permissions.includes("server.manage")} />;
  else if (page === "calls") content = <LiveCallsPage api={api} />;
  else if (page === "history") content = <CallHistoryPage api={api} canRead={user.permissions.includes("call.read")} />;
  else if (page === "logs") content = <LogsPage api={api} />;
  else if (page === "alerts") content = <AlertsPage api={api} canAcknowledge={user.permissions.includes("alert.acknowledge")} />;
  else if (page === "incidents") content = <IncidentsPage api={api} />;
  else if (page === "maintenance") content = <MaintenancePage api={api} />;
  else if (page === "reports") content = <ReportsPage api={api} canExport={user.permissions.includes("report.export")} />;
  else if (page === "audit") content = <AuditPage api={api} />;
  else if (page === "settings") content = <MonitoringSettingsPage api={api} />;
  else content = <PageState kind="unauthorized" title={t("shell.accessDenied")} />;

  return <div className="app-shell"><a className="skip-link" href="#main-content">{t("shell.skip")}</a>
    <header className="topbar"><button className="menu-button" aria-label={t("shell.toggleNavigation")} aria-expanded={mobileOpen} onClick={() => setMobileOpen(!mobileOpen)}>☰</button><div className="brand"><span className="brand-mark" aria-hidden="true">AF</span><div><p className="eyebrow">{t("brand.observability")}</p><h1>AsterForge</h1></div></div><div className="platform-status"><span aria-hidden="true">●</span><span>{t("shell.platformOnline")}<small>{t("shell.authenticatedSession")}</small></span></div><LanguageSelector className="language-selector--shell" /><div className="account"><span className="avatar" aria-hidden="true">{user.username.slice(0, 2).toUpperCase()}</span><span><strong>{user.username}</strong><small>{t("shell.permissionCount", { count: user.permissions.length })}</small></span><button className="button--quiet" onClick={() => void onLogout()}>{t("shell.signOut")}</button></div></header>
    <aside className={`sidebar ${mobileOpen ? "sidebar--open" : ""}`} aria-label={t("shell.primaryNavigation")}>{groups.map((group) => { const groupKey: TranslationKey = `nav.group.${group}`; return <nav key={group} aria-label={t(groupKey)}><p>{t(groupKey)}</p>{allowed.filter((item) => item.group === group).map((item) => <button key={item.key} className={page === item.key ? "active" : ""} aria-current={page === item.key ? "page" : undefined} onClick={() => navigate(item.key)}><span aria-hidden="true">{page === item.key ? "◆" : "◇"}</span>{t(item.labelKey)}</button>)}</nav>; })}{allowed.length === 0 && <p className="no-navigation">{t("shell.noNavigation")}</p>}<p className="read-only"><span aria-hidden="true">◉</span><span>{t("shell.observationMode")}<small>{t("shell.noMutation")}</small></span></p></aside>
    <main id="main-content" className="content" tabIndex={-1}>{content}</main>
  </div>;
}

function AsterForgeApp() {
  const { t } = useI18n();
  const [user, setUser] = useState<CurrentUser>(); const [api, setApi] = useState<ApiClient>();
  const [expired, setExpired] = useState(false); const [timer, setTimer] = useState<number>();
  const [restoring, setRestoring] = useState(true); const restoreStarted = useRef(false);
  function endSession(wasExpired = false) { setUser(undefined); setApi(undefined); setExpired(wasExpired); if (timer !== undefined) window.clearTimeout(timer); setTimer(undefined); }
  async function authenticate(token: string, expiresAt: string) {
    const expiresAtMs = new Date(expiresAt).getTime();
    if (!Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) {
      endSession(true);
      return;
    }
    const client = new ApiClient(token, () => endSession(true));
    const currentUser = await client.me();
    setApi(client); setUser(currentUser); setExpired(false);
    const delay = Math.min(2_147_483_647, expiresAtMs - Date.now());
    setTimer(window.setTimeout(() => endSession(true), delay));
  }
  async function logout() { try { await api?.logout(); } catch { /* Local sign-out still clears sensitive state. */ } endSession(false); }
  async function changePassword(currentPassword: string, newPassword: string) { if (!api) throw new ApiError(t("error.sessionExpired"), 401, "error.sessionExpired"); await api.changePassword(currentPassword, newPassword); const currentUser = await api.me(); if (currentUser.must_change_password) throw new ApiError(t("password.notConfirmed"), 409, "password.notConfirmed"); setUser(currentUser); }
  useEffect(() => {
    if (restoreStarted.current) return;
    restoreStarted.current = true;
    void (async () => {
      try {
        const restored = await restoreSession();
        if (restored.session) {
          await authenticate(restored.session.access_token, restored.session.expires_at);
        } else {
          setExpired(restored.invalid);
        }
      } catch {
        // Restoration is best-effort; never trap the user in a retry loop.
      } finally {
        setRestoring(false);
      }
    })();
  }, []);
  useEffect(() => () => { if (timer !== undefined) window.clearTimeout(timer); }, [timer]);
  if (restoring) return <main className="session-bootstrap"><p role="status">{t("login.restoring")}</p></main>;
  if (!user || !api) return <LoginScreen expired={expired} onAuthenticated={authenticate} />;
  if (user.must_change_password) return <PasswordChangeScreen username={user.username} onChangePassword={changePassword} onLogout={logout} />;
  return <AppShell user={user} api={api} onLogout={logout} />;
}

export function App() { return <I18nProvider><AsterForgeApp /></I18nProvider>; }
