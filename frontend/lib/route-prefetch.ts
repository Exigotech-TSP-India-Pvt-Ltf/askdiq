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
import { filtersForPreset } from "@/components/admin/analytics/use-analytics-preload";
import { DEFAULT_STATE } from "@/components/admin/analytics/url-state";
import { pageOf, prefetchBundle, viewerTimeZone } from "@/components/admin/analytics/use-insights";
import { api } from "@/lib/api";
import { AUDIENCE_COUNTS_KEY } from "@/lib/audience-context";
import { azureApi, azureKeys } from "@/lib/azure-capacity-api";
import { dashboardApi, dashboardKeys } from "@/lib/dashboard-api";
import { summaryFetcher } from "@/lib/use-dashboard";
import { listDocumentInventory } from "@/lib/documents-api";
import { getDocumentCitationStats, getVisitorStats, getVisitors } from "@/lib/entity-stats-api";
import { warmAudiencePage } from "@/lib/audience-data";
import {
  DEFAULT_CASE_QUERY,
  evaluationCasesKey,
  evaluationLiveKey,
  evaluationSummaryKey,
  fetchEvaluationCases,
  fetchEvaluationLive,
  fetchEvaluationSummary,
} from "@/lib/evaluation-live";
import { feedbackKeys, getFeedbackAnalytics } from "@/lib/feedback-api";
import { governanceApi } from "@/lib/governance-api";
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

/** Page defaults mirrored from the pages (keys must match exactly). */
const TOP_CITED_LIMIT = 10;
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
  // Monitoring's Azure capacity / health / Advisor panels (snapshots).
  warm(azureKeys.capacity(), (signal) => azureApi.capacity(token, signal));
  warm(azureKeys.health(), (signal) => azureApi.health(token, signal));
  warm(azureKeys.advisor(), (signal) => azureApi.advisor(token, signal));
  warm(["admin/governance/traces-total", audience], (signal) =>
    governanceApi.traces(token, { audience, limit: 1 }, signal),
  );
  warm(["admin/governance/logs-total", audience], (signal) =>
    governanceApi.logs(token, { audience, limit: 1 }, signal),
  );
}

const PREFETCHERS: Record<string, Prefetcher> = {
  "/admin": (token, audience) => {
    const range = presetRange(DEFAULT_PRESET);
    warm(dashboardKeys.summary(audience, range), summaryFetcher(token, range, audience));
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
  // Both tabs (Visitors | Conversations) of the page.
  "/admin/visitors": (token, audience) => warmAudiencePage(warm, token, audience, defaultDays()),
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
    warm(evaluationSummaryKey(audience, days), (signal) => fetchEvaluationSummary(token, audience, days, signal));
    warm(evaluationCasesKey(audience, days, DEFAULT_CASE_QUERY), (signal) =>
      fetchEvaluationCases(token, audience, days, DEFAULT_CASE_QUERY, signal),
    );
  },
  "/admin/governance": governanceOverall,
  "/admin/analytics": (token, audience) => {
    // The page's first view: the default range preset, one bundle request.
    const filters = filtersForPreset({ period: "all", audience, tz: viewerTimeZone() }, DEFAULT_PRESET);
    prefetchBundle(token, filters, pageOf(DEFAULT_STATE));
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

/** Shell data every page shows (header audience counts, Azure health banner). */
export function prefetchShellData(token: string | null, canReadDashboard = false): void {
  if (!token) return;
  warm(AUDIENCE_COUNTS_KEY, (signal) => api.getAudienceCounts(token, signal));
  if (canReadDashboard) warm(azureKeys.health(), (signal) => azureApi.health(token, signal));
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
