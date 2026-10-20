/**
 * Phase F7 integration journey: a superadmin opens
 * Settings from the sidebar, changes the brand colour (CSS variables update
 * app-wide without a reload), the logo pre-check rejects a bad file, the
 * taxonomy tab archives an entry, and an admin (non-superadmin) has no
 * Settings nav item or access. `fetch` is mocked; the router is in memory.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Sidebar } from "@/components/Sidebar";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { useBrand } from "@/lib/brand-context";
import SettingsPage from "@/app/(app)/admin/settings/page";
import {
  Providers,
  RAMP_3355CC,
  SHA_A,
  branding,
  file,
  installFetch,
  json,
  resetSettingsState,
  taxonomyFixture,
} from "../components/settings/harness";

const auth = vi.hoisted(() => ({ role: "superadmin" as string | undefined }));
const history = vi.hoisted(() => {
  const state = { url: "/admin/settings", listeners: new Set<() => void>() };
  const go = (url: string) => {
    state.url = url;
    state.listeners.forEach((l) => l());
  };
  return { state, push: vi.fn(go), replace: vi.fn(go) };
});

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    history.state.listeners.add(cb);
    return () => {
      history.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, () => history.state.url);
  return {
    useRouter: () => ({ push: history.push, replace: history.replace, prefetch: vi.fn() }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  };
});

// Superadmin-only permissions; admin gets the rest (mirrors ROLE_PERMISSIONS).
const SUPERADMIN_ONLY = ["users:manage", "audit:auth:read", "branding:write", "taxonomy:write"];
const ALL_PERMISSIONS = [
  "dashboard:read",
  "documents:write",
  "feedback:triage",
  "feedback:export",
  "eval:run",
  "testip:write",
  "usage:export",
  ...SUPERADMIN_ONLY,
];
function permissionsFor(role: string | undefined | null): string[] {
  if (role === "superadmin") return ALL_PERMISSIONS;
  if (role === "admin") return ALL_PERMISSIONS.filter((p) => !SUPERADMIN_ONLY.includes(p));
  if (role === "operator") return ["dashboard:read"];
  return [];
}

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: "t",
    user: { id: "u1", full_name: "Sam Super", email: "sam@contoso.example", created_at: "", role: auth.role },
    isLoading: false,
    logout: vi.fn(),
    role: auth.role ?? null,
    status: auth.role ? "active" : "pending",
    permissions: permissionsFor(auth.role),
  }),
  usePermission: (permission: string) => permissionsFor(auth.role).includes(permission),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function ProductName() {
  return <span data-testid="app-product">{useBrand().productName}</span>;
}

function renderApp() {
  return render(
    <Providers>
      <Sidebar />
      <ProductName />
      <SettingsPage />
    </Providers>,
  );
}

beforeEach(() => {
  resetSettingsState();
  auth.role = "superadmin";
  history.state.url = "/admin/settings";
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetSettingsState();
});

// Full-page renders are slow under the parallel suite; allow headroom (default 5 s).
describe("Phase F7 Settings journey", { timeout: 20_000 }, () => {
  it("superadmin: Settings nav item, brand colour save updates CSS variables and names app-wide, bad logo rejected", async () => {
    const { calls } = installFetch(({ method, url, body }) => {
      if (method === "GET" && url === "/api/proxy/branding") return json(branding());
      if (method === "PUT" && url === "/api/proxy/admin/branding") {
        return json(branding({ ...(body as object), palette: RAMP_3355CC, version: 4 }));
      }
      return undefined;
    });
    renderApp();

    const nav = screen.getByRole("link", { name: "Settings" });
    expect(nav).toHaveAttribute("href", "/admin/settings");
    expect(nav).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    await screen.findByRole("form", { name: "Branding settings" });

    // Logo pre-check: a GIF never reaches the server.
    fireEvent.drop(screen.getByTestId("dropzone-Mark (dark)"), { dataTransfer: { files: [file("mark.gif", "image/gif", 2048)] } });
    expect(await screen.findByText("mark.gif is not a PNG, WebP or SVG image.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/assets"))).toBe(false);

    fireEvent.change(screen.getByRole("textbox", { name: "Primary" }), { target: { value: "#3355CC" } });
    fireEvent.change(screen.getByLabelText("Product name"), { target: { value: "Contoso IQ" } });
    expect(screen.getByTestId("app-product")).toHaveTextContent("DeployIQ");

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--brand-600")).toBe("51 85 204"));
    expect(document.documentElement.style.getPropertyValue("--brand-400")).toBe("117 149 234");
    expect(screen.getByTestId("app-product")).toHaveTextContent("Contoso IQ");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ version: 3, product_name: "Contoso IQ", primary_hex: "#3355cc" });
    // No navigation or reload happened.
    expect(history.push).not.toHaveBeenCalled();
  });

  it("a saved logo is served through the Next app at /branding/assets/{sha} and swaps the sidebar logo", async () => {
    installFetch(({ method, body }) => {
      if (method === "GET") return json(branding());
      if (method === "POST") {
        return json({ id: SHA_A, mime: "image/png", size_bytes: 10, width: 1, height: 1, url: `/branding/assets/${SHA_A}`, created_at: null }, 201);
      }
      if (method === "PUT") {
        return json(
          branding({
            ...(body as object),
            assets: { logo_light: `/branding/assets/${SHA_A}`, logo_dark: null, mark_light: null, mark_dark: null, favicon: null },
            version: 4,
          }),
        );
      }
      return undefined;
    });
    render(
      <Providers>
        <div data-testid="app-logo">
          <BrandLogo />
        </div>
        <SettingsPage />
      </Providers>,
    );
    await screen.findByRole("form", { name: "Branding settings" });
    fireEvent.drop(screen.getByTestId("dropzone-Logo (light)"), { dataTransfer: { files: [file("logo.png", "image/png", 10)] } });
    await waitFor(() => expect(screen.getByRole("button", { name: /save changes/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(within(screen.getByTestId("app-logo")).getAllByRole("img")[0]).toHaveAttribute("src", `/branding/assets/${SHA_A}`),
    );
  });

  it("taxonomy tab (deep-linked): archived entries leave the table, the URL keeps the tab", async () => {
    const store = taxonomyFixture();
    installFetch(({ method, url }) => {
      if (method === "GET" && url.startsWith("/api/proxy/admin/insights/taxonomy")) return json({ items: store });
      if (method === "POST" && url.endsWith("/intent/SEC/archive")) {
        const sec = store.find((i) => i.code === "SEC")!;
        sec.is_active = false;
        return json({ ...sec });
      }
      if (method === "GET") return json(branding());
      return undefined;
    });
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /Insights Taxonomy/ }));
    expect(history.replace).toHaveBeenLastCalledWith("/admin/settings?tab=taxonomy", { scroll: false });
    await screen.findByTestId("taxonomy-row-SEC");

    fireEvent.click(screen.getByRole("button", { name: "Archive SEC" }));
    const dialog = await screen.findByRole("alertdialog");
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
    });
    await waitFor(() => expect(screen.queryByTestId("taxonomy-row-SEC")).not.toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /Intents/ })).toHaveTextContent("2");
  });

  it("admin (non-superadmin): no Settings nav item and the page refuses access", () => {
    auth.role = "admin";
    const { calls } = installFetch(() => undefined);
    renderApp();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Feedback" })).toBeInTheDocument();
    expect(screen.getByText("Superadmin only")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Branding settings" })).not.toBeInTheDocument();
    // Nothing but the header bell's active-alerts poll.
    expect(calls.filter((c) => !c.url.includes("/admin/alerts"))).toHaveLength(0);
  });

  it("operator and role-less users have no Settings item either", () => {
    for (const role of ["operator", null]) {
      auth.role = role as string | undefined;
      const { unmount } = render(
        <Providers>
          <Sidebar />
        </Providers>,
      );
      expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
      unmount();
    }
  });
});
