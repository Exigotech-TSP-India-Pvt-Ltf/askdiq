import type { Audience, UsageSummary } from "@/lib/types";
import { api } from "@/lib/api";
import { prefetch, runWhenIdle, useApiQuery } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange, rangeUtcDays } from "@/lib/range-context";

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

/** `GET /admin/usage/summary` for one audience and range (hook and prefetch). */
export function fetchUsageSummary(
  token: string,
  audience: Audience,
  days: UsageDays,
  signal?: AbortSignal,
): Promise<UsageSummary> {
  return api.getUsageSummary(token, { audience, from_date: days.from, to_date: days.to }, signal);
}

/** Warms the summary of `audience` over `days` (no-op while it is fresh). */
export function prefetchUsageSummary(token: string | null, audience: Audience, days: UsageDays): void {
  if (!token) return;
  void prefetch(
    usageKeys.summary(audience, days),
    (signal) => fetchUsageSummary(token, audience, days, signal),
    USAGE_SUMMARY_STALE_MS,
  );
}

/**
 * `GET /admin/usage/summary` through the app-wide query cache: a revisit (or a
 * dashboard KPI hover) renders the cached summary at once and revalidates in
 * the background once it is older than 15 s (stale-while-revalidate).
 * `days` (the global range) is sent as `from_date` / `to_date`. A range or
 * audience switch keeps the previous summary on screen (`isUpdating`) until
 * the new one arrives: the cards never fall back to skeletons.
 */
export function useUsageSummary(token: string | null, audience: Audience, days: UsageDays = {}) {
  const query = useApiQuery<UsageSummary>(
    token ? usageKeys.summary(audience, days) : null,
    (signal) => fetchUsageSummary(token ?? "", audience, days, signal),
    { staleTime: USAGE_SUMMARY_STALE_MS, keepPreviousData: true },
  );
  const { refetch } = query;

  return {
    summary: query.data ?? null,
    // Signed-out / auth still loading reads as loading, as before.
    isLoading: !token || query.isLoading,
    /** The summary on screen belongs to the previous audience / range. */
    isUpdating: query.isPlaceholderData,
    error: errorMessage(query.error),
    refresh: refetch,
  };
}

/** Usage list endpoints (All Users / All Sessions / All Queries tabs). */
export type UsageListKind = "users" | "sessions" | "queries";
type UsageListParams = Record<string, string | number | boolean | undefined>;

/** `api` method per list (looked up at call time, so test spies apply). */
const USAGE_LIST_METHODS = {
  users: "getUsageUsers",
  sessions: "getUsageSessions",
  queries: "getUsageQueries",
} as const;

/** Stable key: defined params sorted by name (undefined = not sent). */
function paramsKey(params: UsageListParams): string {
  return Object.keys(params)
    .filter((k) => params[k] !== undefined && params[k] !== "")
    .sort()
    .map((k) => `${k}=${String(params[k])}`)
    .join("&");
}

export const usageListKey = (kind: UsageListKind, params: UsageListParams) =>
  [`admin/usage/${kind}`, paramsKey(params)] as const;

/** Fetches one page of a usage list (shared by the tabs and prefetch). */
export function fetchUsageList<T>(
  kind: UsageListKind,
  token: string,
  params: UsageListParams,
  signal?: AbortSignal,
): Promise<T> {
  const fetcher = api[USAGE_LIST_METHODS[kind]] as unknown as (
    token: string,
    params: UsageListParams,
    signal?: AbortSignal,
  ) => Promise<T>;
  return fetcher(token, params, signal);
}

/**
 * One page of a usage list through the app-wide query cache, keeping the
 * previous page on screen while another loads (no blank table on paging,
 * sorting, filtering or a range change).
 */
export function useUsageList<T>(kind: UsageListKind, token: string | null, params: UsageListParams) {
  return useApiQuery<T>(
    token ? usageListKey(kind, params) : null,
    (signal) => fetchUsageList<T>(kind, token ?? "", params, signal),
    { staleTime: USAGE_SUMMARY_STALE_MS, keepPreviousData: true },
  );
}

/** Rows in the Overview's live query feed. */
export const OVERVIEW_FEED_LIMIT = 8;

/** Params of the Overview's live query feed (one cache key per filter set). */
export function overviewFeedParams(audience: Audience, days: UsageDays, outcome?: string): UsageListParams {
  return {
    audience,
    outcome: outcome && outcome !== "all" ? outcome : undefined,
    from_date: days.from || undefined,
    to_date: days.to || undefined,
    sort: "created_at_desc",
    limit: OVERVIEW_FEED_LIMIT,
  };
}

const AUDIENCES: readonly Audience[] = ["overview", "users", "testing"];

/**
 * Warms, when the browser is idle, what one click on the Overview can show
 * next: every range preset of this audience and this range for the other
 * audiences (summary + live feed). Each is a precomputed snapshot server
 * side, so a switch renders from the cache. Returns a cancel function.
 */
export function warmUsageOverview(token: string | null, audience: Audience, days: UsageDays): () => void {
  if (!token) return () => undefined;
  return runWhenIdle(() => {
    const targets: Array<[Audience, UsageDays]> = [
      ...RANGE_PRESETS.map((preset): [Audience, UsageDays] => [audience, rangeUtcDays(presetRange(preset))]),
      ...AUDIENCES.filter((other) => other !== audience).map((other): [Audience, UsageDays] => [other, days]),
    ];
    for (const [aud, range] of targets) {
      prefetchUsageSummary(token, aud, range);
      const feed = overviewFeedParams(aud, range);
      void prefetch(
        usageListKey("queries", feed),
        (signal) => fetchUsageList("queries", token, feed, signal),
        USAGE_SUMMARY_STALE_MS,
      );
    }
  });
}

/** Display text of a query error (null when there is none). */
export function queryErrorMessage(error: unknown, fallback: string): string | null {
  if (!error) return null;
  if (error instanceof Error && error.name === "AbortError") return null;
  return error instanceof Error && error.message ? error.message : fallback;
}

/** A finished turn does not change; the inspector revalidates rarely. */
export const QUERY_DETAIL_STALE_MS = 5 * 60_000;

export const queryDetailKey = (requestId: string) => ["admin/usage/query", requestId] as const;

/** Warms the query inspector for `requestId` (row hover). */
export function prefetchQueryDetail(token: string | null, requestId: string): void {
  if (!token || !requestId) return;
  void prefetch(queryDetailKey(requestId), (signal) => api.getQueryDetail(token, requestId, signal), QUERY_DETAIL_STALE_MS);
}
