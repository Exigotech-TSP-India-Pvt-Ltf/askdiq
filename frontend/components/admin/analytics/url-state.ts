"use client";

/**
 * Analytics page state lives in the URL (planchanges4 F6.1): tab, filters,
 * sort and page round-trip through `?tab=...&period=...`. Every value is
 * validated against the same allow-lists as the backend, so a hand-edited
 * URL can never send an invalid request. Writes use `history.replaceState`
 * (like the audience scope), which Next.js keeps in sync with its router.
 */
import { useCallback, useMemo, useSyncExternalStore } from "react";
import {
  CODE_RE,
  CONVERSATION_OUTCOMES,
  CONVERSATION_SORTS,
  DATE_RE,
  DEFAULT_PAGE_SIZE,
  EXPERIENCE_RATINGS,
  INSIGHTS_PERIODS,
  MAX_PAGE_SIZE,
  MAX_SEARCH_CHARS,
  MAX_USER_TYPE_CHARS,
  PERSON_RE,
  SORT_DIRECTIONS,
  type ConversationOutcome,
  type ConversationSort,
  type ExperienceRating,
  type InsightsFilters,
  type InsightsPeriod,
  type SortDirection,
} from "@/lib/insights-api";

export const ANALYTICS_TAB_IDS = ["guide", "overview", "trend", "flow", "topics", "dropoff", "conversations"] as const;
export type AnalyticsTabId = (typeof ANALYTICS_TAB_IDS)[number];
export const DEFAULT_TAB: AnalyticsTabId = "overview";

export interface AnalyticsState {
  tab: AnalyticsTabId;
  period: InsightsPeriod;
  from: string;
  to: string;
  user_type: string;
  outcome: ConversationOutcome | "";
  rating: ExperienceRating | "";
  issue: string;
  intent: string;
  quality: string;
  person: string;
  q: string;
  sort: ConversationSort;
  dir: SortDirection;
  /** 1-based page of the Conversations table. */
  page: number;
  limit: number;
}

export const DEFAULT_STATE: AnalyticsState = {
  tab: DEFAULT_TAB,
  period: "all",
  from: "",
  to: "",
  user_type: "",
  outcome: "",
  rating: "",
  issue: "",
  intent: "",
  quality: "",
  person: "",
  q: "",
  sort: "start",
  dir: "desc",
  page: 1,
  limit: DEFAULT_PAGE_SIZE,
};

/** URL keys owned by this page (anything else, e.g. `audience`, is preserved). */
const OWNED_KEYS = Object.keys(DEFAULT_STATE) as (keyof AnalyticsState)[];

