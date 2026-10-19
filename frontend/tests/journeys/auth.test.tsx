/**
 * Phase F1 auth journey (planchanges4 F1.5): `/auth/me` is mocked through each
 * state. A new user signs in, uses Chat, meets Access Required on a dashboard
 * route, requests access, is approved (the 15 s poll picks it up) and lands on
 * the dashboard without signing in again. A suspended user sees the suspended
 * page. Signed-out visitors are sent to /login with `next`.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { AuthProvider } from "@/lib/auth-context";
import { queryCache } from "@/lib/query-cache";
import type { AuthMe, AccessRequest } from "@/lib/auth-api";
import { ACCESS_POLL_INTERVAL_MS } from "@/components/auth/AccessRequiredView";
import AppLayout from "@/app/(app)/layout";
import { makeMe } from "../helpers/auth";

const nav = vi.hoisted(() => ({ pathname: "/chat", replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: nav.replace, push: nav.push, prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
const redirectTo = vi.hoisted(() => vi.fn());
vi.mock("@/lib/navigation", () => ({ redirectTo }));

interface Backend {
  me: AuthMe | null;
  request: AccessRequest | null;
  posted: unknown[];
}

let backend: Backend;
let fetchMock: ReturnType<typeof vi.fn>;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function route(url: string, init?: RequestInit): Response {
  const path = url.replace("/api/proxy", "");
  if (path === "/auth/me") return backend.me ? json(200, backend.me) : json(401, { detail: "Not authenticated." });
  if (path === "/auth/access-request" && init?.method === "POST") {
    backend.posted.push(JSON.parse(String(init.body)));
    backend.request = {
      id: "r-1",
      status: "pending",
      requested_role: "operator",
      justification: "Weekly reporting",
      created_at: "2026-10-15T00:00:00Z",
      decided_at: null,
      decision_note: null,
    };
    return json(201, backend.request);
  }
  if (path === "/auth/access-request") {
    return json(200, {
      user_status: backend.me?.status,
      role: backend.me?.role ?? null,
      request: backend.request,
      access_contact_email: "it-admin@contoso.example",
    });
  }
  if (path.startsWith("/sessions")) return json(200, []);
  return json(200, {});
}

function renderApp(page: React.ReactNode = <h1>Dashboard page</h1>) {
  return render(
    <MotionProvider>
      <ToastProvider>
        <AuthProvider>
          <AppLayout>{page}</AppLayout>
        </AuthProvider>
      </ToastProvider>
    </MotionProvider>,
  );
}

let visibility: DocumentVisibilityState = "visible";

beforeEach(() => {
  backend = { me: makeMe(null, "pending"), request: null, posted: [] };
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => route(url, init));
  vi.stubGlobal("fetch", fetchMock);
  nav.pathname = "/chat";
  nav.replace.mockClear();
  queryCache.clear();
  window.localStorage.clear();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const meCalls = () => fetchMock.mock.calls.filter(([url]) => url === "/api/proxy/auth/me").length;

describe("Auth journey (F1.5)", () => {
  it("new user: Chat works, dashboards show Access Required, request, approval, dashboard", async () => {

    // 1. Pending user on /chat: the page renders, the sidebar offers Chat only.
    const { rerender } = renderApp(<h1>Chat page</h1>);
    expect(await screen.findByRole("heading", { name: "Chat page" })).toBeInTheDocument();
    const mainNav = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(within(mainNav).getAllByRole("link").map((a) => a.textContent)).toEqual(["AskDIQ"]);

    // 2. A dashboard route renders Access Required instead of the page.
    nav.pathname = "/admin";
    rerender(
      <MotionProvider>
        <ToastProvider>
          <AuthProvider>
            <AppLayout>
              <h1>Dashboard page</h1>
            </AppLayout>
          </AuthProvider>
        </ToastProvider>
      </MotionProvider>,
    );
    // (rerender keeps the provider state; the gate re-evaluates the new path)
    expect(await screen.findByRole("heading", { name: "You don't have dashboard access yet" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard page" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "it-admin@contoso.example" })).toHaveAttribute("href", "mailto:it-admin@contoso.example");
    const stepper = screen.getByRole("list", { name: "Access request progress" });
    expect(within(stepper).getAllByRole("listitem").map((li) => li.getAttribute("data-state"))).toEqual([
      "current",
      "upcoming",
      "upcoming",
    ]);

    // 3. Request access (role + justification, max 500 characters).
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "operator" } });
    const reason = screen.getByLabelText(/why do you need access/i);
    fireEvent.change(reason, { target: { value: "x".repeat(600) } });
    expect((reason as HTMLTextAreaElement).value).toHaveLength(500);
    fireEvent.change(reason, { target: { value: "Weekly reporting" } });
    fireEvent.click(screen.getByRole("button", { name: "Request access" }));
    await waitFor(() => expect(backend.posted).toEqual([{ requested_role: "operator", justification: "Weekly reporting" }]));
    expect(await screen.findByText(/is waiting for review/)).toBeInTheDocument();
    await waitFor(() =>
      expect(within(stepper).getAllByRole("listitem").map((li) => li.getAttribute("data-state"))).toEqual([
        "done",
        "current",
        "upcoming",
      ]),
    );

    // 4. Approved in the backend. The status re-check (every 15 s, and at once
    //    when the tab becomes visible again) picks it up; here the user
    //    switches back to the tab.
    backend.me = makeMe("operator");
    const before = meCalls();
    visibility = "hidden";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(meCalls()).toBe(before + 1));

    // 5. The dashboard renders in place, with no re-login and no navigation.
    expect(await screen.findByRole("heading", { name: "Dashboard page" })).toBeInTheDocument();
    expect(screen.getByText("Access approved")).toBeInTheDocument();
    expect(redirectTo).not.toHaveBeenCalled();
    const sidebar = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(within(sidebar).getByRole("link", { name: /visitors/i })).toBeInTheDocument();
  }, 20_000);

  it("re-checks /auth/me every 15 s, pauses while the tab is hidden and resumes when visible", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    nav.pathname = "/admin";
    renderApp();
    await screen.findByRole("heading", { name: "You don't have dashboard access yet" });
    const initial = meCalls();
    await act(async () => {
      vi.advanceTimersByTime(ACCESS_POLL_INTERVAL_MS - 1);
    });
    expect(meCalls()).toBe(initial);
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    await waitFor(() => expect(meCalls()).toBe(initial + 1));
    const start = meCalls();

    visibility = "hidden";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await act(async () => {
      vi.advanceTimersByTime(ACCESS_POLL_INTERVAL_MS * 3);
    });
    expect(meCalls()).toBe(start);

    visibility = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(meCalls()).toBe(start + 1));
  });

  it("rejected user sees the reviewer note and can request again", async () => {
    backend.me = makeMe(null, "rejected");
    backend.request = {
      id: "r-0",
      status: "rejected",
      requested_role: "admin",
      justification: null,
      created_at: "2026-10-01T00:00:00Z",
      decided_at: "2026-10-02T00:00:00Z",
      decision_note: "Please ask your manager first.",
    };
    nav.pathname = "/documents";
    renderApp();
    expect(await screen.findByText("Reviewer note: Please ask your manager first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request access again" })).toBeInTheDocument();
    const stepper = screen.getByRole("list", { name: "Access request progress" });
    expect(within(stepper).getByText("Not approved").closest("li")).toHaveAttribute("data-state", "failed");
  });

  it("suspended user sees the suspended page with the contact email and no shell", async () => {
    backend.me = makeMe("admin", "suspended");
    nav.pathname = "/admin";
    renderApp();
    expect(await screen.findByRole("heading", { name: "Your account is suspended" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "it-admin@contoso.example" })).toHaveAttribute("href", "mailto:it-admin@contoso.example");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard page" })).not.toBeInTheDocument();
  });

  it("suspended user is also kept out of Chat", async () => {
    backend.me = makeMe("admin", "suspended");
    nav.pathname = "/chat";
    renderApp(<h1>Chat page</h1>);
    expect(await screen.findByRole("heading", { name: "Your account is suspended" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Chat page" })).not.toBeInTheDocument();
  });

  it("signed-out visitor is sent to /login with next", async () => {
    backend.me = null;
    nav.pathname = "/admin/feedback";
    window.history.replaceState(null, "", "/admin/feedback?tab=queue");
    renderApp();
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login?next=%2Fadmin%2Ffeedback%3Ftab%3Dqueue"));
    expect(screen.queryByRole("heading", { name: "Dashboard page" })).not.toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });

  it("active superadmin gets the full shell, role badge and access-request notification", async () => {
    backend.me = makeMe("superadmin");
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      url.startsWith("/api/proxy/admin/access-requests") ? json(200, { items: [], total: 2, limit: 1, offset: 0 }) : route(url, init),
    );
    nav.pathname = "/admin";
    renderApp();
    expect(await screen.findByRole("heading", { name: "Dashboard page" })).toBeInTheDocument();
    const sidebar = screen.getAllByRole("navigation", { name: "Main" })[0];
    const um = within(sidebar).getByRole("link", { name: /user management/i });
    await waitFor(() => expect(um).toHaveTextContent("2"));
  });

  it("shows a retry state (not a login loop) when /auth/me fails with a server error", async () => {
    fetchMock.mockImplementation(async () => json(502, { detail: "Backend unreachable" }));
    nav.pathname = "/admin";
    renderApp();
    expect(await screen.findByText("We couldn't check your session")).toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
