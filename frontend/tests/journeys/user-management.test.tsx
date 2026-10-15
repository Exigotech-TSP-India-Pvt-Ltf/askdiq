/**
 * User Management journey (planchanges4 F1.4, newplan §7.8): a superadmin
 * drills from a chart into the Users tab, inspects a pending user, approves
 * their request from the queue with the keyboard, sees KPIs and lists
 * refresh, checks the audit trail, and the page follows light / dark
 * instantly. `fetch` is mocked; the router is in memory.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";
import { chartTheme } from "@/lib/theme-tokens";
import { memoryRouter } from "../helpers/memory-router";
import { renderWithProviders, setupUserManagement, type FakeServer } from "../helpers/user-management-server";

vi.mock("next/navigation", () => import("../helpers/memory-router").then((mod) => mod.navigationMock));

// Multi-step UI flows with animated overlays; the 5 s default is tight on loaded CI runners.
vi.setConfig({ testTimeout: 20_000 });

let server: FakeServer;

beforeEach(() => {
  document.documentElement.classList.remove("dark");
  server = setupUserManagement();
});

afterEach(() => {
  document.documentElement.classList.remove("dark");
  vi.unstubAllGlobals();
});

const usersTable = () => screen.getByRole("table");

describe("User Management journey", () => {
  it("drill-down, pending user, keyboard approval, refreshed KPIs and audit trail", async () => {
    renderWithProviders(<UserManagementPage />);
    await screen.findByTestId("um-kpis");
    expect(screen.getByRole("heading", { name: "User Management" })).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByTestId("kpi-pending")).getByText("1")).toBeInTheDocument());
    expect(screen.getByText("3 pending")).toBeInTheDocument();

    // 1. Funnel "Requested" drills into the Users tab filtered to pending users.
    fireEvent.click(await screen.findByRole("button", { name: /^Requested: 8/ }));
    expect(memoryRouter.params().get("status")).toBe("pending");
    await waitFor(() => expect(within(usersTable()).getByText("Carol New")).toBeInTheDocument());
    expect(within(usersTable()).queryByText("Alice Admin")).not.toBeInTheDocument();

    // 2. Open Carol: pending, no role yet.
    fireEvent.click(within(usersTable()).getByRole("button", { name: "Carol New" }));
    const drawer = await screen.findByRole("dialog", { name: "Carol New" });
    expect(within(drawer).getAllByText("Pending").length).toBeGreaterThan(0);
    expect(within(drawer).getByText("No role")).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(memoryRouter.params().get("user")).toBeNull());

    // 3. Access Requests tab: approve Carol with `a` on her focused row.
    fireEvent.click(screen.getByRole("tab", { name: /Access Requests/ }));
    expect(memoryRouter.params().get("tab")).toBe("requests");
    const carol = await screen.findByTestId("request-row-r-1");
    const statsBefore = server.callsTo("GET", "/admin/users-accounts/stats").length;
    carol.focus();
    fireEvent.keyDown(carol, { key: "a" });
    await waitFor(() => expect(screen.queryByTestId("request-row-r-1")).not.toBeInTheDocument());
    expect(server.callsTo("POST", "/admin/access-requests/r-1/approve")[0].body).toEqual({ role: "admin" });
    expect(await screen.findByText("Access approved")).toBeInTheDocument();
    await waitFor(() => expect(server.callsTo("GET", "/admin/users-accounts/stats").length).toBeGreaterThan(statsBefore));

    // 4. Audit Trail tab lists auth events only.
    fireEvent.click(screen.getByRole("tab", { name: /Audit Trail/ }));
    await waitFor(() => expect(screen.getByText("Sign-in denied")).toBeInTheDocument());

    // 5. Back to Users clears the tab param (default view).
    fireEvent.click(screen.getByRole("tab", { name: /^Users/ }));
    expect(memoryRouter.params().get("tab")).toBeNull();
  });

  it("renders with theme tokens and recolours charts instantly on a light / dark toggle", async () => {
    const { container } = renderWithProviders(<UserManagementPage />);
    const bars = await screen.findByRole("group", { name: "Logins per day" });
    const barFill = () => bars.querySelector("rect[rx='3']")?.getAttribute("fill");
    await waitFor(() => expect(barFill()).toBe(chartTheme.light.categorical[0]));

    // Every card / table uses theme tokens: no fixed white or gray-only classes.
    for (const el of Array.from(container.querySelectorAll<HTMLElement>("[class]"))) {
      const cls = el.getAttribute("class") ?? "";
      if (/(^|\s)bg-white(\s|$)/.test(cls)) expect(cls).toMatch(/dark:(?:[\w-]+:)*bg-/);
      expect(cls).not.toMatch(/(^|\s)text-gray-\d{2,3}(\s|$)/);
    }
    expect(screen.getByTestId("kpi-total").querySelector(".bg-surface-card")).not.toBeNull();

    await act(async () => {
      document.documentElement.classList.add("dark");
    });
    await waitFor(() => expect(barFill()).toBe(chartTheme.dark.categorical[0]));
    const donut = screen.getByRole("group", { name: "Role distribution" });
    expect(donut.querySelector("path")?.getAttribute("fill")).toBe(chartTheme.dark.categorical[0]);

    await act(async () => {
      document.documentElement.classList.remove("dark");
    });
    await waitFor(() => expect(barFill()).toBe(chartTheme.light.categorical[0]));
  });

  it("deep links restore the tab, filters and the open drawer", async () => {
    server = setupUserManagement("/admin/user-management?tab=users&role=admin&user=u-2");
    renderWithProviders(<UserManagementPage />);
    expect(await screen.findByRole("dialog", { name: "Alice Admin" })).toBeInTheDocument();
    await waitFor(() =>
      expect(server.callsTo("GET", "/admin/users-accounts").some((c) => c.query.get("role") === "admin")).toBe(true),
    );
    expect(screen.getByRole("tab", { name: /^Users/ })).toHaveAttribute("aria-selected", "true");
  });
});
