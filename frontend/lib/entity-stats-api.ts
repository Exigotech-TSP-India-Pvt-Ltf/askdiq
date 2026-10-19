/**
 * Typed client for the entity pages (planchanges4 F3.2 to F3.4): Visitors,
 * Conversations and Documents, list and detail.
 *
 * Contract: `frontend/docs/contracts/entity-stats.md` (the backend PR-14,
 * planchanges4 B7.2 / B7.3, implements it). Every new endpoint and field is
 * ADDITIVE: the pages keep rendering existing data when an endpoint answers
 * 404 / 405 / 501 (not deployed yet), and the charts fed by it show their
 * empty state. Nothing here invents data.
 *
 * Requests go through the same `/api/proxy` route and `ApiError` as `lib/api.ts`.
 */
import type { ActiveVisitors } from "./active-visitors";
import { ApiError } from "./api";
import type { Audience, PageEnvelope } from "./types";

const API_BASE_URL = "/api/proxy";
const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

/** `turn_index` outcomes, mirrored from `chat_messages.outcome`. */
export const OUTCOMES = ["answered", "ungrounded", "blocked", "conversational", "llm_failed"] as const;
export type Outcome = (typeof OUTCOMES)[number];

export const OUTCOME_LABELS: Record<Outcome, string> = {
  answered: "Answered",
  ungrounded: "Ungrounded",
  blocked: "Blocked",
  conversational: "Conversational",
  llm_failed: "LLM failed",
};

export function isOutcome(value: unknown): value is Outcome {
  return typeof value === "string" && (OUTCOMES as readonly string[]).includes(value);
}

/** Request stages in pipeline order (keys of `TurnDetail.stages_ms`). */
export const STAGES = ["guardrail_pre", "embedding", "retrieval", "reranker", "llm", "guardrail_post"] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  guardrail_pre: "Guardrail (in)",
  embedding: "Embedding",
  retrieval: "Retrieval",
  reranker: "Reranker",
  llm: "LLM",
  guardrail_post: "Guardrail (out)",
};

/** Weekday index used by the visitor heatmap: 0 = Monday ... 6 = Sunday (ISO order). */
export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export const HOURS_PER_DAY = 24;

/** Ratings at or below this are "low rated" (1 to 5 scale, feedback API). */
export const LOW_RATING_MAX = 2;

/** Visitor table `sort` values accepted by `GET /admin/users`. */
export const VISITOR_SORTS = [
  "last_active_desc",
  "last_active_asc",
  "queries_desc",
  "queries_asc",
  "sessions_desc",
  "sessions_asc",
  "first_seen_desc",
  "first_seen_asc",
] as const;

/** `sort` values for `GET /documents/{id}/chunks`. */
export const CHUNK_SORTS = ["index", "hits"] as const;
export type ChunkSort = (typeof CHUNK_SORTS)[number];

export function isChunkSort(value: unknown): value is ChunkSort {
  return typeof value === "string" && (CHUNK_SORTS as readonly string[]).includes(value);
}

/** Delay before a typed chunk search reaches the URL and the server. */
export const CHUNK_SEARCH_DEBOUNCE_MS = 300;

/** Longest chunk search string sent to the server (B7.3 validates the same bound). */
export const MAX_CHUNK_QUERY_CHARS = 200;

// ---------------------------------------------------------------------------
// Existing endpoints (shapes as served today by the base backend)
// ---------------------------------------------------------------------------

/** Row of `GET /admin/users` (one row per client IP). */
export interface VisitorRow {
  id: string;
  ip_address: string;
  is_test?: boolean;
  type?: string;
  first_seen?: string | null;
  last_active?: string | null;
  status?: string;
  sessions?: number;
  conversations?: number;
  queries?: number;
  feedback_count?: number;
}

export interface VisitorConversation {
  id: string;
  title?: string | null;
  created_at?: string | null;
  last_activity_at?: string | null;
  message_count?: number;
}

