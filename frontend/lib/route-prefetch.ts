"use client";

/**
 * Data prefetch for every console destination, under the exact keys each
 * page reads, so a click renders from cache:
 *
 * - on navigation intent (sidebar hover / focus), and
 * - for every destination the user can open, once the console is idle after
 *   sign-in (`preloadConsole`), so even the first click on a page is
 *   instant. The backend answers these from precomputed snapshots (memory
 *   reads), so the whole preload costs a few cheap requests.
 *
 * Pages open with the default range (`DEFAULT_PRESET`) and default filters;
 * those are the keys warmed here.
 */
import { DEFAULT_STATE, toInsightsFilters } from "@/components/admin/analytics/url-state";
import { viewerTimeZone } from "@/components/admin/analytics/use-insights";
import { api } from "@/lib/api";
import { AUDIENCE_COUNTS_KEY } from "@/lib/audience-context";
import { dashboardApi, dashboardKeys, rangeParams } from "@/lib/dashboard-api";
import { listDocumentInventory } from "@/lib/documents-api";
import { getDocumentCitationStats, getVisitorStats, getVisitors } from "@/lib/entity-stats-api";
import { evaluationLiveKey, fetchEvaluationLive } from "@/lib/evaluation-live";
import { feedbackKeys, getFeedbackAnalytics } from "@/lib/feedback-api";
import { governanceApi } from "@/lib/governance-api";
import { INSIGHTS_CACHE_PREFIX, insightsApi, insightsQuery, type InsightsEndpoint } from "@/lib/insights-api";
import { prefetch, runWhenIdle } from "@/lib/query-cache";
import { DEFAULT_PRESET, presetRange, rangeUtcDays } from "@/lib/range-context";
import type { Audience } from "@/lib/types";
import { fetchUsageList, usageKeys, usageListKey, type UsageListKind } from "@/lib/use-usage-data";
import { BRANDING_KEY, MODELS_KEY, TAXONOMY_KEY, settingsApi } from "@/lib/settings-api";
import {
  AUDIT_PAGE_DEFAULT,
  umKeys,
  userManagementApi,
  type UserListParams,
} from "@/lib/user-management-api";

/** Every card of the Analytics Overview tab (same keys as use-insights). */
const ANALYTICS_ENDPOINTS: readonly InsightsEndpoint[] = [
  "summary",
  "trend",
  "hourly",
  "flow",
  "topics",
  "quality",
  "dropoff",
  "people",
];
/** Page defaults mirrored from the pages (keys must match exactly). */
const TOP_CITED_LIMIT = 10;
const VISITORS_PAGE_SIZE = 10;
const UM_STATS_DAYS = 30;
const UM_PAGE_SIZE = 25;
/** The Users tab's default first page (use-um-url defaults). */
const UM_USERS_FIRST_PAGE: UserListParams = { sort: "created_at", order: "desc", limit: UM_PAGE_SIZE, offset: 0 };
const LIVE_FEED_LIMIT = 8;
const USAGE_PAGE_SIZE = 10;
const USAGE_TAB_DEFAULTS: ReadonlyArray<readonly [UsageListKind, string]> = [
  ["users", "cost_desc"],
  ["sessions", "cost_desc"],
  ["queries", "created_at_desc"],
];
/** Spacing between destinations during the idle preload (keeps it gentle). */
export const PRELOAD_STEP_MS = 150;

type Prefetcher = (token: string, audience: Audience) => void;

/** Promises of the prefetches started by the running prefetcher (preload waits on them). */
let started: Array<Promise<unknown>> | null = null;

function warm<T>(...args: Parameters<typeof prefetch<T>>): void {
  const promise = prefetch<T>(...args);
  started?.push(promise);
}

/** The default range as inclusive UTC days (day-based endpoints). */
function defaultDays(): { from: string; to: string } {
  return rangeUtcDays(presetRange(DEFAULT_PRESET));
}

function governanceOverall(token: string, audience: Audience): void {
  warm(dashboardKeys.health(audience), (signal) => governanceApi.overview(token, audience, signal));
  warm(["admin/governance/traces-total", audience], (signal) =>
    governanceApi.traces(token, { audience, limit: 1 }, signal),
  );
  warm(["admin/governance/logs-total", audience], (signal) =>
    governanceApi.logs(token, { audience, limit: 1 }, signal),
  );
}

