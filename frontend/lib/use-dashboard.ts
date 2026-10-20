"use client";

import { useEffect, useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useLiveConnected } from "@/lib/live-updates";
import { presetRange, DEFAULT_PRESET, RANGE_PRESETS, useRange, type DateRange } from "@/lib/range-context";
import { prefetch, runWhenIdle, useApiQuery } from "@/lib/query-cache";
import {
  currentRangeParams,
  dashboardApi,
  dashboardKeys,
  isNotImplemented,
  rangeParams,
  type DashboardParams,
} from "@/lib/dashboard-api";
import type { Audience } from "@/lib/types";

/** Feed refresh while the live stream is down and the tab is visible (newplan §13.3). */
export const ACTIVITY_REFRESH_MS = 15_000;
const SUMMARY_STALE_MS = 30_000;
const AUDIENCES: readonly Audience[] = ["overview", "users", "testing"];

/** The global range (DateRangePicker) and audience as request params. */
export function useDashboardParams(): { range: DateRange; params: DashboardParams } {
  const { audience } = useAudience();
  const ctx = useRange();
  const range = useMemo(() => ctx?.range ?? presetRange(DEFAULT_PRESET), [ctx?.range]);
  const params = useMemo(() => rangeParams(range, audience), [range, audience]);
  return { range, params };
}

/** Summary fetcher; a preset's window is computed when the request starts. */
export function summaryFetcher(token: string | null, range: DateRange, audience: Audience) {
  return (signal: AbortSignal) => dashboardApi.summary(token, currentRangeParams(range, audience), signal);
}

export function useDashboardSummary(range: DateRange) {
  const { token } = useAuth();
  const { audience } = useAudience();
  return useApiQuery(
    token ? dashboardKeys.summary(audience, range) : null,
    summaryFetcher(token, range, audience),
    // A range / audience switch keeps the current charts until the new ones arrive.
    { staleTime: SUMMARY_STALE_MS, keepPreviousData: true },
  );
}

/**
 * Once the dashboard is on screen, warms every view one click away (the other
 * presets for this audience, the other audiences for this range) in idle
 * time, so a range or audience switch renders from cache. The backend serves
 * them from precomputed snapshots (memory reads).
 */
export function useDashboardPreload(range: DateRange, ready: boolean): void {
  const { token } = useAuth();
  const { audience } = useAudience();
  useEffect(() => {
    if (!token || !ready) return undefined;
    return runWhenIdle(() => {
      for (const preset of RANGE_PRESETS) {
        if (preset === range.kind) continue;
        const other = presetRange(preset);
        void prefetch(dashboardKeys.summary(audience, other), summaryFetcher(token, other, audience), SUMMARY_STALE_MS);
      }
      for (const aud of AUDIENCES) {
        if (aud === audience) continue;
        void prefetch(dashboardKeys.summary(aud, range), summaryFetcher(token, range, aud), SUMMARY_STALE_MS);
        void prefetch(dashboardKeys.activity(aud), (signal) => dashboardApi.activity(token, aud, signal), ACTIVITY_REFRESH_MS);
        void prefetch(dashboardKeys.health(aud), (signal) => dashboardApi.health(token, aud, signal), SUMMARY_STALE_MS);
      }
    });
  }, [token, ready, range, audience]);
}

export function useDashboardActivity() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const live = useLiveConnected();
  const query = useApiQuery(
    token ? dashboardKeys.activity(audience) : null,
    (signal) => dashboardApi.activity(token, audience, signal),
    { staleTime: ACTIVITY_REFRESH_MS },
  );
  const { refetch, error } = query;
  const unavailable = isNotImplemented(error);

  // The console live stream refreshes the feed on every change; poll only
  // while it is down.
  useEffect(() => {
    if (!token || unavailable || live) return undefined;
    const id = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState === "visible") void refetch();
    }, ACTIVITY_REFRESH_MS);
    return () => clearInterval(id);
  }, [refetch, token, unavailable, live]);

  return query;
}

export function useDashboardHealth() {
  const { token } = useAuth();
  const { audience } = useAudience();
  return useApiQuery(
    token ? dashboardKeys.health(audience) : null,
    (signal) => dashboardApi.health(token, audience, signal),
    { staleTime: SUMMARY_STALE_MS },
  );
}
