"use client";

import { useMemo } from "react";
import { useAudience } from "@/lib/audience-context";
import { useAuth } from "@/lib/auth-context";
import {
  INSIGHTS_CACHE_PREFIX,
  insightsApi,
  insightsQuery,
  type ConversationPageParams,
  type InsightsEndpoint,
  type InsightsFilters,
  type InsightsResponses,
} from "@/lib/insights-api";
import { useApiQuery, usePrefetchNextPage, type UseApiQueryResult } from "@/lib/query-cache";
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

export function useInsightsAggregate<E extends InsightsEndpoint>(
  endpoint: E,
  filters: InsightsFilters,
  enabled = true,
): UseApiQueryResult<InsightsResponses[E]> {
  const { token } = useAuth();
  return useApiQuery<InsightsResponses[E]>(
    [`${INSIGHTS_CACHE_PREFIX}/${endpoint}`, insightsQuery(filters)],
    (signal) => insightsApi.aggregate(endpoint, token, filters, signal),
    // A filter / period / audience change swaps the chart in place.
    { enabled, keepPreviousData: true },
  );
}

const conversationsKey = (filters: InsightsFilters, page: ConversationPageParams) =>
  [`${INSIGHTS_CACHE_PREFIX}/conversations`, insightsQuery(filters), page.sort, page.dir, page.limit, page.offset] as const;

/** One page of the Conversations table; the next page is warmed while idle. */
export function useInsightsConversations(filters: InsightsFilters, page: ConversationPageParams) {
  const { token } = useAuth();
  const result = useApiQuery(
    conversationsKey(filters, page),
    (signal) => insightsApi.getConversations(token, filters, page, signal),
    { keepPreviousData: true },
  );
  const next = { ...page, offset: page.offset + page.limit };
  const hasNext = result.data !== undefined && next.offset < result.data.total;
  usePrefetchNextPage(
    hasNext ? conversationsKey(filters, next) : null,
    (signal) => insightsApi.getConversations(token, filters, next, signal),
  );
  return result;
}

export function useInsightsConversation(convId: string, filters: InsightsFilters, enabled: boolean) {
  const { token } = useAuth();
  const scope = { audience: filters.audience, tz: filters.tz };
  return useApiQuery(
    enabled ? [`${INSIGHTS_CACHE_PREFIX}/conversation`, convId, scope.audience, scope.tz] : null,
    (signal) => insightsApi.getConversation(token, convId, scope, signal),
  );
}

export function useInsightsPending() {
  const { token } = useAuth();
  return useApiQuery(`${INSIGHTS_CACHE_PREFIX}/pending`, (signal) => insightsApi.getPending(token, signal));
}

/** Readable message for a failed query. */
export function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error && error.message ? error.message : "Could not load this data.";
}
