import type { Audience, UsageSummary } from "@/lib/types";
import { api } from "@/lib/api";
import { useApiQuery } from "@/lib/query-cache";

/** Matches the backend's USAGE_SUMMARY_CACHE_TTL_SECONDS (15 s). */
export const USAGE_SUMMARY_STALE_MS = 15_000;

/** Shared query-cache keys (the page and the dashboard's intent prefetch use the same entry). */
export const usageKeys = {
  summary: (audience: Audience) => ["admin/usage/summary", audience] as const,
};

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message ? error.message : "Failed to load usage summary.";
}

/**
 * `GET /admin/usage/summary` through the app-wide query cache: a revisit (or a
 * dashboard KPI hover) renders the cached summary at once and revalidates in
 * the background once it is older than 15 s (stale-while-revalidate).
 */
export function useUsageSummary(token: string | null, audience: Audience) {
  const query = useApiQuery<UsageSummary>(
    token ? usageKeys.summary(audience) : null,
    (signal) => api.getUsageSummary(token ?? "", { audience }, signal),
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
