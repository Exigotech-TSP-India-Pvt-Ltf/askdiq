"use client";

/**
 * Data prefetch on navigation intent (sidebar hover / focus): warms the first
 * queries of the target page under the exact keys the page uses, so the page
 * renders its cards from cache on the first click. The backend serves these
 * from precomputed snapshots, so a prefetch costs one cheap request each.
 */
import { DEFAULT_STATE, toInsightsFilters } from "@/components/admin/analytics/url-state";
import { viewerTimeZone } from "@/components/admin/analytics/use-insights";
import { api } from "@/lib/api";
import { dashboardApi, dashboardKeys, rangeParams } from "@/lib/dashboard-api";
import { listDocumentInventory } from "@/lib/documents-api";
import { governanceApi } from "@/lib/governance-api";
import { INSIGHTS_CACHE_PREFIX, insightsApi, insightsQuery, type InsightsEndpoint } from "@/lib/insights-api";
import { prefetch } from "@/lib/query-cache";
import { DEFAULT_PRESET, presetRange } from "@/lib/range-context";
import type { Audience } from "@/lib/types";
import { usageKeys } from "@/lib/use-usage-data";

/** The Analytics Overview tab's first requests (same keys as use-insights). */
const ANALYTICS_FIRST: readonly InsightsEndpoint[] = ["summary", "trend", "hourly", "flow"];

type Prefetcher = (token: string, audience: Audience) => void;

const PREFETCHERS: Record<string, Prefetcher> = {
  "/admin": (token, audience) => {
    const params = rangeParams(presetRange(DEFAULT_PRESET), audience);
    void prefetch(dashboardKeys.summary(params), (signal) => dashboardApi.summary(token, params, signal));
    void prefetch(dashboardKeys.health(audience), (signal) => governanceApi.overview(token, audience, signal));
  },
  "/admin/governance": (token, audience) => {
    void prefetch(dashboardKeys.health(audience), (signal) => governanceApi.overview(token, audience, signal));
  },
  "/admin/usage": (token, audience) => {
    void prefetch(usageKeys.summary(audience), (signal) => api.getUsageSummary(token, { audience }, signal));
  },
  "/admin/analytics": (token, audience) => {
    const tz = viewerTimeZone();
    const filters = { ...toInsightsFilters(DEFAULT_STATE), audience, ...(tz ? { tz } : {}) };
    for (const endpoint of ANALYTICS_FIRST) {
      void prefetch([`${INSIGHTS_CACHE_PREFIX}/${endpoint}`, insightsQuery(filters)], (signal) =>
        insightsApi.aggregate(endpoint, token, filters, signal),
      );
    }
  },
  "/documents": (token) => {
    void prefetch(["documents", "inventory"], (signal) => listDocumentInventory(token, signal));
  },
};

/** Warms `href`'s first queries; a no-op for pages without a prefetcher. */
export function prefetchRouteData(href: string, token: string | null, audience: Audience): void {
  if (!token) return;
  PREFETCHERS[href]?.(token, audience);
}
