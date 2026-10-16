/**
 * Fake `/api/proxy` backend for the User Management tests: fixtures shaped
 * exactly like `schemas/user_management.py`, a call log, and per-route
 * overrides (errors, deferred responses for optimistic-update checks).
 */
import { AuthProvider } from "@/lib/auth-context";
import React from "react";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { queryCache } from "@/lib/query-cache";
import type {
  AccessRequest,
  AuditLogEntry,
  Me,
  UserAccount,
  UserStats,
} from "@/lib/user-management-api";
import { memoryRouter } from "./memory-router";

export const NOW = "2026-10-15T09:00:00Z";

export function user(partial: Partial<UserAccount> & Pick<UserAccount, "id" | "email" | "name">): UserAccount {
  return {
    role: "operator",
    status: "active",
    last_login_at: "2026-10-14T08:00:00Z",
    created_at: "2026-09-01T08:00:00Z",
    approved_by_email: "root@contoso.example",
    approved_at: "2026-09-02T08:00:00Z",
    version: 3,
    ...partial,
  };
}

export function request(partial: Partial<AccessRequest> & Pick<AccessRequest, "id" | "user_email">): AccessRequest {
  return {
    user_id: `user-${partial.id}`,
    user_name: partial.user_email.split("@")[0],
    user_status: "pending",
    requested_role: "operator",
    justification: "Need dashboards",
    status: "pending",
    created_at: "2026-10-14T08:00:00Z",
    decided_at: null,
    decided_by_email: null,
    decision_note: null,
    waiting_seconds: 7200,
    ...partial,
  };
}

export const ME: Me = {
  user: { id: "me-1", email: "root@contoso.example", name: "Root Admin" },
  role: "superadmin",
  status: "active",
  permissions: [
    "dashboard:read",
    "documents:write",
    "feedback:triage",
    "feedback:export",
    "eval:run",
    "testip:write",
    "usage:export",
    "users:manage",
    "audit:auth:read",
    "branding:write",
    "taxonomy:write",
  ],
  version: 1,
  access_contact_email: "it@contoso.example",
};

export function fixtures() {
  const users: UserAccount[] = [
    user({ id: "me-1", email: "root@contoso.example", name: "Root Admin", role: "superadmin" }),
    user({ id: "u-2", email: "alice@contoso.example", name: "Alice Admin", role: "admin" }),
    user({ id: "u-3", email: "bob@contoso.example", name: "Bob Operator", status: "suspended" }),
    user({ id: "u-4", email: "carol@contoso.example", name: "Carol New", role: null, status: "pending" }),
    user({ id: "u-5", email: "sam@contoso.example", name: "Sam Super", role: "superadmin" }),
  ];
  const requests: AccessRequest[] = [
    request({ id: "r-1", user_email: "carol@contoso.example", requested_role: "admin" }),
    request({ id: "r-2", user_email: "dave@contoso.example" }),
    request({ id: "r-3", user_email: "erin@contoso.example", justification: "Evaluation runs" }),
    request({
      id: "r-9",
      user_email: "old@contoso.example",
      status: "approved",
      decided_at: "2026-10-01T10:00:00Z",
      decided_by_email: "root@contoso.example",
      waiting_seconds: null,
    }),
  ];
  const stats: UserStats = {
    days: 30,
    kpis: { total: 5, active: 3, pending: 1, rejected: 0, suspended: 1, open_requests: 3, logins_24h: 7 },
    logins_per_day: Array.from({ length: 30 }, (_, i) => ({
      date: new Date(Date.UTC(2026, 8, 16 + i)).toISOString().slice(0, 10),
      count: i % 5,
    })),
    funnel: [
      { stage: "signed_in", count: 12 },
      { stage: "requested", count: 8 },
      { stage: "approved", count: 5 },
      { stage: "active", count: 3 },
    ],
    role_mix: [
      { role: "superadmin", count: 2 },
      { role: "admin", count: 1 },
      { role: "operator", count: 0 },
    ],
    time_to_approve: [
      { bucket: "<1h", count: 2 },
      { bucket: "1-4h", count: 1 },
    ],
  };
  const audit: AuditLogEntry[] = [
    {
      id: "a-1",
      request_id: "req-aaaaaaaaaaaa1",
      actor: "root@contoso.example",
      action: "role_changed",
      detail: { target_user_id: "u-2", target_email: "a***@contoso.example", from_role: "operator", to_role: "admin" },
      created_at: "2026-10-14T10:00:00Z",
      ip_address: "10.0.0.1",
    },
    {
      id: "a-2",
      request_id: "req-aaaaaaaaaaaa2",
      actor: "alice@contoso.example",
      action: "login_success",
      detail: {},
      created_at: "2026-10-14T09:00:00Z",
      ip_address: "10.0.0.2",
    },
    {
      id: "a-3",
      request_id: "req-aaaaaaaaaaaa3",
      actor: "anonymous",
      action: "login_denied",
      detail: { reason: "suspended" },
      created_at: "2026-10-13T09:00:00Z",
      ip_address: "10.0.0.3",
    },
    {
      id: "a-4",
      request_id: "req-aaaaaaaaaaaa4",
      actor: "system",
      action: "query",
      detail: {},
      created_at: "2026-10-13T08:00:00Z",
      ip_address: null,
    },
  ];
  return { users, requests, stats, audit, me: ME };
}

export interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
}

type Reply = { status: number; body?: unknown };
type Handler = (call: Call) => Reply | Promise<Reply>;

