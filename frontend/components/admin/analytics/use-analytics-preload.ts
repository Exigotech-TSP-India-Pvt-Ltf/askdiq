"use client";

import { useEffect } from "react";
import { useAuth } from "@/lib/auth-context";
import { AUDIENCES } from "@/lib/audience-data";
import { insightsQuery, type InsightsFilters } from "@/lib/insights-api";
import { runWhenIdle } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange } from "@/lib/range-context";
import { pageOf, prefetchBundle, prefetchConversations } from "./use-insights";
import { periodFromRange, toInsightsFilters, type AnalyticsState } from "./url-state";

/** `filters` with the period of a date-range preset. */
export function filtersForPreset(filters: InsightsFilters, preset: (typeof RANGE_PRESETS)[number]): InsightsFilters {
  const { from: _from, to: _to, period: _period, ...rest } = filters;
  const range = periodFromRange(presetRange(preset));
  return range.period === "custom" ? { ...rest, period: "custom", from: range.from, to: range.to } : { ...rest, period: range.period };
}

/**
 * Warms what `patch` (a KPI's "View records", a filter, a sort) opens from
 * `state`: the bundle of the new filters, or only the table page when just
 * the sort or page changes. Every tab reads the one bundle, so a tab switch
 * alone needs nothing.
 */
export function prefetchAnalyticsPatch(
  token: string | null,
  state: AnalyticsState,
  filters: InsightsFilters,
  patch: Partial<AnalyticsState>,
): void {
  const next: AnalyticsState = { ...state, ...patch };
  const nextFilters: InsightsFilters = { ...toInsightsFilters(next), audience: filters.audience, tz: filters.tz };
  if (insightsQuery(nextFilters) !== insightsQuery(filters)) {
    prefetchBundle(token, nextFilters, pageOf({ ...next, page: 1 }));
    return;
  }
  const page = pageOf(next);
  const current = pageOf(state);
  if (page.sort !== current.sort || page.dir !== current.dir || page.offset !== current.offset || page.limit !== current.limit) {
    prefetchConversations(token, filters, page);
  }
}

/**
 * Once the page is idle, warms everything one click away so any switch
 * paints from memory (each one request answered from the server's in-memory
 * facts): every other date-range preset and audience of the same filters,
 * and the records behind each of `targets` (the KPI cards' "View records").
 * Every tab reads the bundle already on screen; the table warms its next
 * page itself.
 */
export function useAnalyticsPreload(
  state: AnalyticsState,
  filters: InsightsFilters,
  targets: readonly Partial<AnalyticsState>[] = [],
): void {
  const { token } = useAuth();
  useEffect(() => {
    // Unit tests assert the exact requests a view makes: no speculative ones there.
    if (!token || process.env.NODE_ENV === "test") return undefined;
    const page = pageOf({ ...state, page: 1 });
    return runWhenIdle(() => {
      // The code-split Flow chart: its tab never waits on a chunk.
      void import("@/components/charts/heavy/SankeyImpl").catch(() => undefined);
      for (const preset of RANGE_PRESETS) prefetchBundle(token, filtersForPreset(filters, preset), page);
      for (const audience of AUDIENCES) {
        if (audience !== filters.audience) prefetchBundle(token, { ...filters, audience }, page);
      }
      for (const target of targets) prefetchAnalyticsPatch(token, state, filters, target);
    });
  }, [token, filters, state, targets]);
}