/** `GET /admin/users/{visitor_id}`. */
export interface VisitorDetail extends VisitorRow {
  email?: string | null;
  full_name?: string | null;
  conversations_data?: VisitorConversation[];
}

/** Row of `GET /admin/conversations`. `turn_count` / `outcome_counts` are B7.2 additions. */
export interface ConversationRow {
  id: string;
  title?: string | null;
  created_at?: string | null;
  last_activity_at?: string | null;
  expires_at?: string | null;
  message_count?: number;
  created_ip?: string | null;
  turn_count?: number;
  outcome_counts?: Partial<Record<string, number>>;
}

export interface ConversationMessage {
  id: string;
  session_id?: string;
  turn_index: number;
  role: string;
  content: string;
  created_at?: string | null;
  request_id?: string | null;
  grounded?: boolean | null;
  guardrail_flags?: string[] | null;
  ip_address?: string | null;
}

export interface ConversationHeader {
  id: string;
  title?: string | null;
  user_email?: string | null;
  created_at?: string | null;
  last_activity_at?: string | null;
  expires_at?: string | null;
  created_ip?: string | null;
}

// ---------------------------------------------------------------------------
// B7.2: visitors
// ---------------------------------------------------------------------------

export interface RangeParams {
  /** Inclusive first day, `YYYY-MM-DD` (server timezone UTC unless `tz`). */
  from?: string;
  /** Inclusive last day, `YYYY-MM-DD`. */
  to?: string;
}

export interface VisitorStatsKpis {
  visitors: number;
  new_visitors: number;
  returning_visitors: number;
  /** Active visitors (>= 1 query, test IPs excluded) in the last 5 minutes (independent of the range). */
  active_now: number;
  sessions: number;
  queries: number;
  /** `sessions / visitors`; null when there are no visitors. */
  avg_sessions_per_visitor: number | null;
}

export interface NewVsReturningDay {
  /** `YYYY-MM-DD`. Every day of the range is present (zero-filled). */
  day: string;
  new: number;
  returning: number;
}

export interface VisitorHeatCell {
  /** 0 = Monday ... 6 = Sunday. */
  weekday: number;
  /** 0 to 23. */
  hour: number;
  /** Sessions started in this slot within the range. Zero cells may be omitted. */
  sessions: number;
}

/** `GET /admin/visitors/stats`. */
export interface VisitorStats {
  from: string;
  to: string;
  tz: string;
  kpis: VisitorStatsKpis;
  new_vs_returning: NewVsReturningDay[];
  heatmap: VisitorHeatCell[];
  /** DAU / WAU / MAU and active now, as of the request (independent of the range). */
  active_visitors?: ActiveVisitors;
}

export interface TimelineSession {
  session_id: string;
  title: string | null;
  /** ISO-8601 UTC. */
  started_at: string;
  /** Last activity, ISO-8601 UTC (>= started_at). */
  ended_at: string;
  turns: number;
  cost_usd: number;
}

export interface TimelineQuery {
  request_id: string;
  session_id: string;
  turn_index: number;
  query: string;
  outcome: string | null;
  grounded: boolean | null;
  created_at: string;
  latency_ms: number | null;
  cost_usd: number | null;
}

export interface CostDay {
  day: string;
  cost_usd: number;
  queries: number;
}

/** Per-day counts behind the visitor KPI sparklines (additive; absent on older backends). */
export interface ActivityDay {
  day: string;
  sessions: number;
  queries: number;
  feedback: number;
}

export interface TimelineFeedback {
  id: string;
  message_id: string;
  session_id: string;
  turn_index: number | null;
  request_id: string | null;
  rating: number;
  comment: string | null;
  reason_tags: string[];
  created_at: string;
}

/** `GET /admin/visitors/{visitor_id}/timeline`. */
export interface VisitorTimeline {
  visitor_id: string;
  ip_address: string | null;
  sessions: TimelineSession[];
  queries: TimelineQuery[];
  cost_series: CostDay[];
  activity_series?: ActivityDay[];
  feedback: TimelineFeedback[];
  truncated: { sessions: boolean; queries: boolean; feedback: boolean };
}

