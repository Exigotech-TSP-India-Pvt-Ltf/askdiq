/**
 * Typed client and contract for the Dashboard aggregations (planchanges4
 * B7.1 / F3.1, newplan §1 and §15.4). The written contract is
 * `docs/technical/frontend/contracts/dashboard-aggregations.md`; keep the two in sync.
 *
 * Backward compatible by design: every field the backend returns today is
 * kept as is, every new field is optional. The page renders the existing data
 * and shows an honest empty state for anything the server does not send yet
 * (never invented numbers).
 *
 * Units: durations in milliseconds (`*_ms`), money in US dollars (`*_usd`),
 * ratios as fractions 0..1 (`ratio`, `share`, `delta`). The legacy
 * `error_rate` is a percentage 0..100 (unchanged).
 */
import type { ActiveVisitors } from "@/lib/active-visitors";
import { ApiError } from "@/lib/api";
import type { Audience } from "@/lib/types";
import { utcMidnight, type DateRange } from "@/lib/range-context";

const PROXY_BASE = "/api/proxy/admin";

// ---------------------------------------------------------------------------
// Existing response (GET /admin/dashboard, today)
// ---------------------------------------------------------------------------

export interface DashboardPipeline {
  embedding_ms?: number | null;
  retrieval_ms?: number | null;
  reranker_enabled?: boolean;
  reranker_ms?: number | null;
  llm_ms?: number | null;
}

export interface QueryTrendPoint {
  /** `YYYY-MM-DD` (UTC day). */
  date: string;
  queries: number;
}

export interface LatencyStage {
  stage: string;
  ms: number;
}

// ---------------------------------------------------------------------------
// Additive fields (B7.1). All optional: absent = not implemented yet.
// ---------------------------------------------------------------------------

export const DASHBOARD_KPI_KEYS = [
  "users",
  "active_users",
  "sessions",
  "queries",
  "feedback",
  "avg_latency_ms",
  "error_rate",
  "cost_usd",
] as const;
export type DashboardKpiKey = (typeof DASHBOARD_KPI_KEYS)[number];

/** Outcome keys, the same set as `UsageSummary.quality.outcomes`. */
export const DASHBOARD_OUTCOMES = ["answered", "ungrounded", "blocked", "conversational", "llm_failed", "clarify"] as const;
export type DashboardOutcome = (typeof DASHBOARD_OUTCOMES)[number];

export type SeriesBucket = "hour" | "day";

/** Echo of the window the server actually applied. Its presence means `from` / `to` were honoured. */
export interface DashboardWindow {
  /** ISO 8601 UTC, inclusive. */
  from: string;
  /** ISO 8601 UTC, exclusive. */
  to: string;
  /** Bucket of every series below: `hour` when the window is 48 h or less, else `day`. */
  bucket: SeriesBucket;
  /** The equal-length window just before `from`, used for `deltas`. */
  previous_from: string;
  previous_to: string;
}

export interface SeriesPoint {
  /** Bucket start: `YYYY-MM-DD` for days, ISO 8601 UTC for hours. */
  t: string;
  /** null = no data in the bucket (not zero). */
  value: number | null;
}

export type OutcomePoint = { t: string } & Record<DashboardOutcome, number>;

export interface LatencyPoint {
  t: string;
  p50_ms: number | null;
  p95_ms: number | null;
  /** Requests in the bucket. */
  count: number;
}

export interface DashboardCost {
  currency: "USD";
  /** Calendar month of the figures, `YYYY-MM` (UTC). Always the current month, independent of the range. */
  month: string;
  mtd_usd: number;
  /** Linear projection: `mtd_usd / days_elapsed * days_in_month`. null before the first full day. */
  forecast_month_end_usd: number | null;
  /** `MONTHLY_COST_BUDGET_USD`; null when not configured. */
  budget_usd: number | null;
  days_elapsed: number;
  days_in_month: number;
}

export interface DashboardSatisfaction {
  thumbs_up: number;
  thumbs_down: number;
  /** `thumbs_up / (thumbs_up + thumbs_down)`; null when there are no ratings. */
  ratio: number | null;
}

export interface TopIntent {
  /** Taxonomy key, used as `/admin/analytics?intent=`. */
  intent: string;
  label: string;
  count: number;
  /** count / all classified queries in the window, 0..1. */
  share: number;
}

export interface DashboardData {
  users: number;
  active_users: number;
  sessions: number;
  conversations: number;
  queries: number;
  feedback: number;
  avg_latency_ms: number;
  /** Percentage 0..100. */
  error_rate: number;
  pipeline?: DashboardPipeline;
  governance?: Record<string, string>;
  query_trend?: QueryTrendPoint[];
  latency_breakdown?: LatencyStage[];

