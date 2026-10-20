import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import type { AuthMe } from "@/lib/auth-api";
import { ApiError } from "@/lib/api";
import { QueryCache, queryCache, useApiQuery } from "@/lib/query-cache";
import {
  PERSIST_STORAGE_KEY,
  cacheScope,
  restoreQueryCache,
  serializeQueryCache,
  startQueryPersistence,
} from "@/lib/query-persist";

const fetchMe = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/auth-api")>()),
  fetchMe,
}));

// Imported after the mock so the provider uses it.
const { AuthProvider, useAuth } = await import("@/lib/auth-context");

/** Cache scoping by access: role / permission changes never show older data. */

const ADMIN: AuthMe = {
  user: { id: "u1", email: "a@x.test", name: "A" },
  role: "admin",
  status: "active",
  permissions: ["dashboard:read", "documents:write", "usage:export"],
  version: 1,
  access_contact_email: null,
};
const OPERATOR: AuthMe = { ...ADMIN, role: "operator", permissions: ["dashboard:read"], version: 2 };

function persisted(owner: string, entries: Array<[string, unknown]>): void {
  const source = new QueryCache();
  for (const [key, data] of entries) source.setData(key, data);
  window.sessionStorage.setItem(PERSIST_STORAGE_KEY, serializeQueryCache(owner, source));
}

beforeEach(() => {
  window.sessionStorage.clear();
  queryCache.clear();
  fetchMe.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  queryCache.clear();
});

describe("cacheScope", () => {
  it("covers id, role, status and the permission set (order-insensitive)", () => {
    const shuffled = { ...ADMIN, permissions: [...ADMIN.permissions].reverse() };
    expect(cacheScope(shuffled)).toBe(cacheScope(ADMIN));
    expect(cacheScope(OPERATOR)).not.toBe(cacheScope(ADMIN));
    expect(cacheScope({ ...ADMIN, status: "suspended" })).not.toBe(cacheScope(ADMIN));
    expect(cacheScope({ ...ADMIN, permissions: [...ADMIN.permissions, "users:manage"] })).not.toBe(
      cacheScope(ADMIN),
    );
    expect(cacheScope(null)).toBe("");
  });
});

describe("restore by scope", () => {
  it("the same user with the same permissions still restores (instant reload)", () => {
    persisted(cacheScope(ADMIN), [["admin/usage/summary", { total: 3 }]]);
    const target = new QueryCache();
    expect(restoreQueryCache(cacheScope({ ...ADMIN, version: 9 } as AuthMe), target)).toBe(1);
    expect(target.getData("admin/usage/summary")).toEqual({ total: 3 });
  });

  it("a different permission set does not restore, and drops the stored copy", () => {
    persisted(cacheScope(ADMIN), [["admin/usage/summary", { total: 3 }]]);
    const target = new QueryCache();
    const fewer = { ...ADMIN, permissions: ["dashboard:read", "documents:write"] } as AuthMe;
    expect(restoreQueryCache(cacheScope(fewer), target)).toBe(0);
    expect(target.getData("admin/usage/summary")).toBeUndefined();
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toBeNull();
  });

  it("a cache stored under the old id-only owner is not restored", () => {
    persisted("u1", [["k", 1]]);
    expect(restoreQueryCache(cacheScope(ADMIN), new QueryCache())).toBe(0);
  });
});

describe("role change while signed in", () => {
  function Probe({ onAuth }: { onAuth: (auth: ReturnType<typeof useAuth>) => void }) {
    onAuth(useAuth());
    return null;
  }

  it("discards the persisted and in-memory data and stops persisting under the old scope", async () => {
    vi.useFakeTimers();
    let auth: ReturnType<typeof useAuth> | null = null;
    render(
      <AuthProvider initialMe={ADMIN}>
        <Probe onAuth={(a) => (auth = a)} />
      </AuthProvider>,
    );
    const stop = startQueryPersistence(cacheScope(ADMIN), queryCache);
    queryCache.setData("admin/usage/summary", { cost: 42 });
    window.dispatchEvent(new Event("pagehide"));
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toContain("cost");

    fetchMe.mockResolvedValue(OPERATOR);
    await act(async () => {
      await auth!.refresh();
    });

    expect(queryCache.getData("admin/usage/summary")).toBeUndefined();
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toBeNull();
    // The old scope's persistence is stopped: nothing is written back.
    window.dispatchEvent(new Event("pagehide"));
    vi.advanceTimersByTime(5_000);
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toBeNull();
    expect(auth!.role).toBe("operator");
    stop();
  });

  it("keeps the cache when /auth/me returns the same access", async () => {
    let auth: ReturnType<typeof useAuth> | null = null;
    render(
      <AuthProvider initialMe={ADMIN}>
        <Probe onAuth={(a) => (auth = a)} />
      </AuthProvider>,
    );
    queryCache.setData("admin/usage/summary", { cost: 42 });
    fetchMe.mockResolvedValue({ ...ADMIN, permissions: [...ADMIN.permissions].reverse() });
    await act(async () => {
      await auth!.refresh();
    });
    expect(queryCache.getData("admin/usage/summary")).toEqual({ cost: 42 });
  });

  it("a mounted view loses its data at once (also kept placeholder data) and refetches", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ secret: 1 }).mockRejectedValue(new ApiError("Forbidden", 403));
    const { result } = renderHook(() => useApiQuery("admin/secret", fetcher, { keepPreviousData: true }));
    await waitFor(() => expect(result.current.data).toEqual({ secret: 1 }));

    act(() => queryCache.reset());
    expect(result.current.data).toBeUndefined();
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((result.current.error as ApiError | undefined)?.status).toBe(403));
    expect(result.current.data).toBeUndefined();
  });
});

describe("401 / 403 eviction", () => {
  it.each([401, 403])("a %s evicts the key from memory and from storage at once", async (status) => {
    const cache = new QueryCache();
    const stop = startQueryPersistence(cacheScope(ADMIN), cache);
    cache.setData("admin/usage/summary", { cost: 42 });
    cache.setData("admin/feedback", { ok: true });
    window.dispatchEvent(new Event("pagehide"));
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toContain("admin/usage/summary");

    await cache
      .fetch("admin/usage/summary", () => Promise.reject(new ApiError("refused", status)), { force: true })
      .catch(() => undefined);

    expect(cache.getData("admin/usage/summary")).toBeUndefined();
    const stored = window.sessionStorage.getItem(PERSIST_STORAGE_KEY) ?? "";
    expect(stored).not.toContain("admin/usage/summary");
    expect(stored).toContain("admin/feedback");
    stop();
  });

  it("other failures keep showing the last data", async () => {
    const cache = new QueryCache();
    cache.setData("k", { v: 1 });
    await cache.fetch("k", () => Promise.reject(new ApiError("boom", 500)), { force: true }).catch(() => undefined);
    expect(cache.getData("k")).toEqual({ v: 1 });
  });
});