// ---------------------------------------------------------------------------
// B7.2: conversations
// ---------------------------------------------------------------------------

export interface TurnsBin {
  /** Inclusive lower bound (turns). */
  min_turns: number;
  /** Inclusive upper bound; null = open-ended last bin ("10+"). */
  max_turns: number | null;
  conversations: number;
}

export interface OutcomeDay {
  day: string;
  /** Turns per outcome on that day (assistant turns). Unknown outcomes may appear. */
  counts: Partial<Record<string, number>>;
}

/** `GET /admin/conversations/stats`. */
export interface ConversationStats {
  from: string;
  to: string;
  totals: { conversations: number; turns: number; avg_turns: number | null };
  turns_histogram: TurnsBin[];
  outcome_by_day: OutcomeDay[];
}

export interface TurnTokens {
  prompt: number;
  cached_prompt: number;
  completion: number;
  reasoning: number;
  embedding: number;
  guardrail: number;
  total: number;
}

export interface TurnSource {
  document_id: string | null;
  chunk_id: string | null;
  chunk_index: number | null;
  source_name: string | null;
  section_heading: string | null;
  score: number | null;
}

export interface TurnFeedback {
  id: string;
  rating: number;
  comment: string | null;
  reason_tags: string[];
  triage_status: string | null;
  created_at: string;
}

export interface TurnLabels {
  intent: string | null;
  quality: string | null;
  issue: string | null;
}

export interface TurnDetail {
  turn_index: number;
  request_id: string | null;
  user_message_id: string | null;
  assistant_message_id: string | null;
  created_at: string | null;
  outcome: string | null;
  grounded: boolean | null;
  /** Milliseconds per stage; null = stage did not run or was not measured. */
  stages_ms: Partial<Record<Stage, number | null>>;
  total_ms: number | null;
  tokens: TurnTokens | null;
  cost_usd: number | null;
  sources: TurnSource[];
  feedback: TurnFeedback[];
  labels: TurnLabels | null;
  /** Golden test case id when this turn was already promoted. */
  promoted_case_id: string | null;
}

/** `GET /admin/conversations/{id}?turn=` (B7.2 adds `turns` and `focus_turn`). */
export interface ConversationDetail {
  conversation: ConversationHeader;
  messages: ConversationMessage[];
  message_count: number;
  turns?: TurnDetail[];
  focus_turn?: number | null;
}

export interface PromoteResult {
  status: string;
  case_id: string;
  query: string;
}

// ---------------------------------------------------------------------------
// B7.3: documents
// ---------------------------------------------------------------------------

export interface CitedDocument {
  document_id: string;
  source_name: string;
  citations: number;
  last_cited_at: string | null;
}

export interface DocumentAgePoint {
  document_id: string;
  source_name: string;
  /** Days since the document was last (re)ingested. */
  age_days: number;
  citations: number;
}

export interface CitationCoverage {
  /** Assistant turns with outcome "answered" in the range. */
  answered_queries: number;
  /** Of those, turns that cited at least one source. */
  cited_queries: number;
  /** 0 to 100; null when `answered_queries` is 0. */
  coverage_pct: number | null;
}

/** `GET /documents/stats/citations`. */
export interface DocumentCitationStats {
  from: string;
  to: string;
  top_cited: CitedDocument[];
  age_vs_citations: DocumentAgePoint[];
  coverage: CitationCoverage;
}

export interface WeeklyHits {
  /** Monday of the ISO week, `YYYY-MM-DD`. */
  week_start: string;
  hits: number;
}

/** Item of `GET /documents/{id}/chunks`; `hits`, `last_hit_at`, `weekly_hits` are B7.3 additions. */
export interface DocumentChunk {
  id: string;
  chunk_index: number;
  content: string;
  strategy: string;
  section_heading: string | null;
  char_count: number;
  hits?: number;
  low_rated_hits?: number;
  last_hit_at?: string | null;
  weekly_hits?: WeeklyHits[];
}

