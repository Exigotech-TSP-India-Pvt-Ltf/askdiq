/**
 * Test harness for the entity pages: an in-memory router (history with
 * back / forward) for `vi.mock("next/navigation")`, and a fetch mock that
 * routes `/api/proxy/...` requests to per-path handlers.
 *
 * Usage in a test file:
 *   vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());
 */
import React from "react";
import { vi } from "vitest";
import { render } from "@testing-library/react";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { RangeProvider } from "@/lib/range-context";
import { ROLE_PERMISSIONS } from "@/lib/use-can";
import { AuthProvider } from "@/lib/auth-context";
import { makeMe } from "./auth";
import { queryCache } from "@/lib/query-cache";

export { history, currentParams, navigationMock } from "./router-mock";
import { history } from "./router-mock";

export interface MockReply {
  status?: number;
  body?: unknown;
}

export type RouteHandler = (url: URL, init: RequestInit | undefined) => MockReply | Promise<MockReply>;

/** `[method, path regexp, handler]`; the first match wins, anything else is a 404. */
export type Route = [string, RegExp, RouteHandler];

export interface FetchLog {
  method: string;
  url: URL;
}

export function mockFetch(routes: Route[]): FetchLog[] {
  const calls: FetchLog[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, url });
      const path = url.pathname.replace(/^\/api\/proxy/, "");
      const route = routes.find(([m, re]) => m === method && re.test(path));
      const reply = route ? await route[2](url, init) : { status: 404, body: { detail: "Not Found" } };
      const status = reply.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

/** Requests to `path` (no proxy prefix), newest last. */
export function callsTo(calls: readonly FetchLog[], path: string | RegExp, method = "GET"): URL[] {
  return calls
    .filter((c) => c.method === method)
    .map((c) => c.url)
    .filter((u) => {
      const p = u.pathname.replace(/^\/api\/proxy/, "");
      return typeof path === "string" ? p === path : path.test(p);
    });
}

export type Role = keyof typeof ROLE_PERMISSIONS;

let mounted: ReturnType<typeof render> | null = null;

/**
 * Renders a page inside the providers the app shell gives it. A second call
 * unmounts the previous page first (a click-through to another route).
 */
export function renderPage(ui: React.ReactElement, { role = "superadmin", url }: { role?: Role; url?: string } = {}) {
  mounted?.unmount();
  if (url) history.reset(url);
  queryCache.clear();
  mounted = render(
    <AuthProvider initialMe={makeMe(role)}>
      <ToastProvider>
        <RangeProvider>{ui}</RangeProvider>
      </ToastProvider>
    </AuthProvider>,
  );
  return mounted;
}

export function envelope<T>(items: T[], total = items.length) {
  return { items, total, limit: 10, offset: 0 };
}
