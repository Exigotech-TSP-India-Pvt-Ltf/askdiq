"use client";

import { useMemo } from "react";
import { useAudience } from "@/lib/audience-context";
import { useAuth } from "@/lib/auth-context";
import {
  INSIGHTS_CACHE_PREFIX,
  insightsApi,
  insightsQuery,
  type ConversationList,
  type ConversationPageParams,
  type InsightsBundle,
  type InsightsEndpoint,
  type InsightsFilters,
  type InsightsResponses,
} from "@/lib/insights-api";
import {
  prefetch,
  queryCache,
  serializeKey,
  useApiQuery,
  usePrefetchNextPage,
  type UseApiQueryResult,
} from "@/lib/query-cache";
import { toInsightsFilters, type AnalyticsState } from "./url-state";

/**
 * Zone for Analytics days and hours: UTC, like the Visitors, Conversations
 * and Dashboard pages (their days are UTC), so the same range gives the same
 * conversation, question and people counts on every page. The browser's zone
 * used to shift day boundaries and made the numbers disagree.
 */
export const ANALYTICS_TIME_ZONE = "UTC";

/** The zone Analytics queries are bucketed in (see `ANALYTICS_TIME_ZONE`). */
export function viewerTimeZone(): string {
  return ANALYTICS_TIME_ZONE;
}

/** API filters for the current URL state plus audience scope and zone. */
export function useInsightsFilters(state: AnalyticsState): InsightsFilters {
  const { audience } = useAudience();
  return useMemo(() => {
    const filters = toInsightsFilters(state);
    return { ...filters, audience, tz: viewerTimeZone() };
  }, [state, audience]);
}

/** The Conversations table page of the URL state. */
export function pageOf(state: Pick<AnalyticsState, "sort" | "dir" | "limit" | "page">): ConversationPageParams {
  return { sort: state.sort, dir: state.dir, limit: state.limit, offset: (state.page - 1) * state.limit };
}

/** Cache key of the bundle (every widget) for one filter set. */
export const bundleKey = (filters: InsightsFilters) => [`${INSIGHTS_CACHE_PREFIX}/bundle`, insightsQuery(filters)] as const;

const conversationsKey = (filters: InsightsFilters, page: ConversationPageParams) =>
  [`${INSIGHTS_CACHE_PREFIX}/conversations`, insightsQuery(filters), page.sort, page.dir, page.limit, page.offset] as const;

/** Bundle requests in flight (the table joins them instead of asking again). */
const inflight = new Map<string, Promise<InsightsBundle>>();

/**
 * Fetches the bundle for `filters` (one request for every card, the KPIs,
 * the backlog and the table page) and seeds the table's cache with its page.
 */
export function fetchBundle(
  token: string | null,
  filters: InsightsFilters,
  page: ConversationPageParams,
  signal?: AbortSignal,
): Promise<InsightsBundle> {
  const key = serializeKey(bundleKey(filters));
  const request = insightsApi.getBundle(token, filters, page, undefined, signal).then((bundle) => {
    if (bundle.conversations) queryCache.setData(conversationsKey(filters, page), bundle.conversations);
    return bundle;
  });
  inflight.set(key, request);
  const clear = () => {
    if (inflight.get(key) === request) inflight.delete(key);
  };
  request.then(clear, clear);
  return request;
}

/** Warms the bundle of `filters` (hover, idle, route preload). */
export function prefetchBundle(token: string | null, filters: InsightsFilters, page: ConversationPageParams): void {
  void prefetch(bundleKey(filters), (signal) => fetchBundle(token, filters, page, signal));
}

/** Every widget for `filters`; switching filters keeps the previous data on screen. */
export function useInsightsBundle(
  filters: InsightsFilters,
  page: ConversationPageParams,
  enabled = true,
): UseApiQueryResult<InsightsBundle> {
  const { token } = useAuth();
  return useApiQuery<InsightsBundle>(bundleKey(filters), (signal) => fetchBundle(token, filters, page, signal), {
    enabled,
    keepPreviousData: true,
  });
}

/** One widget of the bundle (the cards share one request). */
export function useInsightsAggregate<E extends InsightsEndpoint>(
  endpoint: E,
  filters: InsightsFilters,
  page: ConversationPageParams,
  enabled = true,
): UseApiQueryResult<InsightsResponses[E]> {
  const bundle = useInsightsBundle(filters, page, enabled);
  const part = (bundle.data?.[endpoint] ?? undefined) as InsightsResponses[E] | undefined;
  return {
    ...bundle,
    data: part,
    refetch: async () => (await bundle.refetch())?.[endpoint] as InsightsResponses[E] | undefined,
    setData: () => undefined,
  };
}

async function fetchConversations(
  token: string | null,
  filters: InsightsFilters,
  page: ConversationPageParams,
  signal: AbortSignal,
): Promise<ConversationList> {
  // Parent effects (the bundle) start in the same commit: join that request.
  await Promise.resolve();
  const pending = inflight.get(serializeKey(bundleKey(filters)));
  if (pending) {
    try {
      const c = (await pending).conversations;
      if (c && c.sort === page.sort && c.direction === page.dir && c.limit === page.limit && c.offset === page.offset) {
        return c;
      }
    } catch {
      // fall through to a request of its own
    }
  }
  const bundle = await insightsApi.getBundle(token, filters, page, ["conversations"], signal);
  if (!bundle.conversations) throw new Error("Could not load the conversations.");
  return bundle.conversations;
}

/** One page of the Conversations table; the next page is warmed while idle. */
export function useInsightsConversations(filters: InsightsFilters, page: ConversationPageParams) {
  const { token } = useAuth();
  const result = useApiQuery(
    conversationsKey(filters, page),
    (signal) => fetchConversations(token, filters, page, signal),
    { keepPreviousData: true },
  );
  const next = { ...page, offset: page.offset + page.limit };
  const hasNext = result.data !== undefined && next.offset < result.data.total;
  usePrefetchNextPage(
    hasNext ? conversationsKey(filters, next) : null,
    (signal) => fetchConversations(token, filters, next, signal),
  );
  return result;
}

/** Warms a table page (sort header hover). */
export function prefetchConversations(token: string | null, filters: InsightsFilters, page: ConversationPageParams): void {
  void prefetch(conversationsKey(filters, page), (signal) => fetchConversations(token, filters, page, signal));
}

export function useInsightsConversation(convId: string, filters: InsightsFilters, enabled: boolean) {
  const { token } = useAuth();
  const scope = { audience: filters.audience, tz: filters.tz };
  return useApiQuery(
    enabled ? [`${INSIGHTS_CACHE_PREFIX}/conversation`, convId, scope.audience, scope.tz] : null,
    (signal) => insightsApi.getConversation(token, convId, scope, signal),
  );
}

/** Warms one conversation's questions (row hover). */
export function prefetchConversation(token: string | null, convId: string, filters: InsightsFilters): void {
  const scope = { audience: filters.audience, tz: filters.tz };
  void prefetch([`${INSIGHTS_CACHE_PREFIX}/conversation`, convId, scope.audience, scope.tz], (signal) =>
    insightsApi.getConversation(token, convId, scope, signal),
  );
}

/** Readable message for a failed query. */
export function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message ? error.message : "Could not load this data.";
}
