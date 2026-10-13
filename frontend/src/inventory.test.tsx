import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, type Server } from "./api";
import { I18nProvider } from "./i18n";
import { InventoryPage } from "./inventory";

function response(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const environment = { id: "env-1", code: "PROD", display_name: "Production", enabled: true };
const tag = { id: "tag-1", name: "Critical" };
const server: Server = {
  id: "server-1",
  name: "PBX Lima",
  hostname: "pbx-lima.internal",
  environment,
  location: "Lima DC",
  ami_port: 5039,
  ami_tls: true,
  monitoring_enabled: true,
  tags: [tag],
  ami_username_configured: true,
  ami_secret_configured: true,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:00Z",
  archived_at: null,
};

function view(canRead: boolean, canManage: boolean) {
  const api = new ApiClient("memory-only", vi.fn());
  return render(<I18nProvider><InventoryPage api={api} canRead={canRead} canManage={canManage} /></I18nProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("InventoryPage", () => {
  it("renders an unauthorized state without contacting inventory APIs", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    view(false, false);
    expect(screen.getByRole("heading", { name: "Inventory access not granted" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("distinguishes an empty inventory from an API failure for read-only users", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/servers?")) return response({ items: [], total: 0, limit: 200, offset: 0 });
      return response([]);
    }));
    const rendered = view(true, false);
    expect(screen.getByRole("heading", { name: "Loading environment and tag catalogs" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "No matching servers" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Register server" })).not.toBeInTheDocument();
    rendered.unmount();

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      detail: { code: "inventory_unavailable", message: "Inventory service unavailable" },
    }, 503)));
    view(true, false);
    expect(await screen.findByRole("heading", { name: "Server inventory unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Classification catalogs unavailable" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No matching servers" })).not.toBeInTheDocument();
  });

  it("uses API filters and keeps monitoring drill-down separate", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/servers?")) return response({ items: [server], total: 1, limit: 200, offset: 0 });
      if (url.includes("/environments")) return response([environment]);
      return response([tag]);
    });
    vi.stubGlobal("fetch", fetchMock);
    view(true, false);
    await screen.findByText("PBX Lima");
    fireEvent.change(screen.getByLabelText("Name, hostname or location"), { target: { value: "Lima" } });
    fireEvent.change(screen.getByLabelText("Environment"), { target: { value: "PROD" } });
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "Critical" } });
    fireEvent.change(screen.getByLabelText("Monitoring"), { target: { value: "enabled" } });
    fireEvent.click(screen.getByLabelText("Include archived"));
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => {
      const url = String(input);
      return url.includes("search=Lima") && url.includes("environment=PROD") &&
        url.includes("tag=Critical") && url.includes("monitoring_enabled=true") &&
        url.includes("include_archived=true");
    })).toBe(true));
    expect(screen.getByText(/Explicit onboarding/)).toBeInTheDocument();
    expect(screen.queryByText(/Asterisk health/)).not.toBeInTheDocument();
  });

  it("never renders stored AMI values and sends only explicitly entered replacements", async () => {
    let updateBody: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/servers/server-1") && init?.method === "PATCH") {
        updateBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return response(server);
      }
      if (url.includes("/servers?")) return response({ items: [server], total: 1, limit: 200, offset: 0 });
      if (url.includes("/environments")) return response([environment]);
      return response([tag]);
    });
    vi.stubGlobal("fetch", fetchMock);
    view(true, true);
    const serverRow = (await screen.findByText("PBX Lima")).closest("tr");
    expect(serverRow).not.toBeNull();
    fireEvent.click(within(serverRow as HTMLTableRowElement).getByRole("button", { name: "Edit" }));
    expect(screen.getByText(/Stored values are never displayed/)).toBeInTheDocument();
    expect(screen.getByLabelText("New AMI username")).toHaveValue("");
    expect(screen.getByLabelText("New AMI secret")).toHaveValue("");

    fireEvent.change(screen.getByLabelText("New AMI username"), { target: { value: "readonly-ami" } });
    fireEvent.change(screen.getByLabelText("New AMI secret"), { target: { value: "new-secret-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(updateBody).toBeDefined());
    expect(updateBody).toMatchObject({ ami_username: "readonly-ami", ami_secret: "new-secret-value" });
    expect(document.body.textContent).not.toContain("new-secret-value");
  });

  it("keeps catalog administration out of server inventory and shows the tag empty state", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/servers?")) return response({ items: [], total: 0, limit: 200, offset: 0 });
      if (url.includes("/environments")) return response([environment]);
      return response([]);
    }));

    view(true, true);
    expect(await screen.findByText("No tags exist yet. A server may be saved with zero tags.")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Tags" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create environment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create tag" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create the first tag" })).not.toBeInTheDocument();
  });

  it("restores and persists environment and tag assignments, including deselection", async () => {
    const qa = { id: "env-2", code: "QA", display_name: "Quality Assurance", enabled: true };
    const edge = { id: "tag-2", name: "Edge" };
    let currentServer = server;
    let updateBody: Record<string, unknown> | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/servers/server-1") && init?.method === "PATCH") {
        updateBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        currentServer = { ...currentServer, environment: qa, tags: [edge] };
        return response(currentServer);
      }
      if (url.includes("/servers?")) return response({ items: [currentServer], total: 1, limit: 200, offset: 0 });
      if (url.includes("/environments")) return response([environment, qa]);
      return response([tag, edge]);
    });
    vi.stubGlobal("fetch", fetchMock);
    view(true, true);

    let serverRow = (await screen.findByText("PBX Lima")).closest("tr");
    fireEvent.click(within(serverRow as HTMLTableRowElement).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Environment", { selector: "select[name=environment_id]" })).toHaveValue("env-1");
    expect(screen.getByLabelText("Critical")).toBeChecked();
    expect(screen.getByLabelText("Edge")).not.toBeChecked();
    fireEvent.change(screen.getByLabelText("Environment", { selector: "select[name=environment_id]" }), { target: { value: "env-2" } });
    fireEvent.click(screen.getByLabelText("Critical"));
    fireEvent.click(screen.getByLabelText("Edge"));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(updateBody).toMatchObject({ environment_id: "env-2", tag_ids: ["tag-2"] }));

    serverRow = screen.getByText("PBX Lima").closest("tr");
    fireEvent.click(within(serverRow as HTMLTableRowElement).getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Environment", { selector: "select[name=environment_id]" })).toHaveValue("env-2");
    expect(screen.getByLabelText("Critical")).not.toBeChecked();
    expect(screen.getByLabelText("Edge")).toBeChecked();
  });
});
