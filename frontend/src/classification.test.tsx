import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "./api";
import { EnvironmentsPage, TagsPage } from "./classification";
import { I18nProvider, LanguageSelector } from "./i18n";

function response(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function api() { return new ApiClient("memory-only", vi.fn()); }

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("inventory classification administration", () => {
  it("renders distinct unauthorized states without contacting catalog APIs", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const environmentView = render(<I18nProvider><EnvironmentsPage api={api()} canRead={false} canManage={false} /></I18nProvider>);
    expect(screen.getByRole("heading", { name: "Environment access not granted" })).toBeInTheDocument();
    environmentView.unmount();
    render(<I18nProvider><TagsPage api={api()} canRead={false} canManage={false} /></I18nProvider>);
    expect(screen.getByRole("heading", { name: "Tag access not granted" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows environment usage counts and read-only management authorization separately", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([
      { id: "dev", code: "DEV", display_name: "Development", enabled: true, server_count: 2 },
      { id: "qa", code: "QA", display_name: "Quality Assurance", enabled: false, server_count: 0 },
    ])));
    render(<I18nProvider><EnvironmentsPage api={api()} canRead canManage={false} /></I18nProvider>);
    expect(screen.getByRole("heading", { name: "Loading environments" })).toBeInTheDocument();
    expect(await screen.findByText("Development")).toBeInTheDocument();
    expect(screen.getByLabelText("2 associated servers")).toHaveTextContent("2");
    expect(screen.getByRole("heading", { name: "Catalog management not granted" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create environment" })).not.toBeInTheDocument();
  });

  it("distinguishes tag loading, empty and error states and offers first-tag creation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response([])));
    const emptyView = render(<I18nProvider><TagsPage api={api()} canRead canManage /></I18nProvider>);
    expect(screen.getByRole("heading", { name: "Loading tags" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "No tags available" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create the first tag" })).toBeInTheDocument();
    emptyView.unmount();

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      detail: { code: "catalog_unavailable", message: "Unavailable" },
    }, 503)));
    render(<I18nProvider><TagsPage api={api()} canRead canManage /></I18nProvider>);
    expect(await screen.findByRole("heading", { name: "Tags unavailable" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No tags available" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("creates, updates and deletes environments through the existing API", async () => {
    let environments = [
      { id: "dev", code: "DEV", display_name: "Development", enabled: true, server_count: 1 },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { code: string; display_name: string };
        environments = [...environments, { id: "lab", ...body, code: body.code.toUpperCase(), enabled: true, server_count: 0 }];
        return response(environments.at(-1), 201);
      }
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as { display_name?: string; enabled?: boolean };
        environments = environments.map((item) => url.endsWith(item.id) ? { ...item, ...body } : item);
        return response(environments.find((item) => url.endsWith(item.id)));
      }
      if (init?.method === "DELETE") {
        environments = environments.filter((item) => !url.endsWith(item.id));
        return response(null, 204);
      }
      return response(environments);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<I18nProvider><EnvironmentsPage api={api()} canRead canManage /></I18nProvider>);

    const devRow = (await screen.findByText("Development")).closest("tr") as HTMLTableRowElement;
    fireEvent.click(within(devRow).getByRole("button", { name: "Edit" }));
    const editForm = screen.getByRole("form", { name: "Edit environment" });
    expect(within(editForm).getByLabelText("Code")).toBeDisabled();
    fireEvent.change(within(editForm).getByLabelText("Display name"), { target: { value: "Developer Systems" } });
    fireEvent.click(within(editForm).getByLabelText("Enabled"));
    fireEvent.click(within(editForm).getByRole("button", { name: "Save environment" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Environment updated."));

    const createForm = screen.getByRole("form", { name: "Create environment" });
    fireEvent.change(within(createForm).getByLabelText("Code"), { target: { value: "lab" } });
    fireEvent.change(within(createForm).getByLabelText("Display name"), { target: { value: "Laboratory" } });
    fireEvent.click(within(createForm).getByRole("button", { name: "Create environment" }));
    const labRow = (await screen.findByText("Laboratory")).closest("tr") as HTMLTableRowElement;
    fireEvent.click(within(labRow).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Laboratory")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true);
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
  });

  it("creates the first tag, preserves its operational value, renames and deletes it", async () => {
    let tags: Array<{ id: string; name: string; server_count: number }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { name: string };
        tags = [{ id: "edge", name: body.name, server_count: 0 }];
        return response(tags[0], 201);
      }
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as { name: string };
        tags = tags.map((item) => url.endsWith(item.id) ? { ...item, name: body.name } : item);
        return response(tags[0]);
      }
      if (init?.method === "DELETE") { tags = []; return response(null, 204); }
      return response(tags);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<I18nProvider><LanguageSelector /><TagsPage api={api()} canRead canManage /></I18nProvider>);

    await screen.findByRole("heading", { name: "No tags available" });
    fireEvent.click(screen.getByRole("button", { name: "Create the first tag" }));
    const createForm = screen.getByRole("form", { name: "Create tag" });
    fireEvent.change(within(createForm).getByLabelText("Tag name"), { target: { value: "Edge-Lima" } });
    fireEvent.click(within(createForm).getByRole("button", { name: "Create tag" }));
    await screen.findByText("Edge-Lima");
    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "es" } });
    const tagRow = (await screen.findByText("Edge-Lima")).closest("tr") as HTMLTableRowElement;
    expect(within(tagRow).getByText("Edge-Lima")).toBeInTheDocument();
    fireEvent.click(within(tagRow).getByRole("button", { name: "Editar" }));
    const editForm = screen.getByRole("form", { name: "Editar etiqueta" });
    fireEvent.change(within(editForm).getByLabelText("Nombre de etiqueta"), { target: { value: "Core-Lima" } });
    fireEvent.click(within(editForm).getByRole("button", { name: "Guardar etiqueta" }));
    const renamedRow = (await screen.findByText("Core-Lima")).closest("tr") as HTMLTableRowElement;
    fireEvent.click(within(renamedRow).getByRole("button", { name: "Eliminar" }));
    expect(await screen.findByRole("heading", { name: "No hay etiquetas disponibles" })).toBeInTheDocument();
  });
});