  window?: DashboardWindow;
  /** One series per KPI across the window (oldest first). */
  sparklines?: Partial<Record<DashboardKpiKey, SeriesPoint[]>>;
  /** Change vs the previous window as a fraction (0.12 = +12 %); null when the previous value is 0. */
  deltas?: Partial<Record<DashboardKpiKey, number | null>>;
  /** Cost in USD for the window (the KPI); the month view is `cost`. */
  cost_usd?: number;
  queries_by_outcome?: OutcomePoint[];
  latency_series?: LatencyPoint[];
  cost?: DashboardCost;
  satisfaction?: DashboardSatisfaction;
  /** At most 8, highest count first. */
  top_intents?: TopIntent[];
  /** DAU / WAU / MAU and active now, as of the request (independent of the window). */
  active_visitors?: ActiveVisitors;
}

// ---------------------------------------------------------------------------
// Activity feed: GET /admin/dashboard/activity?limit=20 (new, B7.1)
// ---------------------------------------------------------------------------

export const ACTIVITY_TYPES = ["query", "feedback", "error", "guardrail", "document", "admin"] as const;
export type ActivityType = (typeof ACTIVITY_TYPES)[number];

export interface ActivityEvent {
  id: string;
  type: ActivityType;
  /** ISO 8601 UTC. */
  at: string;
  /** One line, PII-masked server side. */
  summary: string;
  request_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  document_id: string | null;
}

export interface ActivityFeed {
  items: ActivityEvent[];
  generated_at: string;
}

export const ACTIVITY_LIMIT = 20;

// ---------------------------------------------------------------------------
// Health pill: GET /admin/governance/overview (PR-08); only `health` is read.
// ---------------------------------------------------------------------------

export const HEALTH_STATES = ["Healthy", "Degraded", "Incident"] as const;
export type HealthState = (typeof HEALTH_STATES)[number];

export interface GovernanceHealthOverview {
  health: { status: HealthState; window_minutes: number };
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface DashboardParams {
  audience: Audience;
  /** ISO 8601 UTC. */
  from: string;
  /** ISO 8601 UTC. */
  to: string;
}

const MINUTE_MS = 60_000;

/** Rounds to the minute so the cache key is stable within a minute. */
function isoMinute(date: Date): string {
  return new Date(Math.floor(date.getTime() / MINUTE_MS) * MINUTE_MS).toISOString();
}

/**
 * Request window for a range. Custom ranges are whole UTC days (the app-wide
 * day convention, matching the UTC day buckets), `to` inclusive, so it moves
 * to the next UTC midnight. Presets are rolling windows ending now.
 */
export function rangeParams(range: DateRange, audience: Audience): DashboardParams {
  if (range.kind === "custom") {
    return { audience, from: isoMinute(utcMidnight(range.from)), to: isoMinute(utcMidnight(range.to, 1)) };
  }
  return { audience, from: isoMinute(range.from), to: isoMinute(range.to) };
}

export function buildQuery(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

const audienceParam = (audience: Audience) => (audience === "overview" ? undefined : audience);

async function getJson<T>(path: string, token: string | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, {
    signal,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown; title?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.title === "string") detail = body.title;
    } catch {
      // no JSON body
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

export const dashboardKeys = {
  summary: (p: DashboardParams) => ["admin/dashboard", p.audience, p.from, p.to] as const,
  activity: (audience: Audience) => ["admin/dashboard/activity", audience] as const,
  health: (audience: Audience) => ["admin/governance/overview", audience] as const,
};

export const dashboardApi = {
  summary: (token: string | null, p: DashboardParams, signal?: AbortSignal) =>
    getJson<DashboardData>(
      `/dashboard${buildQuery({ audience: audienceParam(p.audience), from: p.from, to: p.to })}`,
      token,
      signal,
    ),
  activity: (token: string | null, audience: Audience, signal?: AbortSignal) =>
    getJson<ActivityFeed>(
      `/dashboard/activity${buildQuery({ audience: audienceParam(audience), limit: ACTIVITY_LIMIT })}`,
      token,
      signal,
    ),
  /** Warms a KPI card's target page (admin-relative GET, audience-scoped). */
  prefetch: (token: string | null, path: string, audience: Audience, signal?: AbortSignal) =>
    getJson<unknown>(`${path}${buildQuery({ audience: audienceParam(audience) })}`, token, signal),
  health: (token: string | null, audience: Audience, signal?: AbortSignal) =>
    getJson<GovernanceHealthOverview>(
      `/governance/overview${buildQuery({ audience: audienceParam(audience) })}`,
      token,
      signal,
    ),
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** True for a 404 / 405: the endpoint does not exist on this backend yet. */
export function isNotImplemented(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}

export function isHealthState(value: unknown): value is HealthState {
  return typeof value === "string" && (HEALTH_STATES as readonly string[]).includes(value);
}

const dayLabel = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short", timeZone: "UTC" });
const hourLabel = new Intl.DateTimeFormat("en-AU", { hour: "numeric", timeZone: "UTC" });

/** Axis label for a bucket start. */
export function bucketLabel(t: string, bucket: SeriesBucket = "day"): string {
  const date = new Date(t.length === 10 ? `${t}T00:00:00Z` : t);
  if (Number.isNaN(date.getTime())) return t;
  return bucket === "hour" ? hourLabel.format(date) : dayLabel.format(date);
}
