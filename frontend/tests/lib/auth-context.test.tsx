import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, RequirePermission, SESSION_MARKER, useAuth, usePermission } from "@/lib/auth-context";
import { gateDecision, type GateDecision } from "@/lib/auth-gate";
import { parseMe } from "@/lib/auth-api";
import type { Permission, Role, UserStatus } from "@/lib/permissions";
import { makeMe } from "../helpers/auth";

const redirectTo = vi.hoisted(() => vi.fn());
vi.mock("@/lib/navigation", () => ({ redirectTo }));

function json(status: number, body: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  redirectTo.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

function Probe({ permission = "users:manage" as Permission }) {
  const auth = useAuth();
  const allowed = usePermission(permission);
  return (
    <div>
      <span data-testid="state">
        {auth.isLoading ? "loading" : `${auth.status ?? "signed-out"}|${auth.role ?? "none"}|${auth.token ?? "no-token"}`}
      </span>
      <span data-testid="name">{auth.user?.full_name ?? ""}</span>
      <span data-testid="contact">{auth.accessContactEmail ?? ""}</span>
      <span data-testid="allowed">{String(allowed)}</span>
      <RequirePermission permission={permission} fallback={<span>hidden</span>}>
        <button type="button">Guarded action</button>
      </RequirePermission>
      <button type="button" onClick={() => void auth.logout()}>
        Sign out
      </button>
    </div>
  );
}

describe("AuthProvider: real state from GET /auth/me (F1.1.4)", () => {
  it.each([
    ["superadmin", "active", true],
    ["admin", "active", false],
    ["operator", "active", false],
    [null, "pending", false],
    [null, "rejected", false],
    ["admin", "suspended", false],
  ] as [Role | null, UserStatus, boolean][])("role %s / %s renders with users:manage=%s", async (role, status, allowed) => {
    fetchMock.mockResolvedValue(json(200, makeMe(role, status)));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("state")).toHaveTextContent("loading");
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent(`${status}|${role ?? "none"}|${SESSION_MARKER}`));
    expect(screen.getByTestId("name")).toHaveTextContent("Jane Doe");
    expect(screen.getByTestId("contact")).toHaveTextContent("it-admin@contoso.example");
    expect(screen.getByTestId("allowed")).toHaveTextContent(String(allowed));
    expect(screen.queryByRole("button", { name: "Guarded action" }) !== null).toBe(allowed);
    expect(fetchMock).toHaveBeenCalledWith("/api/proxy/auth/me", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("treats 401 as signed out (no token, no permissions)", async () => {
    fetchMock.mockResolvedValue(json(401, { detail: "Not authenticated." }));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("signed-out|none|no-token"));
    expect(screen.getByText("hidden")).toBeInTheDocument();
  });

  it("never exposes the session cookie: token is an opaque marker", async () => {
    fetchMock.mockResolvedValue(json(200, makeMe("admin")));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent(`active|admin|${SESSION_MARKER}`));
  });

  it("logout calls POST /auth/logout, then leaves for /login", async () => {
    fetchMock.mockResolvedValueOnce(json(200, makeMe("admin"))).mockResolvedValueOnce(json(204, null));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("active"));
    await act(async () => screen.getByRole("button", { name: "Sign out" }).click());
    await waitFor(() => expect(redirectTo).toHaveBeenCalledWith("/login"));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/proxy/auth/logout");
    expect(init.method).toBe("POST");
    expect(fetchMock.mock.invocationCallOrder[1]).toBeLessThan(redirectTo.mock.invocationCallOrder[0]);
  });

  it("still leaves for /login when the logout call fails", async () => {
    fetchMock.mockResolvedValueOnce(json(200, makeMe("admin"))).mockRejectedValueOnce(new TypeError("offline"));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("active"));
    await act(async () => screen.getByRole("button", { name: "Sign out" }).click());
    await waitFor(() => expect(redirectTo).toHaveBeenCalledWith("/login"));
  });

  it("outside a provider useAuth is signed out and makes no request", () => {
    render(<Probe />);
    expect(screen.getByTestId("state")).toHaveTextContent("signed-out|none|no-token");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("parseMe drops unknown permissions and rejects malformed payloads", () => {
    const me = parseMe({ ...makeMe("admin"), permissions: ["dashboard:read", "root:everything"] });
    expect(me?.permissions).toEqual(["dashboard:read"]);
    expect(parseMe({ user: {}, status: "active" })).toBeNull();
    expect(parseMe({ ...makeMe("admin"), status: "god" })).toBeNull();
    expect(parseMe({ ...makeMe("admin"), role: "owner" })?.role).toBeNull();
  });
});

describe("route gate: every status x route pair (F1.2.3)", () => {
  const ROUTES = ["/chat", "/chat/s1", "/admin", "/admin/feedback", "/documents", "/admin/user-management"];
  const CASES: [string, ReturnType<typeof makeMe> | null, (route: string) => GateDecision][] = [
    ["signed out", null, () => "login"],
    ["pending", makeMe(null, "pending"), (r) => (r.startsWith("/chat") ? "app" : "access-required")],
    ["rejected", makeMe(null, "rejected"), (r) => (r.startsWith("/chat") ? "app" : "access-required")],
    ["active without a role", makeMe(null, "active"), (r) => (r.startsWith("/chat") ? "app" : "access-required")],
    ["suspended", makeMe("admin", "suspended"), () => "suspended"],
    ["operator", makeMe("operator"), () => "app"],
    ["admin", makeMe("admin"), () => "app"],
    ["superadmin", makeMe("superadmin"), () => "app"],
  ];

  for (const [label, me, expected] of CASES) {
    it.each(ROUTES)(`${label} on %s`, (route) => {
      expect(gateDecision(me, route)).toBe(expected(route));
    });
  }

  it("is loading until /auth/me answers", () => {
    expect(gateDecision(null, "/admin", true)).toBe("loading");
    expect(gateDecision(makeMe("admin"), "/admin", true)).toBe("app");
  });

  it("does not treat /chatter as Chat", () => {
    expect(gateDecision(makeMe(null, "pending"), "/chatter")).toBe("access-required");
  });
});
