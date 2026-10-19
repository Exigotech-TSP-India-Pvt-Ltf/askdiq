/**
 * Review-queue filters for the Feedback page (newplan §5.4 "Clickable
 * analytics"). Every chart mark sets one of these; they live in the URL so a
 * filtered queue can be shared and survives back / forward.
 *
 * `reason` and `sentiment` have no list-API parameter yet, so they narrow the
 * loaded page on the client (`matchesClientFilters`).
 */
import {
  FEEDBACK_SORTS,
  NEGATIVE_MAX,
  OUTCOMES,
  POSITIVE_MIN,
  TRIAGE_LABELS,
  TRIAGE_STATUSES,
  isReasonTag,
  reasonLabel,
  type FeedbackItem,
  type FeedbackListParams,
  type FeedbackSort,
  type Outcome,
  type ReasonTag,
  type TriageStatus,
} from "./feedback-api";

export type Sentiment = "positive" | "negative";
export type StatusFilter = TriageStatus | "all";

export interface FeedbackFilters {
  rating?: number;
  outcome?: Outcome;
  /** Defaults to "new": the queue is the work still to triage. */
  status: StatusFilter;
  grounded?: boolean;
  /** `YYYY-MM-DD` (a trend point). */
  day?: string;
  reason?: ReasonTag;
  sentiment?: Sentiment;
  message_id?: string;
  q?: string;
  has_comment?: boolean;
  sort: FeedbackSort;
}

export const DEFAULT_STATUS: StatusFilter = "new";
export const DEFAULT_SORT: FeedbackSort = "created_at_desc";

/** URL keys owned by the filters (also cleared by "Clear all"). */
export const FILTER_URL_KEYS = [
  "rating",
  "outcome",
  "status",
  "grounded",
  "day",
  "reason",
  "sentiment",
  "message_id",
  "q",
  "has_comment",
  "sort",
] as const;
export type FilterUrlKey = (typeof FILTER_URL_KEYS)[number];

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const UUID_PATTERN = /^[0-9a-fA-F-]{8,64}$/;
const MAX_Q = 200;

function bool(raw: string | null): boolean | undefined {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return undefined;
}

/** Reads and validates the filters from URL params (unknown values are ignored). */
export function parseFilters(params: URLSearchParams): FeedbackFilters {
  const rating = Number(params.get("rating"));
  const outcome = params.get("outcome");
  const status = params.get("status");
  const day = params.get("day");
  const reason = params.get("reason");
  const sentiment = params.get("sentiment");
  const messageId = params.get("message_id");
  const q = params.get("q")?.trim();
  const sort = params.get("sort");
  return {
    rating: Number.isInteger(rating) && rating >= 1 && rating <= 5 ? rating : undefined,
    outcome: outcome && (OUTCOMES as readonly string[]).includes(outcome) ? (outcome as Outcome) : undefined,
    // A deep link to one message must find it whatever its triage status.
    status:
      status === "all" || (status && (TRIAGE_STATUSES as readonly string[]).includes(status))
        ? (status as StatusFilter)
        : messageId && UUID_PATTERN.test(messageId)
          ? "all"
          : DEFAULT_STATUS,
    grounded: bool(params.get("grounded")),
    day: day && DAY_PATTERN.test(day) ? day : undefined,
    reason: reason && isReasonTag(reason) ? reason : undefined,
    sentiment: sentiment === "positive" || sentiment === "negative" ? sentiment : undefined,
    message_id: messageId && UUID_PATTERN.test(messageId) ? messageId : undefined,
    q: q ? q.slice(0, MAX_Q) : undefined,
    has_comment: bool(params.get("has_comment")),
    sort: sort && (FEEDBACK_SORTS as readonly string[]).includes(sort) ? (sort as FeedbackSort) : DEFAULT_SORT,
  };
}

export type FilterPatch = Partial<Record<FilterUrlKey, string | number | boolean | null>>;