function oneOf<T extends string>(allowed: readonly T[], value: string | null, fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function matching(re: RegExp, value: string | null): string {
  return value !== null && re.test(value) ? value : "";
}

function isRealDate(value: string): boolean {
  return DATE_RE.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function intIn(value: string | null, min: number, max: number, fallback: number): number {
  const n = Number(value);
  return value !== null && Number.isInteger(n) && n >= min && n <= max ? n : fallback;
}

const FIRST_PRINTABLE = 32;

/** The backend rejects ASCII control characters in `q`. */
export function stripControlChars(value: string): string {
  return Array.from(value)
    .filter((ch) => ch.charCodeAt(0) >= FIRST_PRINTABLE)
    .join("");
}

/** Parses and validates the query string. Invalid values fall back to defaults. */
export function parseAnalyticsState(search: string): AnalyticsState {
  const sp = new URLSearchParams(search);
  let period = oneOf(INSIGHTS_PERIODS, sp.get("period"), "all");
  const from = isRealDate(sp.get("from") ?? "") ? (sp.get("from") as string) : "";
  const to = isRealDate(sp.get("to") ?? "") ? (sp.get("to") as string) : "";
  if (period === "custom" && (!from || !to || from > to)) period = "all";
  const userType = (sp.get("user_type") ?? "").trim();
  const q = stripControlChars(sp.get("q") ?? "").slice(0, MAX_SEARCH_CHARS);
  return {
    tab: oneOf(ANALYTICS_TAB_IDS, sp.get("tab"), DEFAULT_TAB),
    period,
    from: period === "custom" ? from : "",
    to: period === "custom" ? to : "",
    user_type: userType.length <= MAX_USER_TYPE_CHARS ? userType : "",
    outcome: oneOf<ConversationOutcome | "">(CONVERSATION_OUTCOMES, sp.get("outcome"), ""),
    rating: oneOf<ExperienceRating | "">(EXPERIENCE_RATINGS, sp.get("rating"), ""),
    issue: matching(CODE_RE, sp.get("issue")),
    intent: matching(CODE_RE, sp.get("intent")),
    quality: matching(CODE_RE, sp.get("quality")),
    person: matching(PERSON_RE, sp.get("person")),
    q,
    sort: oneOf(CONVERSATION_SORTS, sp.get("sort"), DEFAULT_STATE.sort),
    dir: oneOf(SORT_DIRECTIONS, sp.get("dir"), DEFAULT_STATE.dir),
    page: intIn(sp.get("page"), 1, 10_000, 1),
    limit: intIn(sp.get("limit"), 1, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE),
  };
}

/** Writes the non-default values of `state` into `base` (other params kept). */
export function serializeAnalyticsState(state: AnalyticsState, base = ""): string {
  const sp = new URLSearchParams(base);
  for (const key of OWNED_KEYS) {
    const value = String(state[key]);
    if (value === "" || value === String(DEFAULT_STATE[key])) sp.delete(key);
    else sp.set(key, value);
  }
  return sp.toString();
}

/** API filters from the URL state (the viewer's audience and time zone added by the caller). */
export function toInsightsFilters(state: AnalyticsState): InsightsFilters {
  const out: InsightsFilters = { period: state.period };
  if (state.period === "custom") {
    out.from = state.from;
    out.to = state.to;
  }
  if (state.user_type) out.user_type = state.user_type;
  if (state.outcome) out.outcome = state.outcome;
  if (state.rating) out.rating = state.rating;
  if (state.issue) out.issue = state.issue;
  if (state.intent) out.intent = state.intent;
  if (state.quality) out.quality = state.quality;
  if (state.person) out.person = state.person;
  if (state.q) out.q = state.q;
  return out;
}

/** Filter fields; changing any of them resets the table to page 1. */
export const FILTER_FIELDS = [
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
  "q",
] as const satisfies readonly (keyof AnalyticsState)[];

/** The HTML's `setDay()`: a click selects that single day; clicking it again clears the range. */
export function toggleDay(state: AnalyticsState, day: string): Partial<AnalyticsState> {
  if (state.period === "custom" && state.from === day && state.to === day) {
    return { period: "all", from: "", to: "" };
  }
  return { period: "custom", from: day, to: day };
}

/** Toggles a cross-filter (topic, quality, issue, person): same value again clears it. */
export function toggleValue<K extends keyof AnalyticsState>(
  state: AnalyticsState,
  key: K,
  value: AnalyticsState[K],
): Partial<AnalyticsState> {
  return { [key]: state[key] === value ? DEFAULT_STATE[key] : value } as Partial<AnalyticsState>;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

const URL_EVENT = "analytics:urlchange";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(URL_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(URL_EVENT, onChange);
  };
}

const getSearch = () => window.location.search;
const getServerSearch = () => "";

export interface AnalyticsUrlState {
  state: AnalyticsState;
  /** Merges `patch`; a filter change resets the table page. */
  update: (patch: Partial<AnalyticsState>) => void;
  clearFilters: () => void;
}

export function useAnalyticsUrlState(): AnalyticsUrlState {
  const search = useSyncExternalStore(subscribe, getSearch, getServerSearch);
  const state = useMemo(() => parseAnalyticsState(search), [search]);

  const update = useCallback((patch: Partial<AnalyticsState>) => {
    const current = parseAnalyticsState(window.location.search);
    const touchesFilters = FILTER_FIELDS.some((k) => k in patch && patch[k] !== current[k]);
    const next: AnalyticsState = { ...current, ...patch };
    if (touchesFilters && !("page" in patch)) next.page = 1;
    const qs = serializeAnalyticsState(next, window.location.search);
    const url = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
    window.history.replaceState(window.history.state, "", url);
    window.dispatchEvent(new Event(URL_EVENT));
  }, []);

  const clearFilters = useCallback(() => {
    const reset: Partial<AnalyticsState> = {};
    for (const key of FILTER_FIELDS) Object.assign(reset, { [key]: DEFAULT_STATE[key] });
    update(reset);
  }, [update]);

  return { state, update, clearFilters };
}
