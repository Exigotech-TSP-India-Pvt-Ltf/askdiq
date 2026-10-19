/**
 * Typed client for `/admin/insights/*` (newplan §6.3, §6.4; backend PR-13,
 * `backend/app/schemas/insights.py` and `backend/app/api/routes/insights.py`).
 *
 * Kept separate from `lib/api.ts` (owned by the PR-16 follow-up); it uses the
 * same `/api/proxy` route, headers and `ApiError` so error handling is shared.
 */
import { ApiError } from "./api";
import type { Audience } from "./types";

const API_BASE_URL = "/api/proxy";
const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";
const INSIGHTS_PATH = "/admin/insights";

// ---------------------------------------------------------------------------
// Enums and allow-lists (mirror the backend StrEnums)
// ---------------------------------------------------------------------------

export const INSIGHTS_PERIODS = ["all", "7d", "30d", "custom"] as const;
export type InsightsPeriod = (typeof INSIGHTS_PERIODS)[number];

export const CONVERSATION_OUTCOMES = ["Served", "Partly served", "Not served", "Greeting only"] as const;
export type ConversationOutcome = (typeof CONVERSATION_OUTCOMES)[number];

export const EXPERIENCE_RATINGS = [
  "Great",
  "Good",
  "Served with friction",
  "Partly served",
  "Dropped",
  "No question asked",
] as const;
export type ExperienceRating = (typeof EXPERIENCE_RATINGS)[number];

/** Conversations table sort allow-list (the reference HTML's eight columns). */
export const CONVERSATION_SORTS = [
  "conv_id",
  "person",
  "start",
  "user_type",
  "outcome",
  "experience_rating",
  "issue",
  "exchanges",
] as const;
export type ConversationSort = (typeof CONVERSATION_SORTS)[number];

export const SORT_DIRECTIONS = ["asc", "desc"] as const;
export type SortDirection = (typeof SORT_DIRECTIONS)[number];

export type InsightsSource = "rollup" | "live";