function respond({ status, body }: Reply) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  } as unknown as Response;
}

/** A response that resolves when the test says so. */
export function deferred() {
  let resolve: (reply: Reply) => void = () => undefined;
  const promise = new Promise<Reply>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

export function createServer(data = fixtures()) {
  const calls: Call[] = [];
  const overrides = new Map<string, Handler>();

  const route = (call: Call): Reply => {
    const { method, path, query } = call;
    if (method === "GET" && path === "/auth/me") return { status: 200, body: data.me };
    if (method === "GET" && path === "/admin/users-accounts/stats") return { status: 200, body: data.stats };
    if (method === "GET" && path === "/governance/audit-log") return { status: 200, body: data.audit };
    if (method === "GET" && path === "/admin/users-accounts") {
      const role = query.get("role");
      const status = query.get("status");
      const search = query.get("search")?.toLowerCase();
      const items = data.users.filter(
        (u) =>
          (!role || u.role === role) &&
          (!status || u.status === status) &&
          (!search || u.email.includes(search) || u.name.toLowerCase().includes(search)),
      );
      const limit = Number(query.get("limit") ?? 50);
      const offset = Number(query.get("offset") ?? 0);
      return { status: 200, body: { items: items.slice(offset, offset + limit), total: items.length, limit, offset } };
    }
    const userMatch = path.match(/^\/admin\/users-accounts\/([^/]+)$/);
    if (userMatch && method === "GET") {
      const u = data.users.find((x) => x.id === decodeURIComponent(userMatch[1]));
      if (!u) return { status: 404, body: { detail: "User not found." } };
      return {
        status: 200,
        body: {
          ...u,
          active_sessions: [
            {
              id: "s-1",
              created_at: "2026-10-14T08:00:00Z",
              last_seen_at: "2026-10-14T09:00:00Z",
              expires_at: "2026-10-15T08:00:00Z",
              ip_address: "10.1.1.1",
              user_agent: "Firefox on Windows",
            },
          ],
        },
      };
    }
    if (userMatch && method === "PATCH") {
      const u = data.users.find((x) => x.id === decodeURIComponent(userMatch[1]));
      if (!u) return { status: 404, body: { detail: "User not found." } };
      const body = call.body as { version: number; role?: UserAccount["role"]; status?: UserAccount["status"] };
      Object.assign(u, { role: body.role ?? u.role, status: body.status ?? u.status, version: u.version + 1 });
      return { status: 200, body: u };
    }
    if (method === "POST" && /\/revoke-sessions$/.test(path)) return { status: 200, body: { revoked: 2 } };
    if (method === "POST" && path === "/admin/users-accounts/invite") {
      const body = call.body as { email: string; role: UserAccount["role"]; name?: string };
      if (data.users.some((u) => u.email === body.email)) {
        return { status: 409, body: { detail: "A user with this email already exists." } };
      }
      const created = user({ id: `u-${data.users.length + 1}`, email: body.email, name: body.name ?? "", role: body.role });
      data.users.push(created);
      return { status: 201, body: created };
    }
    if (method === "GET" && path === "/admin/access-requests") {
      const view = query.get("status") ?? "pending";
      const items = data.requests.filter((r) => view === "all" || r.status === view);
      return { status: 200, body: { items, total: items.length, limit: 50, offset: 0 } };
    }
    const decide = path.match(/^\/admin\/access-requests\/([^/]+)\/(approve|reject)$/);
    if (decide && method === "POST") {
      const r = data.requests.find((x) => x.id === decide[1]);
      if (!r) return { status: 404, body: { detail: "Access request not found." } };
      r.status = decide[2] === "approve" ? "approved" : "rejected";
      r.decided_by_email = ME.user.email;
      r.decided_at = NOW;
      if (decide[2] === "reject") r.decision_note = (call.body as { note: string }).note;
      return { status: 200, body: r };
    }
    if (method === "POST" && path === "/admin/access-requests/bulk-approve") {
      const { ids } = call.body as { ids: string[] };
      for (const r of data.requests) if (ids.includes(r.id)) r.status = "approved";
      return { status: 200, body: { approved: ids.length, ids } };
    }
    return { status: 404, body: { detail: `No route ${method} ${path}` } };
  };

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const call: Call = {
      method: init?.method ?? "GET",
      path: url.pathname.replace(/^\/api\/proxy/, ""),
      query: url.searchParams,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const override = overrides.get(`${call.method} ${call.path}`);
    return respond(await (override ? override(call) : route(call)));
  });

  return {
    data,
    calls,
    fetchMock,
    /** Replace one route, e.g. `on("PATCH /admin/users-accounts/u-2", () => ({ status: 409, body }))`. */
    on(key: string, handler: Handler) {
      overrides.set(key, handler);
    },
    callsTo(method: string, pathPrefix: string) {
      return calls.filter((c) => c.method === method && c.path.startsWith(pathPrefix));
    },
  };
}

export type FakeServer = ReturnType<typeof createServer>;

/** Fresh cache, fake fetch and URL; returns the server. */
export function setupUserManagement(url = "/admin/user-management", data = fixtures()): FakeServer {
  queryCache.clear();
  const server = createServer(data);
  vi.stubGlobal("fetch", server.fetchMock);
  memoryRouter.reset(url);
  return server;
}

export function renderWithProviders(ui: React.ReactElement) {
  return render(
    <AuthProvider>
      <MotionProvider>
        <ToastProvider>{ui}</ToastProvider>
      </MotionProvider>
    </AuthProvider>,
  );
}
