import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { queryCache, serializeKey } from "@/lib/query-cache";
import { feedbackKeys } from "@/lib/feedback-api";
import { governanceKeys } from "@/lib/governance-api";
import { usageKeys, useUsageSummary } from "@/lib/use-usage-data";
import { prefetchKey, prefetchParams } from "@/components/admin/dashboard/DashboardKpis";
import type { UsageSummary } from "@/lib/types";

/** perf/dashboard-load-speed: shared stale-while-revalidate cache and hover prefetch reuse. */

const SUMMARY = { audience: "overview", as_of: "2026-10-16T00:00:00Z" } as unknown as UsageSummary;

afterEach(() => {
  queryCache.clear();
  vi.restoreAllMocks();
});

describe("useUsageSummary", () => {
  it("shows the skeleton state on a cold load, then serves a revisit from the shared cache", async () => {
    const fetchSummary = vi.spyOn(api, "getUsageSummary").mockResolvedValue(SUMMARY);

    const first = renderHook(() => useUsageSummary("session", "overview"));
    expect(first.result.current.isLoading).toBe(true);
    expect(first.result.current.summary).toBeNull();
    await waitFor(() => expect(first.result.current.summary).toEqual(SUMMARY));
    expect(first.result.current.isLoading).toBe(false);
    first.unmount();

    // Navigating back: the cached summary renders at once, no skeleton, no refetch while fresh.
    const second = renderHook(() => useUsageSummary("session", "overview"));
    expect(second.result.current.isLoading).toBe(false);
    expect(second.result.current.summary).toEqual(SUMMARY);
    expect(fetchSummary).toHaveBeenCalledTimes(1);
  });

  it("stays in the loading state until the session is known", () => {
    const fetchSummary = vi.spyOn(api, "getUsageSummary").mockResolvedValue(SUMMARY);
    const { result } = renderHook(() => useUsageSummary(null, "overview"));
    expect(result.current.isLoading).toBe(true);
    expect(fetchSummary).not.toHaveBeenCalled();
  });

  it("surfaces a failure as a message", async () => {
    vi.spyOn(api, "getUsageSummary").mockRejectedValue(new Error("Backend down"));
    const { result } = renderHook(() => useUsageSummary("session", "users"));
    await waitFor(() => expect(result.current.error).toBe("Backend down"));
    expect(result.current.isLoading).toBe(false);
  });
});

describe("dashboard KPI intent prefetch", () => {
  it("warms the target page's own cache entry when it reads the same endpoint", () => {
    expect(serializeKey(prefetchKey("/usage/summary", "users"))).toBe(serializeKey(usageKeys.summary("users")));
    expect(serializeKey(prefetchKey("/governance/monitoring", "overview"))).toBe(
      serializeKey(governanceKeys.monitoring("overview")),
    );
    expect(serializeKey(prefetchKey("/feedback/analytics", "testing"))).toBe(
      serializeKey(feedbackKeys.analytics("testing")),
    );
  });

  it("keys and fetches by the date range, so a hover warms the ranged page entry", () => {
    const days = { from: "2026-10-01", to: "2026-10-07" };
    expect(serializeKey(prefetchKey("/usage/summary", "users", days))).toBe(
      serializeKey(usageKeys.summary("users", days)),
    );
    expect(serializeKey(usageKeys.summary("users", days))).not.toBe(serializeKey(usageKeys.summary("users")));
    expect(serializeKey(prefetchKey("/feedback/analytics", "users", days))).toBe(
      serializeKey(feedbackKeys.analytics("users", days)),
    );
    expect(serializeKey(feedbackKeys.analytics("users", days))).not.toBe(serializeKey(feedbackKeys.analytics("users")));
    expect(prefetchParams("/usage/summary", days)).toEqual({ from_date: "2026-10-01", to_date: "2026-10-07" });
    expect(prefetchParams("/feedback/analytics", days)).toEqual({ from: "2026-10-01", to: "2026-10-07" });
    expect(prefetchParams("/governance/monitoring", days)).toEqual({});
  });

  it("sends the range to the summary endpoint", async () => {
    const fetchSummary = vi.spyOn(api, "getUsageSummary").mockResolvedValue(SUMMARY);
    renderHook(() => useUsageSummary("session", "overview", { from: "2026-10-01", to: "2026-10-07" }));
    await waitFor(() =>
      expect(fetchSummary).toHaveBeenCalledWith(
        "session",
        { audience: "overview", from_date: "2026-10-01", to_date: "2026-10-07" },
        expect.anything(),
      ),
    );
  });

  it("keeps a private key for pages with their own query shape", () => {
    expect(serializeKey(prefetchKey("/users", "overview"))).toBe("prefetch//users/overview");
  });
});
