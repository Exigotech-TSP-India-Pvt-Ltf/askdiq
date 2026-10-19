import type { Audience, UsageSummary } from "@/lib/types";
import { api } from "@/lib/api";
import { useApiQuery } from "@/lib/query-cache";

/** Matches the backend's USAGE_SUMMARY_CACHE_TTL_SECONDS (15 s). */
export const USAGE_SUMMARY_STALE_MS = 15_000;

/** Inclusive UTC days (YYYY-MM-DD) of the global date range; empty = all time. */
export interface UsageDays {
  from?: string;
  to?: string;
}

/**
 * Shared query-cache keys (the page and the dashboard's intent prefetch use
 * the same entry). The range is part of the key so two ranges never share a
 * cached summary.
 */
export const usageKeys = {
  summary: (audience: Audience, days: UsageDays = {}) =>
    ["admin/usage/summary", audience, days.from ?? "", days.to ?? ""] as const,
};

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message ? error.message : "Failed to load usage summary.";
}

/**
 * `GET /admin/usage/summary` through the app-wide query cache: a revisit (or a
 * dashboard KPI hover) renders the cached summary at once and revalidates in
 * the background once it is older than 15 s (stale-while-revalidate).
 * `days` (the global range) is sent as `from_date` / `to_date`.
 */
export function useUsageSummary(token: string | null, audience: Audience, days: UsageDays = {}) {
  const query = useApiQuery<UsageSummary>(
    token ? usageKeys.summary(audience, days) : null,
    (signal) =>
      api.getUsageSummary(token ?? "", { audience, from_date: days.from, to_date: days.to }, signal),
    { staleTime: USAGE_SUMMARY_STALE_MS },
  );
  const { refetch } = query;

  return {
    summary: query.data ?? null,
    // Signed-out / auth still loading reads as loading, as before.
    isLoading: !token || query.isLoading,
    error: errorMessage(query.error),
    refresh: refetch,
  };
}
