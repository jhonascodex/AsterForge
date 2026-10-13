import type { ReactNode } from "react";
import { useI18n } from "./i18n";

export function StatusBadge({ enabled }: { enabled: boolean }) {
  const { t } = useI18n();
  return (
    <span className={`badge ${enabled ? "badge--ok" : "badge--muted"}`}>
      <span aria-hidden="true">{enabled ? "●" : "○"}</span> {t(enabled ? "common.enabled" : "common.disabled")}
    </span>
  );
}

export function PageState({
  kind,
  title,
  children,
  action,
}: {
  kind: "loading" | "empty" | "error" | "unauthorized";
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`page-state page-state--${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span className="page-state__icon" aria-hidden="true">
        {kind === "loading" ? "···" : kind === "empty" ? "◇" : kind === "error" ? "!" : "×"}
      </span>
      <div>
        <h3>{title}</h3>
        {children && <p>{children}</p>}
        {action}
      </div>
    </div>
  );
}

export function FieldError({ children }: { children?: ReactNode }) {
  return children ? <p className="field-error" role="alert">{children}</p> : null;
}
