/**
 * User Management page (planchanges4 F1.4.1, F1.4.2): KPI strip, chart
 * drill-downs into the Users filters, the Users table and drawer with its
 * safeguards, optimistic updates with rollback and the 409 toast.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";
import {
  LAST_SUPERADMIN_REASON,
  SELF_CHANGE_REASON,
} from "@/components/admin/user-management/UserDrawer";
import { memoryRouter } from "../helpers/memory-router";
import {
  deferred,
  fixtures,
  renderWithProviders,
  setupUserManagement,
  type FakeServer,
} from "../helpers/user-management-server";

vi.mock("next/navigation", () => import("../helpers/memory-router").then((mod) => mod.navigationMock));

// Multi-step UI flows with animated overlays; the 5 s default is tight on loaded CI runners.
vi.setConfig({ testTimeout: 20_000 });

let server: FakeServer;

function renderPage(url?: string, data = fixtures()) {
  server = setupUserManagement(url, data);
  return renderWithProviders(<UserManagementPage />);
}

const usersTable = () => screen.getByRole("table");

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KPI strip and charts (F1.4.1)", () => {
  beforeEach(() => {
    renderPage();
  });

  it("shows the five KPIs from /admin/users-accounts/stats, each with a sparkline, pending highlighted", async () => {
    const kpis = await screen.findByTestId("um-kpis");
    await waitFor(() => expect(within(kpis).getByText("7")).toBeInTheDocument());
    for (const [key, value] of [
      ["total", "5"],
      ["active", "3"],
      ["pending", "1"],
      ["suspended", "1"],
      ["logins", "7"],
    ] as const) {
      const card = screen.getByTestId(`kpi-${key}`);
      expect(within(card).getByText(value)).toBeInTheDocument();
      expect(within(card).getByRole("group", { name: /trend$/ })).toBeInTheDocument();
    }
    expect(screen.getByTestId("kpi-pending")).toHaveAttribute("data-highlight", "true");
    expect(screen.getByTestId("kpi-total")).not.toHaveAttribute("data-highlight");
    expect(server.callsTo("GET", "/admin/users-accounts/stats")[0].query.get("days")).toBe("30");
  });

  it("KPI cards deep-link to the filtered Users tab", async () => {
    await waitFor(() => expect(within(screen.getByTestId("kpi-active")).getByText("3")).toBeInTheDocument());
    expect(within(screen.getByTestId("kpi-suspended")).getByRole("link")).toHaveAttribute(
      "href",
      "/admin/user-management?tab=users&status=suspended",
    );
    expect(within(screen.getByTestId("kpi-pending")).getByRole("link")).toHaveAttribute(
      "href",
      "/admin/user-management?tab=users&status=pending",
    );
  });

  it("a role donut segment filters the Users tab via the URL and refetches with role + status", async () => {
    const admin = await screen.findByRole("button", { name: /^Admin: 1/ });
    fireEvent.click(admin);
    expect(memoryRouter.params().get("role")).toBe("admin");
    expect(memoryRouter.params().get("status")).toBe("active");
    await waitFor(() => {
      const last = server.callsTo("GET", "/admin/users-accounts").filter((c) => c.path === "/admin/users-accounts").at(-1);
      expect(last?.query.get("role")).toBe("admin");
      expect(last?.query.get("status")).toBe("active");
    });
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    expect(within(usersTable()).queryByText("Bob Operator")).not.toBeInTheDocument();
  });

  it("a funnel stage sets the status filter; keyboard Enter works too", async () => {
    const requested = await screen.findByRole("button", { name: /^Requested: 8/ });
    requested.focus();
    fireEvent.keyDown(requested, { key: "Enter" });
    expect(memoryRouter.params().get("status")).toBe("pending");
    expect(memoryRouter.params().get("role")).toBeNull();
  });

  it("the logins bar sorts Users by last login; the histogram opens approved decisions", async () => {
    const bars = await screen.findByRole("group", { name: "Logins per day" });
    fireEvent.click(within(bars).getAllByRole("button")[0]);
    expect(memoryRouter.params().get("sort")).toBe("last_login_at");

    const hist = screen.getByRole("group", { name: "Time to approve" });
    fireEvent.click(within(hist).getByRole("button", { name: /^<1h: 2/ }));
    expect(memoryRouter.params().get("tab")).toBe("requests");
    expect(memoryRouter.params().get("view")).toBe("approved");
  });
});

describe("Users tab (F1.4.2)", () => {
  it("lists users with role, status, last login and approver; search goes to the URL and API", async () => {
    renderPage();
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    expect(within(usersTable()).getAllByText("root@contoso.example").length).toBeGreaterThan(0);
    expect(within(usersTable()).getByText("No role")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search name or email…"), { target: { value: "alice" } });
    await waitFor(() => expect(memoryRouter.params().get("q")).toBe("alice"));
    await waitFor(() =>
      expect(server.callsTo("GET", "/admin/users-accounts").some((c) => c.query.get("search") === "alice")).toBe(true),
    );
  });

  it("restores filters from the URL and sends only allow-listed values", async () => {
    renderPage("/admin/user-management?status=suspended&role=bogus&sort=password");
    await waitFor(() => expect(within(usersTable()).getByText("Bob Operator")).toBeInTheDocument());
    const call = server.callsTo("GET", "/admin/users-accounts").find((c) => c.path === "/admin/users-accounts");
    expect(call?.query.get("status")).toBe("suspended");
    expect(call?.query.get("role")).toBeNull();
    expect(call?.query.get("sort")).toBe("created_at");
  });

  it("opens the drawer with profile, live sessions, activity and audit entries", async () => {
    renderPage("/admin/user-management?user=u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    await waitFor(() => expect(within(dialog).getByText("Firefox on Windows")).toBeInTheDocument());
    expect(within(dialog).getByText("10.1.1.1")).toBeInTheDocument();
    // Recent activity: Alice's own sign-in; audit entries: the role change about her.
    expect(within(dialog).getByText("Signed in")).toBeInTheDocument();
    expect(within(dialog).getByText("Role changed")).toBeInTheDocument();
    expect(within(dialog).getByText(/operator → admin/)).toBeInTheDocument();
  });

  it("row click opens the drawer and Escape closes it (URL round-trip)", async () => {
    renderPage();
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    fireEvent.click(within(usersTable()).getByRole("button", { name: "Alice Admin" }));
    expect(memoryRouter.params().get("user")).toBe("u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(memoryRouter.params().get("user")).toBeNull());
  });

  it("blocks self-changes with the reason as a tooltip", async () => {
    renderPage("/admin/user-management?user=me-1");
    const dialog = await screen.findByRole("dialog", { name: "Root Admin" });
    const suspend = within(dialog).getByRole("button", { name: /Suspend/ });
    expect(suspend).toHaveAttribute("aria-disabled", "true");
    expect(suspend).toHaveAccessibleDescription(SELF_CHANGE_REASON);
    expect(within(dialog).getByRole("button", { name: "Change role" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(suspend);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(server.callsTo("PATCH", "/admin/users-accounts")).toHaveLength(0);
  });

  it("blocks demoting or suspending the last active superadmin", async () => {
    const data = fixtures();
    data.stats.role_mix[0].count = 1;
    renderPage("/admin/user-management?user=u-5", data);
    const dialog = await screen.findByRole("dialog", { name: "Sam Super" });
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: /Suspend/ })).toHaveAccessibleDescription(LAST_SUPERADMIN_REASON),
    );
    fireEvent.change(within(dialog).getByLabelText("Role"), { target: { value: "admin" } });
    const change = within(dialog).getByRole("button", { name: "Change role" });
    expect(change).toHaveAttribute("aria-disabled", "true");
    expect(within(dialog).getAllByRole("tooltip", { hidden: true }).map((t) => t.textContent)).toContain(LAST_SUPERADMIN_REASON);
  });

  it("changes a role optimistically and sends the version with the CSRF header", async () => {
    renderPage("/admin/user-management?user=u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    const reply = deferred();
    server.on("PATCH /admin/users-accounts/u-2", () => reply.promise);

    fireEvent.change(within(dialog).getByLabelText("Role"), { target: { value: "operator" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change role" }));
    const confirm = await screen.findByRole("dialog", { name: "Change role" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Change role" }));

    const row = within(usersTable()).getByText("Alice Admin").closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("Operator")).toBeInTheDocument());
    const patch = server.callsTo("PATCH", "/admin/users-accounts/u-2")[0];
    expect(patch.body).toEqual({ version: 3, role: "operator" });
    expect(patch.headers["X-Requested-With"]).toBe("askdiq");

    await act(async () => reply.resolve({ status: 200, body: { ...server.data.users[1], role: "operator", version: 4 } }));
    expect(await screen.findByText("Role set to Operator")).toBeInTheDocument();
  });

  it("rolls back the optimistic change when the request fails", async () => {
    renderPage("/admin/user-management?user=u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    const reply = deferred();
    server.on("PATCH /admin/users-accounts/u-2", () => reply.promise);

    fireEvent.click(within(dialog).getByRole("button", { name: /Suspend/ }));
    const confirm = await screen.findByRole("alertdialog", { name: "Suspend user" });
    const submit = within(confirm).getByRole("button", { name: "Suspend" });
    expect(submit).toBeDisabled();
    await userEvent.type(within(confirm).getByRole("textbox"), "alice@contoso.example");
    fireEvent.click(submit);

    const row = () => within(usersTable()).getByText("Alice Admin").closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row()).getByText("Suspended")).toBeInTheDocument());
    await act(async () => reply.resolve({ status: 500, body: { detail: "Database unavailable" } }));
    await waitFor(() => expect(within(row()).getByText("Active")).toBeInTheDocument());
    expect(await screen.findByText("Change failed")).toBeInTheDocument();
    expect(screen.getByText("Database unavailable")).toBeInTheDocument();
  });

  it("a 409 rolls back, refetches and shows the conflict toast with the server reason", async () => {
    renderPage("/admin/user-management?user=u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    server.on("PATCH /admin/users-accounts/u-2", () => ({
      status: 409,
      body: { detail: "This user was changed by someone else. Reload and try again." },
    }));
    const listCalls = server.callsTo("GET", "/admin/users-accounts").length;

    fireEvent.change(within(dialog).getByLabelText("Role"), { target: { value: "superadmin" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change role" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Change role" })).getByRole("button", { name: "Change role" }));

    expect(await screen.findByText("Changed by someone else")).toBeInTheDocument();
    const row = within(usersTable()).getByText("Alice Admin").closest("tr") as HTMLElement;
    expect(within(row).getByText("Admin")).toBeInTheDocument();
    await waitFor(() => expect(server.callsTo("GET", "/admin/users-accounts").length).toBeGreaterThan(listCalls));
  });

  it("revokes sessions behind a typed confirmation", async () => {
    renderPage("/admin/user-management?user=u-2");
    const dialog = await screen.findByRole("dialog", { name: "Alice Admin" });
    fireEvent.click(within(dialog).getByRole("button", { name: /Revoke sessions/ }));
    const confirm = await screen.findByRole("alertdialog", { name: "Revoke all sessions" });
    await userEvent.type(within(confirm).getByRole("textbox"), "alice@contoso.example");
    fireEvent.click(within(confirm).getByRole("button", { name: "Revoke sessions" }));
    expect(await screen.findByText("Revoked 2 sessions")).toBeInTheDocument();
    expect(server.callsTo("POST", "/admin/users-accounts/u-2/revoke-sessions")).toHaveLength(1);
  });

  it("invites by email; a duplicate email (409) shows a toast", async () => {
    renderPage();
    await waitFor(() => expect(within(usersTable()).getByText("Alice Admin")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Invite user" }));
    const modal = await screen.findByRole("dialog", { name: "Invite user" });
    fireEvent.click(within(modal).getByRole("button", { name: "Send invite" }));
    expect(within(modal).getByText("Enter an email address.")).toBeInTheDocument();
    expect(server.callsTo("POST", "/admin/users-accounts/invite")).toHaveLength(0);

    await userEvent.type(within(modal).getByLabelText("Email"), "alice@contoso.example");
    fireEvent.click(within(modal).getByRole("button", { name: "Send invite" }));
    expect(await screen.findByText("A user with this email already exists.")).toBeInTheDocument();

    const email = within(modal).getByLabelText("Email");
    await userEvent.clear(email);
    await userEvent.type(email, "frank@contoso.example");
    fireEvent.change(within(modal).getByLabelText("Role"), { target: { value: "admin" } });
    fireEvent.click(within(modal).getByRole("button", { name: "Send invite" }));
    expect(await screen.findByText("Invitation created")).toBeInTheDocument();
    expect(server.callsTo("POST", "/admin/users-accounts/invite").at(-1)?.body).toEqual({
      email: "frank@contoso.example",
      role: "admin",
    });
  });
});

describe("permission gating", () => {
  it("non-superadmins see a restricted state and no User Management API calls", async () => {
    const data = fixtures();
    data.me = { ...data.me, role: "admin", permissions: ["dashboard:read"] };
    renderPage(undefined, data);
    expect(await screen.findByText("Superadmins only")).toBeInTheDocument();
    expect(server.callsTo("GET", "/admin/")).toHaveLength(0);
  });

  it("fails closed when /auth/me fails", async () => {
    server = setupUserManagement();
    server.on("GET /auth/me", () => ({ status: 401, body: { detail: "Not signed in." } }));
    renderWithProviders(<UserManagementPage />);
    expect(await screen.findByText("Could not check your access")).toBeInTheDocument();
    expect(server.callsTo("GET", "/admin/")).toHaveLength(0);
  });
});
