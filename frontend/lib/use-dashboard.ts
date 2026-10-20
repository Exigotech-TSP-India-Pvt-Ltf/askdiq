"use client";

import { useEffect, useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { presetRange, DEFAULT_PRESET, useRange, type DateRange } from "@/lib/range-context";
import { useApiQuery } from "@/lib/query-cache";
import {
  dashboardApi,
  dashboardKeys,
  isNotImplemented,
  rangeParams,
  type DashboardParams,
} from "@/lib/dashboard-api";

/** Live feed refresh while the tab is visible (paused when hidden). */
export const ACTIVITY_REFRESH_MS = 15_000;
const SUMMARY_STALE_MS = 30_000;

/** The global range (DateRangePicker) and audience as request params. */
export function useDashboardParams(): { range: DateRange; params: DashboardParams } {
  const { audience } = useAudience();
  const ctx = useRange();
  const range = useMemo(() => ctx?.range ?? presetRange(DEFAULT_PRESET), [ctx?.range]);
  const params = useMemo(() => rangeParams(range, audience), [range, audience]);
  return { range, params };
}

export function useDashboardSummary(params: DashboardParams) {
  const { token } = useAuth();
  return useApiQuery(
    token ? dashboardKeys.summary(params) : null,
    (signal) => dashboardApi.summary(token, params, signal),
    // A range / audience switch keeps the current charts until the new ones arrive.
    { staleTime: SUMMARY_STALE_MS, keepPreviousData: true },
  );
}

export function useDashboardActivity() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const query = useApiQuery(
    token ? dashboardKeys.activity(audience) : null,
    (signal) => dashboardApi.activity(token, audience, signal),
    { staleTime: ACTIVITY_REFRESH_MS },
  );
  const { refetch, error } = query;
  const unavailable = isNotImplemented(error);

  useEffect(() => {
    if (!token || unavailable) return undefined;
    const id = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState === "visible") void refetch();
    }, ACTIVITY_REFRESH_MS);
    return () => clearInterval(id);
  }, [refetch, token, unavailable]);

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
