"use client";

import { useEffect } from "react";
import { useAuth } from "@/lib/auth-context";
import type { InsightsFilters } from "@/lib/insights-api";
import { runWhenIdle } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange } from "@/lib/range-context";
import { pageOf, prefetchBundle } from "./use-insights";
import { periodFromRange, type AnalyticsState } from "./url-state";

/** `filters` with the period of a date-range preset. */
export function filtersForPreset(filters: InsightsFilters, preset: (typeof RANGE_PRESETS)[number]): InsightsFilters {
  const { from: _from, to: _to, period: _period, ...rest } = filters;
  const range = periodFromRange(presetRange(preset));
  return range.period === "custom" ? { ...rest, period: "custom", from: range.from, to: range.to } : { ...rest, period: range.period };
}

/**
 * Once the page is idle, warms the bundle of every other date-range preset
 * (same filters) so a range switch paints from memory: one request each,
 * answered from the server's in-memory facts.
 */
export function useAnalyticsPreload(state: AnalyticsState, filters: InsightsFilters): void {
  const { token } = useAuth();
  useEffect(() => {
    // Unit tests assert the exact requests a view makes: no speculative ones there.
    if (!token || state.tab === "guide" || process.env.NODE_ENV === "test") return undefined;
    const page = pageOf({ ...state, page: 1 });
    return runWhenIdle(() => {
      for (const preset of RANGE_PRESETS) prefetchBundle(token, filtersForPreset(filters, preset), page);
    });
  }, [token, filters, state]);
}