const PREFETCHERS: Record<string, Prefetcher> = {
  "/admin": (token, audience) => {
    const params = rangeParams(presetRange(DEFAULT_PRESET), audience);
    warm(dashboardKeys.summary(params), (signal) => dashboardApi.summary(token, params, signal));
    warm(dashboardKeys.health(audience), (signal) => governanceApi.overview(token, audience, signal));
    warm(dashboardKeys.activity(audience), (signal) => dashboardApi.activity(token, audience, signal));
  },
  "/documents": (token) => {
    const days = defaultDays();
    warm(["documents", "inventory"], (signal) => listDocumentInventory(token, signal));
    warm(["documents", "stats", "citations", days.from, days.to], (signal) =>
      getDocumentCitationStats(token, { from: days.from, to: days.to, limit: TOP_CITED_LIMIT }, signal),
    );
  },
  "/admin/visitors": (token, audience) => {
    const days = defaultDays();
    warm(["visitors", "stats", audience, days.from, days.to], (signal) =>
      getVisitorStats(token, { audience, from: days.from, to: days.to }, signal),
    );
    const listKey = [
      "visitors",
      "list",
      audience,
      "",
      "all",
      "all",
      "last_active_desc",
      VISITORS_PAGE_SIZE,
      0,
      undefined,
      undefined,
      null,
      null,
    ];
    warm(listKey, (signal) =>
      getVisitors(
        token,
        { audience, q: undefined, status: "all", type: "all", sort: "last_active_desc", limit: VISITORS_PAGE_SIZE, offset: 0 },
        signal,
      ),
    );
  },
  "/admin/usage": (token, audience) => {
    const days = defaultDays();
    warm(usageKeys.summary(audience, days), (signal) =>
      api.getUsageSummary(token, { audience, from_date: days.from, to_date: days.to }, signal),
    );
    const feed = {
      audience,
      outcome: undefined,
      from_date: days.from,
      to_date: days.to,
      sort: "created_at_desc",
      limit: LIVE_FEED_LIMIT,
    };
    warm(usageListKey("queries", feed), (signal) => fetchUsageList("queries", token, feed, signal));
    // First page of each tab (All Users / All Sessions / All Queries defaults).
    for (const [kind, sort] of USAGE_TAB_DEFAULTS) {
      const page = { audience, from_date: days.from, to_date: days.to, sort, limit: USAGE_PAGE_SIZE, offset: 0 };
      warm(usageListKey(kind, page), (signal) => fetchUsageList(kind, token, page, signal));
    }
  },
  "/admin/feedback": (token, audience) => {
    const days = defaultDays();
    warm(feedbackKeys.analytics(audience, days), (signal) =>
      getFeedbackAnalytics(token, audience, signal, days),
    );
  },
  "/admin/evaluation-live": (token, audience) => {
    const days = defaultDays();
    warm(evaluationLiveKey(audience, days), (signal) => fetchEvaluationLive(token, audience, days, signal));
  },
  "/admin/governance": governanceOverall,
  "/admin/analytics": (token, audience) => {
    const tz = viewerTimeZone();
    const filters = { ...toInsightsFilters(DEFAULT_STATE), audience, ...(tz ? { tz } : {}) };
    for (const endpoint of ANALYTICS_ENDPOINTS) {
      warm([`${INSIGHTS_CACHE_PREFIX}/${endpoint}`, insightsQuery(filters)], (signal) =>
        insightsApi.aggregate(endpoint, token, filters, signal),
      );
    }
    warm(`${INSIGHTS_CACHE_PREFIX}/pending`, (signal) => insightsApi.getPending(token, signal));
  },
  "/admin/user-management": () => {
    // KPIs + charts, then the first page of every tab (Users, the request
    // queue, the audit trail): each tab switch renders from cache.
    warm(umKeys.stats(UM_STATS_DAYS), (signal) => userManagementApi.stats(UM_STATS_DAYS, signal));
    warm(umKeys.users(UM_USERS_FIRST_PAGE), (signal) => userManagementApi.listUsers(UM_USERS_FIRST_PAGE, signal));
    warm(umKeys.requests("pending", UM_PAGE_SIZE, 0), (signal) =>
      userManagementApi.listRequests("pending", UM_PAGE_SIZE, 0, signal),
    );
    const audit = { limit: AUDIT_PAGE_DEFAULT };
    warm(umKeys.auditPage(audit), (signal) => userManagementApi.authAuditPage(audit, signal));
  },
  "/admin/settings": () => {
    warm(BRANDING_KEY, (signal) => settingsApi.getBranding(signal));
    warm(TAXONOMY_KEY, (signal) => settingsApi.listTaxonomy(signal));
    warm(MODELS_KEY, (signal) => settingsApi.getModelSettings(signal));
  },
};

/** Warms `href`'s first queries; a no-op for pages without a prefetcher. */
export function prefetchRouteData(href: string, token: string | null, audience: Audience): void {
  if (!token) return;
  PREFETCHERS[href]?.(token, audience);
}

/** Shell data every page shows (header audience counts). */
export function prefetchShellData(token: string | null): void {
  if (!token) return;
  warm(AUDIENCE_COUNTS_KEY, (signal) => api.getAudienceCounts(token, signal));
}

/** Starts `href`'s prefetches and returns their promises. */
function warmRoute(href: string, token: string, audience: Audience): Array<Promise<unknown>> {
  started = [];
  try {
    PREFETCHERS[href]?.(token, audience);
    return started;
  } finally {
    started = null;
  }
}

/**
 * Warms every destination in `hrefs`, one at a time: each waits for the
 * previous one's requests and for an idle moment (`PRELOAD_STEP_MS` apart),
 * so the preload never competes with what the user is looking at. Returns a
 * cancel function.
 */
export function preloadConsole(
  hrefs: readonly string[],
  token: string | null,
  audience: Audience,
  step: number = PRELOAD_STEP_MS,
): () => void {
  if (!token) return () => undefined;
  let cancelled = false;
  const pause = () =>
    new Promise<void>((resolve) => {
      setTimeout(() => runWhenIdle(resolve), step);
    });
  void (async () => {
    for (const href of hrefs.filter((h) => h in PREFETCHERS)) {
      if (cancelled) return;
      await Promise.allSettled(warmRoute(href, token, audience));
      await pause();
    }
  })();
  return () => {
    cancelled = true;
  };
}
