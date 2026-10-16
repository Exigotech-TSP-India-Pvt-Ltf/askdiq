/**
 * Typed client for the feedback / RLHF loop (newplan §5.4, planchanges4 B5 + F5).
 *
 * Contract: backend `app/api/routes/admin_feedback.py`, `app/schemas/feedback.py`
 * and `app/schemas/session.py` (chat-side `POST /sessions/{sid}/messages/{mid}/feedback`).
 * Requests go through the same `/api/proxy` route as `lib/api.ts`.
 */
import { ApiError } from "./api";
import type { Audience } from "./types";

const API_BASE_URL = "/api/proxy";
const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";

// ---------------------------------------------------------------------------
// Constants mirrored from the backend schema
// ---------------------------------------------------------------------------

export const MAX_COMMENT_CHARS = 1000;
export const MAX_CORRECTED_ANSWER_CHARS = 4000;
export const MAX_REVIEW_NOTE_CHARS = 2000;
export const MAX_BULK_IDS = 200;

export const REASON_TAGS = ["wrong", "incomplete", "off_topic", "unsafe", "too_slow", "unclear"] as const;
export type ReasonTag = (typeof REASON_TAGS)[number];

export const REASON_LABELS: Record<ReasonTag, string> = {
  wrong: "Wrong",
  incomplete: "Incomplete",
  off_topic: "Off topic",
  unsafe: "Unsafe",
  too_slow: "Too slow",
  unclear: "Unclear",
};

export const TRIAGE_STATUSES = ["new", "reviewed", "promoted", "dismissed"] as const;
export type TriageStatus = (typeof TRIAGE_STATUSES)[number];

export const TRIAGE_LABELS: Record<TriageStatus, string> = {
  new: "New",
  reviewed: "Reviewed",
  promoted: "Promoted",
  dismissed: "Dismissed",
};

export const OUTCOMES = ["answered", "ungrounded", "blocked", "conversational", "llm_failed"] as const;
export type Outcome = (typeof OUTCOMES)[number];

export const FEEDBACK_SORTS = ["created_at_desc", "created_at_asc", "rating_desc", "rating_asc"] as const;
export type FeedbackSort = (typeof FEEDBACK_SORTS)[number];

export type BulkAction = "reviewed" | "dismissed" | "promoted";

/** Ratings at or above this count as positive, at or below `NEGATIVE_MAX` as negative. */
export const POSITIVE_MIN = 4;
export const NEGATIVE_MAX = 2;

export function isReasonTag(value: string): value is ReasonTag {
  return (REASON_TAGS as readonly string[]).includes(value);
}

