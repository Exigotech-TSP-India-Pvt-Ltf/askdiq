/**
 * User Management tabs (planchanges4 F1.4.3): Access Requests (approve with a
 * role, reject with a note, bulk approve, keyboard `a` / `x`, animate-out and
 * rollback, history), Roles & Permissions matrix and the auth Audit Trail.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserManagementPage } from "@/components/admin/user-management/UserManagementPage";
import { permissionDrift } from "@/components/admin/user-management/RolesMatrixTab";
import { usersQuery, userManagementApi } from "@/lib/user-management-api";
import { memoryRouter } from "../helpers/memory-router";
import {
  ME,
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

function renderTab(url: string, data = fixtures()) {
  server = setupUserManagement(url, data);
  return renderWithProviders(<UserManagementPage />);
}

const row = (id: string) => screen.getByTestId(`request-row-${id}`);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Access Requests tab", () => {
  it("lists the queue with requested role, justification and waiting time; the tab shows the open count", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    expect(within(row("r-1")).getByText("carol@contoso.example")).toBeInTheDocument();
    expect(within(row("r-1")).getByText("2h 0m")).toBeInTheDocument();
    expect(within(row("r-3")).getByText("Evaluation runs")).toBeInTheDocument();
    expect(screen.queryByTestId("request-row-r-9")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("tab", { name: /Access Requests/ })).toHaveTextContent("3"));
  });

  it("approves with the chosen role; the row animates out", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-2")).toBeInTheDocument());
    fireEvent.change(within(row("r-2")).getByLabelText("Role for dave@contoso.example"), { target: { value: "admin" } });
    fireEvent.click(within(row("r-2")).getByRole("button", { name: "Approve dave@contoso.example" }));
    await waitFor(() => expect(screen.queryByTestId("request-row-r-2")).not.toBeInTheDocument());
    expect(server.callsTo("POST", "/admin/access-requests/r-2/approve")[0].body).toEqual({ role: "admin" });
    expect(await screen.findByText("Access approved")).toBeInTheDocument();
  });

  it("an approved row leaves through AnimatePresence (still mounted while it exits)", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-3")).toBeInTheDocument());
    const exiting = row("r-3");
    fireEvent.click(within(exiting).getByRole("button", { name: "Approve erin@contoso.example" }));
    // Removed from the data immediately (optimistic), kept in the DOM for the exit animation.
    expect(exiting.isConnected).toBe(true);
    await waitFor(() => expect(exiting.isConnected).toBe(false));
  });

  it("keyboard: `a` approves the focused row with its role, `x` opens reject with a required note", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());

    row("r-1").focus();
    fireEvent.keyDown(row("r-1"), { key: "a" });
    await waitFor(() => expect(server.callsTo("POST", "/admin/access-requests/r-1/approve")).toHaveLength(1));
    expect(server.callsTo("POST", "/admin/access-requests/r-1/approve")[0].body).toEqual({ role: "admin" });

    // Typing in the role select must not trigger shortcuts.
    fireEvent.keyDown(within(row("r-2")).getByLabelText("Role for dave@contoso.example"), { key: "x" });
    expect(screen.queryByRole("dialog", { name: "Reject access request" })).not.toBeInTheDocument();

    row("r-2").focus();
    fireEvent.keyDown(row("r-2"), { key: "x" });
    const modal = await screen.findByRole("dialog", { name: "Reject access request" });
    const reject = within(modal).getByRole("button", { name: "Reject" });
    expect(reject).toBeDisabled();
    await userEvent.type(within(modal).getByLabelText("Note"), "Not on the project");
    fireEvent.click(reject);
    await waitFor(() => expect(server.callsTo("POST", "/admin/access-requests/r-2/reject")).toHaveLength(1));
    expect(server.callsTo("POST", "/admin/access-requests/r-2/reject")[0].body).toEqual({ note: "Not on the project" });
    await waitFor(() => expect(screen.queryByTestId("request-row-r-2")).not.toBeInTheDocument());
  });

  it("bulk approves the selected rows with one role", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    const bar = screen.getByTestId("um-bulk-bar");
    const approveSelected = within(bar).getByRole("button", { name: "Approve selected" });
    expect(approveSelected).toBeDisabled();

    fireEvent.click(within(row("r-1")).getByRole("checkbox"));
    fireEvent.click(within(row("r-3")).getByRole("checkbox"));
    expect(within(bar).getByText("2 selected")).toBeInTheDocument();
    fireEvent.change(within(bar).getByLabelText("Role for bulk approval"), { target: { value: "admin" } });
    fireEvent.click(approveSelected);

    await waitFor(() => expect(server.callsTo("POST", "/admin/access-requests/bulk-approve")).toHaveLength(1));
    expect(server.callsTo("POST", "/admin/access-requests/bulk-approve")[0].body).toEqual({ ids: ["r-1", "r-3"], role: "admin" });
    expect(await screen.findByText("Approved 2 requests")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("request-row-r-1")).not.toBeInTheDocument();
      expect(screen.queryByTestId("request-row-r-3")).not.toBeInTheDocument();
    });
    expect(row("r-2")).toBeInTheDocument();
  });

  it("select-all then a failed bulk approve brings the rows back", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    const reply = deferred();
    server.on("POST /admin/access-requests/bulk-approve", () => reply.promise);
    fireEvent.click(screen.getByLabelText("Select all requests"));
    fireEvent.click(screen.getByRole("button", { name: "Approve selected" }));
    await waitFor(() => expect(screen.queryByTestId("request-row-r-1")).not.toBeInTheDocument());
    await act(async () => reply.resolve({ status: 500, body: { detail: "boom" } }));
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    expect(await screen.findByText("Bulk approve failed")).toBeInTheDocument();
  });

  it("a 409 (already decided) shows the toast and refetches the queue", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    server.on("POST /admin/access-requests/r-1/approve", () => ({
      status: 409,
      body: { detail: "This access request has already been decided." },
    }));
    const before = server.callsTo("GET", "/admin/access-requests").length;
    fireEvent.click(within(row("r-1")).getByRole("button", { name: "Approve carol@contoso.example" }));
    expect(await screen.findByText("This access request has already been decided.")).toBeInTheDocument();
    expect(screen.getByText("Change not applied")).toBeInTheDocument();
    await waitFor(() => expect(server.callsTo("GET", "/admin/access-requests").length).toBeGreaterThan(before));
  });

  it("shows the decision history in the Approved / All views (URL state)", async () => {
    renderTab("/admin/user-management?tab=requests");
    await waitFor(() => expect(row("r-1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("radio", { name: "All decisions" }));
    expect(memoryRouter.params().get("view")).toBe("all");
    expect(await screen.findByText("Decision history")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("old@contoso.example")).toBeInTheDocument());
    expect(server.callsTo("GET", "/admin/access-requests").at(-1)?.query.get("status")).toBe("all");
  });
});

describe("Roles & Permissions tab", () => {
  it("renders the read-only matrix and marks the caller's role from /auth/me", async () => {
    renderTab("/admin/user-management?tab=roles");
    const matrix = await screen.findByTestId("um-roles-matrix");
    const headers = within(matrix).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual(["Permission", "Superadmin (you)", "Admin", "Operator"]);
    const manage = within(matrix).getByText("users:manage").closest("tr") as HTMLElement;
    const cells = within(manage).getAllByRole("cell");
    expect(within(cells[0]).getByLabelText("Granted")).toBeInTheDocument();
    expect(within(cells[1]).getByLabelText("Not granted")).toBeInTheDocument();
    expect(within(cells[2]).getByLabelText("Not granted")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /grant|revoke/i })).not.toBeInTheDocument();
  });

  it("flags drift between the table and the live /auth/me permissions", () => {
    expect(permissionDrift(ME)).toEqual([]);
    expect(permissionDrift({ ...ME, permissions: ME.permissions.filter((p) => p !== "branding:write") })).toEqual([
      "branding:write",
    ]);
  });
});

describe("Audit Trail tab", () => {
  it("shows only auth events and filters by event through the URL", async () => {
    renderTab("/admin/user-management?tab=audit");
    await waitFor(() => expect(screen.getByText("Sign-in denied")).toBeInTheDocument());
    expect(screen.getAllByText("Role changed").length).toBeGreaterThan(0);
    expect(screen.queryByText("query")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Filters/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign-in denied" }));
    expect(memoryRouter.params().get("action")).toBe("login_denied");
    await waitFor(() => expect(screen.queryByText("alice@contoso.example")).not.toBeInTheDocument());
  });
});

describe("user-management-api client", () => {
  it("bounds and allow-lists the users query", () => {
    const qs = new URLSearchParams(
      usersQuery({
        search: `  ${"x".repeat(150)}  `,
        role: "root" as never,
        status: "active",
        sort: "password" as never,
        order: "asc",
        limit: 5000,
        offset: -3,
      }),
    );
    expect(qs.get("search")).toHaveLength(100);
    expect(qs.get("role")).toBeNull();
    expect(qs.get("status")).toBe("active");
    expect(qs.get("sort")).toBeNull();
    expect(qs.get("limit")).toBe("200");
    expect(qs.get("offset")).toBe("0");
    expect(new URLSearchParams(usersQuery({ search: "a\u0000b", limit: 10, offset: 0 })).get("search")).toBeNull();
  });

  it("surfaces problem+json detail (incl. 422 lists) as ApiError", async () => {
    server = setupUserManagement();
    server.on("POST /admin/access-requests/r-1/reject", () => ({
      status: 422,
      body: { detail: [{ msg: "String should have at least 1 character" }] },
    }));
    await expect(userManagementApi.reject("r-1", "")).rejects.toMatchObject({
      status: 422,
      message: "String should have at least 1 character",
    });
    expect(server.calls.at(-1)?.headers["X-Requested-With"]).toBe("askdiq");
  });
});