/** Taxonomy code (`CODE_PATTERN`). */
export const CODE_RE = /^[A-Z][A-Z0-9_]{0,31}$/;
/** Stable person pseudonym (`PERSON_PATTERN`). */
export const PERSON_RE = /^[0-9a-f]{16}$/;
/** Visitors-page id (`VISITOR_PATTERN`): `client-<ip>` or a user UUID. */
export const VISITOR_RE = /^(client-[A-Za-z0-9:._-]{1,64}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;
export const HOURS_PER_DAY = 24;
export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_SEARCH_CHARS = 200;
export const MAX_USER_TYPE_CHARS = 64;
export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

/** Shared query params (`InsightsFilters`); blank values are omitted. */
export interface InsightsFilters {
  period: InsightsPeriod;
  /** First day, `YYYY-MM-DD` (custom period only). */
  from?: string;
  /** Last day, inclusive (custom period only). */
  to?: string;
  user_type?: string;
  outcome?: ConversationOutcome;
  rating?: ExperienceRating;
  /** Issue code. */
  issue?: string;
  /** Intent code. */
  intent?: string;
  /** Bot-quality code. */
  quality?: string;
  /** `person_id` (stable pseudonym), not the P01 label. */
  person?: string;
  /** Visitors-page id: conversations started by that visitor. */
  visitor?: string;
  /** Hour of day 0-23 in `tz`: exchanges asked in that hour. */
  hour?: number;
  q?: string;
  audience?: Audience;
  /** IANA zone for days and hours (viewer's zone). */
  tz?: string;
}

export interface ConversationPageParams {
  sort: ConversationSort;
  dir: SortDirection;
  limit: number;
  offset: number;
}

const FILTER_KEYS = [
  "period",
  "from",
  "to",
  "user_type",
  "outcome",
  "rating",
  "issue",
  "intent",
  "quality",
  "person",
  "visitor",
  "hour",
  "q",
  "audience",
  "tz",
] as const satisfies readonly (keyof InsightsFilters)[];

/** Query string for the shared filters, in a stable key order (cache keys). */
export function insightsQuery(filters: InsightsFilters, extra: Record<string, string | number> = {}): string {
  const qs = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    // The backend rejects from / to unless period=custom.
    if ((key === "from" || key === "to") && filters.period !== "custom") continue;
    const value = filters[key];
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  for (const [key, value] of Object.entries(extra)) qs.set(key, String(value));
  return qs.toString();
}

// ---------------------------------------------------------------------------
// Responses (mirror backend response models; JSON uses `from` / `to` aliases)
// ---------------------------------------------------------------------------

export interface DateRange {
  from: string | null;
  to: string | null;
}

export interface InsightsSummary {
  range: DateRange;
  conversations: number;
  /** All exchanges in scope, greetings included. */
  exchanges: number;
  handled_exchanges: number;
  substantive_questions: number;
  handled_questions: number;
  /** Handled / substantive x 100. */
  handled_pct: number | null;
  served_conversations: number;
  /** Conversations that are not "Greeting only". */
  active_conversations: number;
  /** Served / active x 100. */
  served_pct: number | null;
  /** Distinct probable people. */
  people: number;
  avg_questions_per_conversation: number | null;
  pending_classification: number;
  source: InsightsSource;
}

export interface TrendDay {
  day: string;
  questions: number;
  conversations: number;
}

export interface InsightsTrend {
  range: DateRange;
  days: TrendDay[];
  source: InsightsSource;
}

export interface InsightsHourly {
  tz: string;
  /** 24 counts, index = hour of day in `tz`. */
  hours: number[];
  total: number;
  peak_hour: number | null;
}

export interface FlowLink {
  user_type: string;
  outcome: ConversationOutcome;
  conversations: number;
}

export interface FlowNode {
  name: string;
  conversations: number;
}

export interface InsightsFlow {
  total: number;
  user_types: FlowNode[];
  outcomes: FlowNode[];
  links: FlowLink[];
}

export interface TopicItem {
  intent: string;
  label: string;
  group: string | null;
  questions: number;
  handled: number;
  handled_pct: number;
}

export interface InsightsTopics {
  total: number;
  items: TopicItem[];
  source: InsightsSource;
}

export interface QualityItem {
  quality: string;
  label: string;
  color: string | null;
  questions: number;
  pct: number;
}

export interface InsightsQuality {
  total: number;
  items: QualityItem[];
  source: InsightsSource;
}

export interface DropoffItem {
  issue: string;
  label: string;
  conversations: number;
}

export interface InsightsDropoff {
  total: number;
  items: DropoffItem[];
}

export interface PersonItem {
  /** Display label P01...: rank by first conversation in the audience, stable under filters. */
  person: string;
  /** Stable pseudonym (salted hash), no PII. */
  person_id: string;
  conversations: number;
  dominant_outcome: ConversationOutcome;
  outcomes: Record<string, number>;
}

/** How a probable person is grouped (`GET /admin/insights/people/{id}`). */
export type PersonIdentity = "account" | "network" | "session";

export interface PersonVisitor {
  /** `client-<ip>`: `/admin/users/{visitor_id}`. */
  visitor_id: string;
  sessions: number;
  first_seen: string;
  last_seen: string;
}

export interface PersonDetail {
  person: string | null;
  person_id: string;
  identity: PersonIdentity;
  /** Labelled conversations, all time, in the audience. */
  conversations: number;
  /** Chat sessions, labelled or not. */
  sessions: number;
  first_seen: string;
  last_seen: string;
  visitors: PersonVisitor[];
  /** Console user (`/admin/users/{uuid}`) when the e-mail is one. */
  account_visitor_id: string | null;
}

export interface InsightsPeople {
  total: number;
  items: PersonItem[];
}

export interface ConversationRow {
  conv_id: string;
  person: string | null;
  person_id: string;
  start: string;
  end: string;
  minutes: number;
  user_type: string;
  outcome: ConversationOutcome;
  experience_rating: ExperienceRating;
  issue: string | null;
  issue_label: string | null;
  failing_question: string | null;
  exchanges: number;
  substantive_exchanges: number;
}

export interface ConversationList {
  items: ConversationRow[];
  total: number;
  limit: number;
  offset: number;
  sort: ConversationSort;
  direction: SortDirection;
}

export interface TurnDetail {
  turn: number;
  timestamp: string;
  user_message: string;
  intent: string | null;
  intent_label: string | null;
  substantive: boolean | null;
  handled: boolean | null;
  quality: string | null;
  quality_label: string | null;
  issue: string | null;
  issue_label: string | null;
  friction: string | null;
  classified_by: string | null;
  pending: boolean;
}

export interface ConversationDetail {
  conversation: ConversationRow | null;
  turns: TurnDetail[];
}

export interface PendingClassification {
  pending: number;
  labelled: number;
  total: number;
  progress_pct: number | null;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

function clientIdHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const id = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    return id ? { "X-Client-Id": id } : {};
  } catch {
    return {};
  }
}