export function reasonLabel(tag: string): string {
  return isReasonTag(tag) ? REASON_LABELS[tag] : tag.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Response shapes
// ---------------------------------------------------------------------------

export interface FeedbackSource {
  source_name: string | null;
  section_heading: string | null;
  score: number | null;
  chunk_id: string | null;
}

export interface FeedbackItem {
  id: string;
  rating: number;
  comment: string;
  ip_address: string;
  conversation_id: string;
  message_id: string;
  created_at: string | null;
  updated_at: string | null;
  actor: string;
  user_query: string | null;
  answer: string | null;
  outcome: string | null;
  grounded: boolean | null;
  guardrail_flags: string[];
  sources: FeedbackSource[];
  confidence: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  reason_tags: string[];
  corrected_answer: string | null;
  triage_status: TriageStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  promoted_case_id: string | null;
  version: number;
}

export interface FeedbackPage {
  items: FeedbackItem[];
  total: number;
  limit: number;
  offset: number;
  next_cursor: string | null;
}

export interface OutcomeBucket {
  positive: number;
  negative: number;
  total: number;
}

export interface TrendPoint {
  day: string;
  total: number;
  positive: number;
  negative: number;
  avg_rating: number | null;
}

export interface CountItem {
  key: string;
  count: number;
}

export interface DisagreementQuadrant {
  positive_grounded: number;
  positive_ungrounded: number;
  negative_grounded: number;
  negative_ungrounded: number;
}

export interface KnowledgeGap {
  query: string;
  count: number;
  avg_rating: number;
  avg_confidence: number | null;
  last_seen: string | null;
  message_id: string;
}

export interface FeedbackAnalytics {
  /** Unfiltered total for the audience (F3): the KPI uses this, never the list total. */
  total: number;
  total_feedback: number;
  positive_count: number;
  negative_count: number;
  neutral_count: number;
  positive_ratio: number;
  negative_ratio: number;
  feedback_rate: number;
  feedback_by_outcome: Record<string, OutcomeBucket>;
  daily_trend: TrendPoint[];
  rating_distribution: Record<string, number>;
  /** Present only once turn insights exist (B6). */
  by_intent?: CountItem[];
  top_reasons: CountItem[];
  triage_funnel: Record<string, number>;
  disagreement: DisagreementQuadrant;
  knowledge_gaps: KnowledgeGap[];
}

export interface GoldenRunDetail {
  grounded?: boolean | null;
  confidence?: number | null;
  guardrail_flags?: string[];
  latency_ms?: number | null;
  error?: string | null;
}

export interface GoldenCase {
  id: string;
  query: string;
  case_type: string;
  expect_grounded: boolean;
  expected_outcome: string | null;
  source_message_id: string | null;
  feedback_id: string | null;
  reference_answer: string | null;
  reason_tags: string[];
  promoted_by: string;
  promoted_at: string;
  last_run_at: string | null;
  last_run_passed: boolean | null;
  last_run_detail: GoldenRunDetail | null;
}

export interface GoldenCasePage {
  items: GoldenCase[];
  total: number;
  limit: number;
  offset: number;
}

export interface GoldenRunResponse {
  case_id: string;
  status: "queued" | "already_queued";
  job_id: string | null;
}

export interface FeedbackBulkResponse {
  action: BulkAction;
  updated: number;
  ids: string[];
  promoted_case_ids: string[];
}

export interface PromoteResponse {
  status: "promoted";
  case_id: string;
  query: string;
  created: boolean;
  feedback_ids: string[];
}

export interface UserFeedbackResponse {
  message_id: string;
  session_id: string;
  rating: number;
  comment: string | null;
  reason_tags: string[];
  corrected_answer: string | null;
  triage_status: string;
  created_at: string;
  updated_at: string;
}

/** One preference pair line of `GET /admin/feedback/export?format=jsonl`. */
export interface PreferencePair {
  feedback_id: string;
  message_id: string;
  prompt: string | null;
  chosen: string | null;
  rejected: string | null;
  rating: number;
  reason_tags: string[];
  source_ids: string[];
  triage_status: string;
  complete: boolean;
  missing: string[];
}

// ---------------------------------------------------------------------------
// Request plumbing (same proxy pattern as lib/api.ts)
// ---------------------------------------------------------------------------

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

function headers(token: string | null | undefined, json: boolean): Record<string, string> {
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    "X-Client-Id": getClientId(),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
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

async function send(
  path: string,
  { token, method = "GET", body, signal }: { token?: string | null; method?: string; body?: unknown; signal?: AbortSignal },
): Promise<Response> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    signal,
    headers: headers(token, body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await errorFrom(res);
  return res;
}

async function json<T>(path: string, options: Parameters<typeof send>[1]): Promise<T> {
  const res = await send(path, options);
  return (await res.json()) as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Chat side
// ---------------------------------------------------------------------------

/** Thumbs-down detail sent with a rating (newplan §5.4 chat UI). */
export interface FeedbackDetails {
  reason_tags?: ReasonTag[];
  corrected_answer?: string | null;
}

export interface MessageFeedbackPayload extends FeedbackDetails {
  rating: number;
  comment?: string | null;
}

/** Upserts the caller's rating of one assistant message (a resubmission updates it). */
export function submitMessageFeedback(
  token: string,
  sessionId: string,
  messageId: string,
  payload: MessageFeedbackPayload,
): Promise<UserFeedbackResponse> {
  const corrected = payload.corrected_answer?.trim();
  return json<UserFeedbackResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      token,
      method: "POST",
      body: {
        rating: payload.rating,
        comment: payload.comment?.trim() ? payload.comment.trim() : null,
        reason_tags: Array.from(new Set(payload.reason_tags ?? [])),
        corrected_answer: corrected ? corrected.slice(0, MAX_CORRECTED_ANSWER_CHARS) : null,
      },
    },
  );
}

// ---------------------------------------------------------------------------
// Admin side
// ---------------------------------------------------------------------------

export interface FeedbackListParams {
  audience?: Audience;
  rating?: number;
  sort?: FeedbackSort;
  q?: string;
  triage_status?: TriageStatus;
  outcome?: Outcome;
  grounded?: boolean;
  has_comment?: boolean;
  date_from?: string;
  date_to?: string;
  message_id?: string;
  limit?: number;
  cursor?: string | null;
}

export function feedbackListPath(params: FeedbackListParams): string {
  return `/admin/feedback${query({ ...params })}`;
}

export function listFeedback(token: string, params: FeedbackListParams, signal?: AbortSignal): Promise<FeedbackPage> {
  return json<FeedbackPage>(feedbackListPath(params), { token, signal });
}

export function getFeedbackAnalytics(token: string, audience: Audience, signal?: AbortSignal): Promise<FeedbackAnalytics> {
  return json<FeedbackAnalytics>(`/admin/feedback/analytics${query({ audience })}`, { token, signal });
}

export function listGoldenCases(
  token: string,
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<GoldenCasePage> {
  return json<GoldenCasePage>(`/admin/feedback/golden${query(params)}`, { token, signal });
}

export function runGoldenCase(token: string, caseId: string): Promise<GoldenRunResponse> {
  return json<GoldenRunResponse>(`/admin/feedback/golden/${encodeURIComponent(caseId)}/run`, {
    token,
    method: "POST",
  });
}

export function bulkTriage(
  token: string,
  body: { ids: string[]; action: BulkAction; review_note?: string | null },
): Promise<FeedbackBulkResponse> {
  return json<FeedbackBulkResponse>("/admin/feedback/bulk", {
    token,
    method: "POST",
    body: { ...body, ids: body.ids.slice(0, MAX_BULK_IDS) },
  });
}

export interface TriagePatch {
  version: number;
  triage_status?: TriageStatus;
  review_note?: string | null;
  /** Reviewer-written answer (the `chosen` side of a pair); `null` clears it. */
  corrected_answer?: string | null;
}

/** PATCH /admin/feedback/{id}: a stale `version` returns 409. */
export function triageFeedback(token: string, feedbackId: string, body: TriagePatch): Promise<FeedbackItem> {
  return json<FeedbackItem>(`/admin/feedback/${encodeURIComponent(feedbackId)}`, {
    token,
    method: "PATCH",
    body,
  });
}

export function promoteToTest(token: string, messageId: string, reviewNote?: string | null): Promise<PromoteResponse> {
  return json<PromoteResponse>(`/admin/feedback/${encodeURIComponent(messageId)}/promote-to-test`, {
    token,
    method: "POST",
    body: reviewNote ? { review_note: reviewNote } : undefined,
  });
}

export interface ExportParams {
  triage_status?: TriageStatus;
  include_dismissed?: boolean;
  complete_only?: boolean;
}

function exportPath(params: ExportParams): string {
  return `/admin/feedback/export${query({ format: "jsonl", ...params })}`;
}

/** Parses JSONL text into pairs, skipping blank or malformed lines. */
export function parsePairs(text: string): PreferencePair[] {
  const out: PreferencePair[] = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      out.push(JSON.parse(trimmed) as PreferencePair);
    } catch {
      // partial trailing line of a cut-off preview stream
    }
  }
  return out;
}