/** Converts a patch to deep-link updates (defaults are removed from the URL). */
export function patchToUrl(patch: FilterPatch): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || value === "") out[key] = null;
    else if (key === "status" && value === DEFAULT_STATUS) out[key] = null;
    else if (key === "sort" && value === DEFAULT_SORT) out[key] = null;
    else out[key] = String(value);
  }
  return out;
}

/**
 * List-API parameters for the server-side filters. A day picked from a chart
 * (`f.day`) wins; otherwise `days` (the global date range, inclusive UTC days)
 * bounds the ratings.
 */
export function toListParams(
  f: FeedbackFilters,
  days: { from?: string; to?: string } = {},
): Omit<FeedbackListParams, "audience" | "limit" | "cursor"> {
  const first = f.day ?? days.from;
  const last = f.day ?? days.to;
  return {
    rating: f.rating,
    outcome: f.outcome,
    triage_status: f.status === "all" ? undefined : f.status,
    grounded: f.grounded,
    date_from: first ? `${first}T00:00:00Z` : undefined,
    date_to: last ? `${last}T23:59:59Z` : undefined,
    message_id: f.message_id,
    q: f.q,
    has_comment: f.has_comment,
    sort: f.sort,
  };
}

export function hasClientFilters(f: FeedbackFilters): boolean {
  return Boolean(f.reason || f.sentiment);
}

/** Filters the API cannot apply yet (reason tag, sentiment band). */
export function matchesClientFilters(item: FeedbackItem, f: FeedbackFilters): boolean {
  if (f.reason && !item.reason_tags.includes(f.reason)) return false;
  if (f.sentiment === "positive" && item.rating < POSITIVE_MIN) return false;
  if (f.sentiment === "negative" && item.rating > NEGATIVE_MAX) return false;
  return true;
}

/** Whether an item still belongs in the queue after a triage change. */
export function matchesFilters(item: FeedbackItem, f: FeedbackFilters): boolean {
  if (f.status !== "all" && item.triage_status !== f.status) return false;
  return matchesClientFilters(item, f);
}

export interface FilterChip {
  key: FilterUrlKey;
  label: string;
  /** Value written when the chip is removed (default: remove the key). */
  resetTo?: string;
}

const OUTCOME_LABELS: Record<string, string> = {
  answered: "Answered",
  ungrounded: "Ungrounded",
  blocked: "Blocked",
  conversational: "Conversational",
  llm_failed: "LLM failed",
  empty_llm_output: "Empty LLM reply",
};

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "Unknown";
  return OUTCOME_LABELS[outcome] ?? outcome.replace(/_/g, " ");
}

/** Removable chips for the active filters (the default status shows too, so it can be widened). */
export function activeChips(f: FeedbackFilters): FilterChip[] {
  const chips: FilterChip[] = [];
  if (f.status !== "all") chips.push({ key: "status", label: `Status: ${TRIAGE_LABELS[f.status]}`, resetTo: "all" });
  if (f.rating !== undefined) chips.push({ key: "rating", label: `Rating: ${f.rating}` });
  if (f.sentiment) chips.push({ key: "sentiment", label: f.sentiment === "positive" ? "Positive (4-5)" : "Negative (1-2)" });
  if (f.outcome) chips.push({ key: "outcome", label: `Outcome: ${outcomeLabel(f.outcome)}` });
  if (f.grounded !== undefined) chips.push({ key: "grounded", label: f.grounded ? "Grounded" : "Ungrounded" });
  if (f.day) chips.push({ key: "day", label: `Day: ${f.day}` });
  if (f.reason) chips.push({ key: "reason", label: `Reason: ${reasonLabel(f.reason)}` });
  if (f.has_comment !== undefined) chips.push({ key: "has_comment", label: f.has_comment ? "Has comment" : "No comment" });
  if (f.message_id) chips.push({ key: "message_id", label: `Message: ${f.message_id.slice(0, 8)}` });
  if (f.q) chips.push({ key: "q", label: `Search: ${f.q}` });
  return chips;
}
