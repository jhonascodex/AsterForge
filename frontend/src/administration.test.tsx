import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuditPage, MonitoringSettingsPage } from "./administration";
import type { AlertRule, ApiClient, RetentionPolicy } from "./api";
import { I18nProvider } from "./i18n";

const rule: AlertRule = {
  id: "rule-1", code: "host.cpu.high", condition_kind: "numeric_high", severity: "WARNING",
  threshold: 90, clear_threshold: 80, duration_seconds: 300, debounce_seconds: 60, enabled: true,
};
const reopenedRules: AlertRule[] = [
  ["endpoint.availability.unavailable", "categorical_unavailable", null, null],
  ["host.memory.high", "numeric_high", 90, 80],
  ["host.filesystem.low", "numeric_low", 10, 15],
  ["calls.failed_rate.high", "numeric_high", 20, 10],
  ["recording.generation.failures", "numeric_high", 1, 0],
].map(([code, condition_kind, threshold, clear_threshold], index) => ({
  id: `reopened-${index}`, code: String(code), condition_kind: String(condition_kind),
  severity: "WARNING", threshold: threshold as number | null,
  clear_threshold: clear_threshold as number | null, duration_seconds: 0,
  debounce_seconds: 0, enabled: true,
}));
const policy: RetentionPolicy = {
  category: "operational_logs", retention_days: 30, batch_size: 5000,
  updated_at: "2026-09-13T00:00:00Z", updated_by_user_id: null,
};

describe("release administration pages", () => {
  it("renders authorized redacted audit evidence and filters it", async () => {
    const auditEvents = vi.fn().mockResolvedValue({
      items: [{
        id: "audit-1", occurred_at: "2026-09-13T00:00:00Z", actor_user_id: null,
        action: "auth.login_failed", target_type: "authentication", target_id: "digest",
        outcome: "failure", source: "authentication", request_id: null,
        metadata: { token: "[REDACTED]" },
      }],
      total: 1, limit: 100, offset: 0,
    });
    const api = { auditEvents } as unknown as ApiClient;
    render(<I18nProvider><AuditPage api={api} /></I18nProvider>);
    expect(await screen.findByText("auth.login_failed")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Action code"), { target: { value: "auth.login_failed" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(auditEvents).toHaveBeenLastCalledWith(expect.any(URLSearchParams)));
  });

  it("updates validated alert and retention configuration", async () => {
    const updateAlertRule = vi.fn().mockResolvedValue({ ...rule, threshold: 95 });
    const updateRetentionPolicy = vi.fn().mockResolvedValue({ ...policy, retention_days: 14 });
    const api = {
      alertRules: vi.fn().mockResolvedValue([rule]),
      retentionPolicies: vi.fn().mockResolvedValue([policy]),
      updateAlertRule,
      updateRetentionPolicy,
    } as unknown as ApiClient;
    render(<I18nProvider><MonitoringSettingsPage api={api} /></I18nProvider>);
    expect(await screen.findByText("host.cpu.high")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Trigger threshold"), { target: { value: "95" } });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await waitFor(() => expect(updateAlertRule).toHaveBeenCalledWith("rule-1", expect.objectContaining({ threshold: 95 })));
    fireEvent.change(screen.getByLabelText("Retention days"), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Save retention policy" }));
    await waitFor(() => expect(updateRetentionPolicy).toHaveBeenCalledWith("operational_logs", { retention_days: 14, batch_size: 5000 }));
  });

  it("renders the complete reopened initial rule set with bounded numeric inputs", async () => {
    const api = {
      alertRules: vi.fn().mockResolvedValue(reopenedRules),
      retentionPolicies: vi.fn().mockResolvedValue([policy]),
    } as unknown as ApiClient;
    render(<I18nProvider><MonitoringSettingsPage api={api} /></I18nProvider>);
    for (const item of reopenedRules) {
      expect(await screen.findByText(item.code)).toBeInTheDocument();
    }
    const thresholds = screen.getAllByLabelText("Trigger threshold") as HTMLInputElement[];
    expect(thresholds.map((input) => input.max)).toEqual(["100", "100", "100", "10000"]);
  });
});