/**
 * Reads the first `limit` pairs of the export stream, then cancels it so a
 * large export is never fully downloaded just for the preview.
 */
export async function previewPairs(
  token: string,
  params: ExportParams,
  limit: number,
  signal?: AbortSignal,
): Promise<{ pairs: PreferencePair[]; truncated: boolean }> {
  const res = await send(exportPath(params), { token, signal });
  const reader = res.body?.getReader?.();
  if (!reader) {
    const all = parsePairs(await res.text());
    return { pairs: all.slice(0, limit), truncated: all.length > limit };
  }
  const decoder = new TextDecoder();
  let buffer = "";
  let lines = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    buffer += chunk;
    lines += (chunk.match(/\n/g) ?? []).length;
    if (lines > limit) {
      await reader.cancel().catch(() => undefined);
      const pairs = parsePairs(buffer.split("\n").slice(0, limit).join("\n"));
      return { pairs, truncated: true };
    }
  }
  buffer += decoder.decode();
  return { pairs: parsePairs(buffer).slice(0, limit), truncated: false };
}

function filenameFrom(res: Response): string {
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return match?.[1] ?? "feedback_pairs.jsonl";
}

/** Downloads the full JSONL export as a file (blob + temporary anchor). */
export async function downloadPairs(token: string, params: ExportParams): Promise<string> {
  const res = await send(exportPath(params), { token });
  const blob = await res.blob();
  const filename = filenameFrom(res);
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  return filename;
}

// ---------------------------------------------------------------------------
// Cache keys (lib/query-cache)
// ---------------------------------------------------------------------------

export const FEEDBACK_KEY_PREFIX = "admin/feedback";
export const feedbackKeys = {
  analytics: (audience: Audience) => `${FEEDBACK_KEY_PREFIX}/analytics/${audience}`,
  list: (path: string) => `${FEEDBACK_KEY_PREFIX}/list${path}`,
  listPrefix: `${FEEDBACK_KEY_PREFIX}/list`,
  golden: `${FEEDBACK_KEY_PREFIX}/golden`,
  pairs: (complete: boolean) => `${FEEDBACK_KEY_PREFIX}/pairs/${complete ? "complete" : "all"}`,
} as const;
