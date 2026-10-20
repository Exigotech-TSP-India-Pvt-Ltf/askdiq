import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryCache } from "@/lib/query-cache";
import {
  MAX_ENTRY_CHARS,
  MAX_RESTORE_AGE_MS,
  PERSIST_STORAGE_KEY,
  clearPersistedQueryCache,
  restoreQueryCache,
  serializeQueryCache,
  startQueryPersistence,
} from "@/lib/query-persist";
import { LIVE_DATA_PREFIXES, createInvalidator, prefixesForScopes } from "@/lib/live-updates";

/** Instant console: persisted query cache, live-update scope mapping and throttling. */

function seeded(entries: Array<[string, unknown]>): QueryCache {
  const cache = new QueryCache();
  for (const [key, data] of entries) cache.setData(key, data);
  return cache;
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("QueryCache.hydrate / persistable", () => {
  it("seeds only missing data and keeps updatedAt", () => {
    const cache = new QueryCache();
    cache.hydrate("a", { n: 1 }, 1234);
    expect(cache.getState("a")).toMatchObject({ data: { n: 1 }, updatedAt: 1234 });
    cache.setData("b", { n: 2 });
    cache.hydrate("b", { n: 99 }, 1);
    expect(cache.getData("b")).toEqual({ n: 2 });
  });

  it("notifies onAnyChange listeners with the key", () => {
    const cache = new QueryCache();
    const seen: string[] = [];
    const stop = cache.onAnyChange((k) => seen.push(k));
    cache.setData(["admin/x", 1], 1);
    stop();
    cache.setData("y", 2);
    expect(seen).toEqual(["admin/x/1"]);
  });

  it("lists entries with data and no error", () => {
    const cache = seeded([["k1", 1]]);
    expect(cache.persistable().map(([k]) => k)).toEqual(["k1"]);
  });
});

describe("query-persist", () => {
  it("round-trips entries for the same owner", () => {
    const source = seeded([
      ["admin/usage/summary/overview", { total: 3 }],
      ["metrics/evaluation/live/users//", { pass_rate: 0.9 }],
    ]);
    window.sessionStorage.setItem(PERSIST_STORAGE_KEY, serializeQueryCache("u1", source));
    const target = new QueryCache();
    expect(restoreQueryCache("u1", target)).toBe(2);
    expect(target.getData("admin/usage/summary/overview")).toEqual({ total: 3 });
  });

  it("never restores another user's data, and drops it", () => {
    window.sessionStorage.setItem(PERSIST_STORAGE_KEY, serializeQueryCache("u1", seeded([["k", 1]])));
    const target = new QueryCache();
    expect(restoreQueryCache("u2", target)).toBe(0);
    expect(target.getData("k")).toBeUndefined();
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toBeNull();
  });

  it("skips denied keys, oversized and too old entries", () => {
    const cache = seeded([
      ["auth/me", { email: "x" }],
      ["settings/branding", { name: "b" }],
      ["big", "x".repeat(MAX_ENTRY_CHARS + 10)],
      ["ok", 1],
    ]);
    const raw = serializeQueryCache("u1", cache);
    expect(raw).not.toContain("auth/me");
    expect(raw).not.toContain("settings/branding");
    expect(raw).not.toContain('"big"');
    window.sessionStorage.setItem(PERSIST_STORAGE_KEY, raw);
    const later = Date.now() + MAX_RESTORE_AGE_MS + 1000;
    expect(restoreQueryCache("u1", new QueryCache(), later)).toBe(0);
    expect(restoreQueryCache("u1", new QueryCache())).toBe(1);
  });

  it("tolerates garbage in storage", () => {
    window.sessionStorage.setItem(PERSIST_STORAGE_KEY, "{not json");
    expect(restoreQueryCache("u1", new QueryCache())).toBe(0);
    clearPersistedQueryCache();
    expect(window.sessionStorage.getItem(PERSIST_STORAGE_KEY)).toBeNull();
  });

  it("writes after changes (debounced) and on pagehide", () => {
    vi.useFakeTimers();
    const cache = new QueryCache();
    const stop = startQueryPersistence("u1", cache);
    cache.setData("k", { v: 1 });
    window.dispatchEvent(new Event("pagehide"));
    const stored = JSON.parse(window.sessionStorage.getItem(PERSIST_STORAGE_KEY) ?? "{}");
    expect(stored.owner).toBe("u1");
    expect(stored.entries.map((e: unknown[]) => e[0])).toEqual(["k"]);
    stop();
  });
});

describe("live updates", () => {
  it("maps scopes to the query prefixes they refresh", () => {
    expect(prefixesForScopes(["alerts"])).toEqual(["admin/alerts"]);
    expect(prefixesForScopes(["feedback.analytics"])).toEqual(["admin/feedback"]);
    expect(prefixesForScopes(["insights.summary"])).toEqual(["admin/insights"]);
    expect(prefixesForScopes(["usage.audience_counts"])).toEqual(
      expect.arrayContaining(["admin/usage/audience-counts", "admin/usage"]),
    );
    expect(new Set(prefixesForScopes(["data"]))).toEqual(new Set(LIVE_DATA_PREFIXES));
    expect(prefixesForScopes(["unknown.kind"])).toEqual([]);
  });

  it("throttles repeated invalidations of one prefix", () => {
    vi.useFakeTimers();
    const cache = new QueryCache();
    const spy = vi.spyOn(cache, "invalidate");
    const inv = createInvalidator(cache, 1000);
    inv.invalidate(["admin/feedback"]);
    inv.invalidate(["admin/feedback"]);
    inv.invalidate(["admin/feedback"]);
    expect(spy).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(spy).toHaveBeenCalledTimes(2);
    inv.dispose();
  });

  it("an invalidation keeps data on screen and refetches observed keys", async () => {
    const cache = new QueryCache();
    cache.setData("admin/feedback/analytics/overview", { total: 1 });
    const fetcher = vi.fn(async () => ({ total: 2 }));
    const stop = cache.observe("admin/feedback/analytics/overview", {
      fetcher: () => fetcher,
      staleTime: 30_000,
      revalidateOnFocus: false,
    });
    createInvalidator(cache, 0).invalidate(["admin/feedback"]);
    expect(cache.getData("admin/feedback/analytics/overview")).toEqual({ total: 1 });
    await vi.waitFor(() => expect(cache.getData("admin/feedback/analytics/overview")).toEqual({ total: 2 }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    stop();
  });
});