async function get<T>(path: string, token: string | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${INSIGHTS_PATH}${path}`, {
    signal,
    headers: {
      "Content-Type": "application/json",
      ...clientIdHeader(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // response had no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

const withQuery = (path: string, query: string) => (query ? `${path}?${query}` : path);

export type InsightsEndpoint =
  | "summary"
  | "trend"
  | "hourly"
  | "flow"
  | "topics"
  | "quality"
  | "dropoff"
  | "people";

export interface InsightsResponses {
  summary: InsightsSummary;
  trend: InsightsTrend;
  hourly: InsightsHourly;
  flow: InsightsFlow;
  topics: InsightsTopics;
  quality: InsightsQuality;
  dropoff: InsightsDropoff;
  people: InsightsPeople;
}

export const insightsApi = {
  /** Any dashboard aggregate (`summary`, `trend`, ... `people`). */
  aggregate: <E extends InsightsEndpoint>(
    endpoint: E,
    token: string | null,
    filters: InsightsFilters,
    signal?: AbortSignal,
  ): Promise<InsightsResponses[E]> => get<InsightsResponses[E]>(withQuery(`/${endpoint}`, insightsQuery(filters)), token, signal),

  getSummary: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("summary", token, filters, signal),

  getTrend: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("trend", token, filters, signal),

  getHourly: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("hourly", token, filters, signal),

  getFlow: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("flow", token, filters, signal),

  getTopics: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("topics", token, filters, signal),

  getQuality: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("quality", token, filters, signal),

  getDropoff: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("dropoff", token, filters, signal),

  getPeople: (token: string | null, filters: InsightsFilters, signal?: AbortSignal) =>
    insightsApi.aggregate("people", token, filters, signal),

  getConversations: (
    token: string | null,
    filters: InsightsFilters,
    page: ConversationPageParams,
    signal?: AbortSignal,
  ) =>
    get<ConversationList>(
      withQuery(
        "/conversations",
        insightsQuery(filters, { sort: page.sort, dir: page.dir, limit: page.limit, offset: page.offset }),
      ),
      token,
      signal,
    ),

  /** Only `audience` and `tz` apply to the detail route. */
  getConversation: (
    token: string | null,
    convId: string,
    filters: Pick<InsightsFilters, "audience" | "tz">,
    signal?: AbortSignal,
  ) => {
    const qs = new URLSearchParams();
    if (filters.audience) qs.set("audience", filters.audience);
    if (filters.tz) qs.set("tz", filters.tz);
    return get<ConversationDetail>(
      withQuery(`/conversations/${encodeURIComponent(convId)}`, qs.toString()),
      token,
      signal,
    );
  },

  /** Only `audience` and `tz` apply; 404 (ApiError) for an unknown person. */
  getPerson: (
    token: string | null,
    personId: string,
    filters: Pick<InsightsFilters, "audience" | "tz">,
    signal?: AbortSignal,
  ) => {
    const qs = new URLSearchParams();
    if (filters.audience) qs.set("audience", filters.audience);
    if (filters.tz) qs.set("tz", filters.tz);
    return get<PersonDetail>(withQuery(`/people/${encodeURIComponent(personId)}`, qs.toString()), token, signal);
  },

  getPending: (token: string | null, signal?: AbortSignal) => get<PendingClassification>("/pending", token, signal),
};

/** Cache key prefix for every insights query (`invalidate(INSIGHTS_CACHE_PREFIX)`). */
export const INSIGHTS_CACHE_PREFIX = "admin/insights";