export interface LowRatedCitation {
  message_id: string;
  session_id: string;
  turn_index: number | null;
  request_id: string | null;
  chunk_id: string | null;
  chunk_index: number | null;
  query: string | null;
  rating: number;
  reason_tags: string[];
  comment: string | null;
  created_at: string;
}

/** `GET /documents/{id}/low-rated-citations`. */
export interface LowRatedCitations {
  document_id: string;
  items: LowRatedCitation[];
  total: number;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/** Status codes that mean "endpoint not deployed yet" (render without it). */
export const ABSENT_STATUSES: readonly number[] = [404, 405, 501];

export function isAbsent(error: unknown): boolean {
  return error instanceof ApiError && ABSENT_STATUSES.includes(error.status);
}

function getClientId(): string {
  if (typeof window === "undefined") return "server";
  try {
    let id = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, id);
    }
    return id;
  } catch {
    return "anonymous";
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let detail: string = res.statusText || `Request failed (${res.status})`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") {
      const { detail: d, title } = body as { detail?: unknown; title?: unknown };
      if (typeof d === "string") detail = d;
      else if (typeof title === "string") detail = title;
    }
  } catch {
    // no JSON body
  }
  return new ApiError(detail, res.status);
}

interface SendOptions {
  token?: string | null;
  method?: string;
  signal?: AbortSignal;
}

async function json<T>(path: string, { token, method = "GET", signal }: SendOptions = {}): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": getClientId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

/** Same as `json`, but a not-deployed endpoint resolves to `null`. */
async function optional<T>(path: string, options: SendOptions): Promise<T | null> {
  try {
    return await json<T>(path, options);
  } catch (err) {
    if (isAbsent(err)) return null;
    throw err;
  }
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

const seg = encodeURIComponent;

// ---------------------------------------------------------------------------
// Visitors
// ---------------------------------------------------------------------------

export interface VisitorListParams {
  audience?: Audience;
  q?: string;
  status?: string;
  type?: string;
  sort?: string;
  limit?: number;
  offset?: number;
  /** B7.2 heatmap filter: visitors with a session starting on this weekday (0 = Mon). */
  weekday?: number | null;
  /** B7.2 heatmap filter: ... and in this hour (0 to 23). */
  hour?: number | null;
  from?: string;
  to?: string;
}

export function getVisitors(token: string, params: VisitorListParams, signal?: AbortSignal) {
  return json<PageEnvelope<VisitorRow>>(
    `/admin/users${query({
      ...params,
      status: params.status === "all" ? undefined : params.status,
      type: params.type === "all" ? undefined : params.type,
    })}`,
    { token, signal },
  );
}

export function getVisitor(token: string, visitorId: string, signal?: AbortSignal) {
  return json<VisitorDetail>(`/admin/users/${seg(visitorId)}`, { token, signal });
}

export function getVisitorStats(
  token: string,
  params: RangeParams & { audience?: Audience },
  signal?: AbortSignal,
) {
  return optional<VisitorStats>(`/admin/visitors/stats${query({ ...params })}`, { token, signal });
}

export function getVisitorTimeline(token: string, visitorId: string, params: RangeParams, signal?: AbortSignal) {
  return optional<VisitorTimeline>(`/admin/visitors/${seg(visitorId)}/timeline${query({ ...params })}`, {
    token,
    signal,
  });
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export interface ConversationListParams {
  audience?: Audience;
  q?: string;
  ip?: string;
  sort?: string;
  limit?: number;
  offset?: number;
  /** B7.2 histogram filter, inclusive. */
  min_turns?: number | null;
  max_turns?: number | null;
  /** B7.2 outcome-bar filter: conversations with a turn of this outcome ... */
  outcome?: string | null;
  /** ... on this day (`YYYY-MM-DD`). */
  day?: string | null;
  /** Global date range: conversations started in these inclusive UTC days (as the stats). */
  from?: string;
  to?: string;
}

export function getConversations(token: string, params: ConversationListParams, signal?: AbortSignal) {
  return json<PageEnvelope<ConversationRow>>(`/admin/conversations${query({ ...params })}`, { token, signal });
}

export function getConversationStats(
  token: string,
  params: RangeParams & { audience?: Audience; ip?: string; q?: string },
  signal?: AbortSignal,
) {
  return optional<ConversationStats>(`/admin/conversations/stats${query({ ...params })}`, { token, signal });
}

export function getConversation(token: string, id: string, turn: number | null, signal?: AbortSignal) {
  return json<ConversationDetail>(`/admin/conversations/${seg(id)}${query({ turn })}`, { token, signal });
}

/** Existing endpoint: `POST /admin/feedback/{assistant_message_id}/promote-to-test` (feedback:triage). */
export function promoteTurnToTest(token: string, assistantMessageId: string) {
  return json<PromoteResult>(`/admin/feedback/${seg(assistantMessageId)}/promote-to-test`, {
    token,
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

export function getDocumentCitationStats(
  token: string,
  params: RangeParams & { limit?: number },
  signal?: AbortSignal,
) {
  return optional<DocumentCitationStats>(`/documents/stats/citations${query({ ...params })}`, { token, signal });
}

export function getDocumentChunks(
  token: string,
  documentId: string,
  params: { q?: string; sort?: ChunkSort },
  signal?: AbortSignal,
) {
  const q = params.q?.trim().slice(0, MAX_CHUNK_QUERY_CHARS);
  return json<DocumentChunk[]>(
    `/documents/${seg(documentId)}/chunks${query({ q, sort: params.sort === "index" ? undefined : params.sort })}`,
    { token, signal },
  );
}

export function getLowRatedCitations(token: string, documentId: string, signal?: AbortSignal) {
  return optional<LowRatedCitations>(`/documents/${seg(documentId)}/low-rated-citations`, { token, signal });
}

// ---------------------------------------------------------------------------
// Client-side helpers (identical results whether or not the server filtered)
// ---------------------------------------------------------------------------

/** Filters and sorts chunks locally so an older backend that ignores `q` / `sort` still behaves. */
export function applyChunkQuery(chunks: readonly DocumentChunk[], q: string, sort: ChunkSort): DocumentChunk[] {
  const needle = q.trim().toLowerCase();
  const filtered = needle
    ? chunks.filter(
        (c) => c.content.toLowerCase().includes(needle) || (c.section_heading ?? "").toLowerCase().includes(needle),
      )
    : [...chunks];
  if (sort === "hits") {
    filtered.sort((a, b) => (b.hits ?? 0) - (a.hits ?? 0) || a.chunk_index - b.chunk_index);
  } else {
    filtered.sort((a, b) => a.chunk_index - b.chunk_index);
  }
  return filtered;
}

/** Turn count from a list row (B7.2 `turn_count`, else user+assistant message pairs). */
export function turnsOf(row: Pick<ConversationRow, "turn_count" | "message_count">): number {
  if (typeof row.turn_count === "number") return row.turn_count;
  return Math.ceil((row.message_count ?? 0) / 2);
}

/** Label for a turns-histogram bin: "3", "4–6" or "10+". */
export function binLabel(bin: Pick<TurnsBin, "min_turns" | "max_turns">): string {
  if (bin.max_turns === null) return `${bin.min_turns}+`;
  if (bin.max_turns === bin.min_turns) return String(bin.min_turns);
  return `${bin.min_turns}–${bin.max_turns}`;
}

/** Parses a non-negative integer URL param; anything else is null. */
export function intParam(value: string | null, max = Number.MAX_SAFE_INTEGER): number | null {
  if (value === null || !/^\d{1,9}$/.test(value)) return null;
  const n = Number(value);
  return n <= max ? n : null;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function dayParam(value: string | null): string | null {
  return value !== null && ISO_DAY.test(value) ? value : null;
}
