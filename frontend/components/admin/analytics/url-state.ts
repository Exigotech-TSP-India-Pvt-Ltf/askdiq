"use client";

/**
 * Analytics page state lives in the URL: tab, filters,
 * sort and page round-trip through `?tab=...&period=...`. Every value is
 * validated against the same allow-lists as the backend, so a hand-edited
 * URL can never send an invalid request. Writes use `history.replaceState`
 * (like the audience scope), which Next.js keeps in sync with its router.
 *
 * Inside a RangeProvider (the admin layout) the period comes from the global
 * date range picker instead: `period` / `from` / `to` are derived from it,
 * range changes are written as its `range` / `from` / `to` params, and every
 * write goes through the Next router in one step so the two never race.
 */
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  DEFAULT_PRESET,
  clampCustomRange,
  rangeFromParams,
  toIsoDay,
  useRange,
  type DateRange,
} from "@/lib/range-context";
import {
  CODE_RE,
  CONVERSATION_OUTCOMES,
  CONVERSATION_SORTS,
  DATE_RE,
  DEFAULT_PAGE_SIZE,
  EXPERIENCE_RATINGS,
  HOURS_PER_DAY,
  INSIGHTS_PERIODS,
  MAX_PAGE_SIZE,
  MAX_SEARCH_CHARS,
  MAX_USER_TYPE_CHARS,
  PERSON_RE,
  SORT_DIRECTIONS,
  VISITOR_RE,
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
  /** Visitors-page id (`client-<ip>` or a user UUID). */
  visitor: string;
  /** Hour of day (viewer zone), null = every hour. */
  hour: number | null;
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
  visitor: "",
  hour: null,
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

/** A valid hour of day (0-23), else null. */
function hourOf(value: string | null): number | null {
  if (!value) return null;
  const n = intIn(value, 0, HOURS_PER_DAY - 1, -1);
  return n < 0 ? null : n;
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
    visitor: matching(VISITOR_RE, sp.get("visitor")),
    hour: hourOf(sp.get("hour")),
    q,
    sort: oneOf(CONVERSATION_SORTS, sp.get("sort"), DEFAULT_STATE.sort),
    dir: oneOf(SORT_DIRECTIONS, sp.get("dir"), DEFAULT_STATE.dir),
    page: intIn(sp.get("page"), 1, 10_000, 1),
    limit: intIn(sp.get("limit"), 1, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE),
  };
}

/** Keys the global date range owns when a RangeProvider is mounted. */
const RANGE_KEYS = ["period", "from", "to"] as const satisfies readonly (keyof AnalyticsState)[];
const NON_RANGE_KEYS = OWNED_KEYS.filter((k) => !(RANGE_KEYS as readonly string[]).includes(k));

export interface SerializeOptions {
  /**
   * The global range owns `range` / `from` / `to`: never write or delete them
   * (nor `period`), so a filter change keeps the picked range.
   */
  rangeOwned?: boolean;
}

/** Writes the non-default values of `state` into `base` (other params kept). */
export function serializeAnalyticsState(
  state: AnalyticsState,
  base = "",
  { rangeOwned = false }: SerializeOptions = {},
): string {
  const sp = new URLSearchParams(base);
  for (const key of rangeOwned ? NON_RANGE_KEYS : OWNED_KEYS) {
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
  if (state.visitor) out.visitor = state.visitor;
  if (state.hour !== null) out.hour = state.hour;
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
  "visitor",
  "hour",
  "q",
] as const satisfies readonly (keyof AnalyticsState)[];

/**
 * The HTML's `setDay()`: a click selects that single day; clicking it again
 * clears the range (with the global picker: back to its default range).
 */
export function toggleDay(state: AnalyticsState, day: string): Partial<AnalyticsState> {
  if (state.period === "custom" && state.from === day && state.to === day) {
    return { period: "all", from: "", to: "" };
  }
  return { period: "custom", from: day, to: day };
}

/** Toggles a cross-filter (topic, quality, issue, person, hour): same value again clears it. */
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

// ---------------------------------------------------------------------------
// Global date range (RangeProvider) mapping
// ---------------------------------------------------------------------------

type RangeFields = Pick<AnalyticsState, "period" | "from" | "to">;

/** Local calendar day `YYYY-MM-DD` as a local-midnight Date. */
function localDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const PRESET_SPAN_DAYS = { "90d": 90 } as const;

/**
 * The insights period for a global range. "Last 7 / 30 days" map to the API
 * presets; "Last 24 hours", "Last 90 days" and custom ranges become a custom
 * span of local calendar days (the API reads them in the viewer's zone).
 */
export function periodFromRange(range: DateRange): RangeFields {
  if (range.kind === "7d" || range.kind === "30d") return { period: range.kind, from: "", to: "" };
  if (range.kind === "custom") return { period: "custom", from: toIsoDay(range.from), to: toIsoDay(range.to) };
  const end = range.to;
  const first =
    range.kind === "24h"
      ? range.from
      : new Date(end.getFullYear(), end.getMonth(), end.getDate() - (PRESET_SPAN_DAYS[range.kind] - 1));
  return { period: "custom", from: toIsoDay(first), to: toIsoDay(end) };
}

/**
 * Old Analytics links carried `period=all|7d|30d|custom`. Returns the search
 * rewritten for the global range (`30d` -> `range=30d`; `custom` keeps its
 * `from` / `to`; `all` / `7d` -> the default range), or null when there is
 * nothing to rewrite. "Full period" (all time) no longer exists.
 */
export function normaliseLegacyPeriod(search: string): string | null {
  const sp = new URLSearchParams(search);
  const period = sp.get("period");
  if (period === null) return null;
  sp.delete("period");
  if (period !== "custom") {
    sp.delete("from");
    sp.delete("to");
    if (period === "30d") sp.set("range", "30d");
    else sp.delete("range");
  }
  return sp.toString();
}

/** Writes `fields` as the global range's own URL params (as `setCustom` / `setPreset` do). */
function writeRangeParams(sp: URLSearchParams, fields: RangeFields): void {
  if (fields.period === "custom" && DATE_RE.test(fields.from) && DATE_RE.test(fields.to)) {
    const clamped = clampCustomRange(localDay(fields.from), localDay(fields.to));
    sp.delete("range");
    sp.set("from", toIsoDay(clamped.from));
    sp.set("to", toIsoDay(clamped.to));
    return;
  }
  const preset = fields.period === "30d" || fields.period === "7d" ? fields.period : DEFAULT_PRESET;
  sp.delete("from");
  sp.delete("to");
  if (preset === DEFAULT_PRESET) sp.delete("range");
  else sp.set("range", preset);
}

function withRange(parsed: AnalyticsState, range: DateRange | null): AnalyticsState {
  return range ? { ...parsed, ...periodFromRange(range) } : parsed;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAnalyticsUrlState(): AnalyticsUrlState {
  const ctx = useRange();
  const rangeOwned = ctx !== null;
  const windowSearch = useSyncExternalStore(subscribe, getSearch, getServerSearch);
  const router = useRouter();
  const pathname = usePathname();
  const routerSearch = useSearchParams()?.toString() ?? "";

  // With a RangeProvider the Next router is the single URL writer (the
  // provider writes through it too); without one, the old history path.
  const rawSearch = rangeOwned ? routerSearch : windowSearch.replace(/^\?/, "");
  const legacy = useMemo(() => (rangeOwned ? normaliseLegacyPeriod(rawSearch) : null), [rangeOwned, rawSearch]);
  const search = legacy ?? rawSearch;
  // Until an old `period=` link is rewritten, read the range it stands for.
  const range = useMemo(
    () => (ctx ? (legacy !== null ? rangeFromParams(new URLSearchParams(legacy)) : ctx.range) : null),
    [ctx, legacy],
  );
  const state = useMemo(() => withRange(parseAnalyticsState(search), range), [search, range]);

  // Several writes in one tick compose: each starts from the URL the
  // previous one wrote until the router re-renders with it.
  const pending = useRef<{ base: string; next: string } | null>(null);
  const latest = useRef({ search, pathname, router });
  latest.current = { search, pathname, router };

  const writeRouter = useCallback((qs: string) => {
    const { search: base, pathname: path, router: r } = latest.current;
    pending.current = { base, next: qs };
    r.replace(`${path ?? ""}${qs ? `?${qs}` : ""}`, { scroll: false });
  }, []);

  useEffect(() => {
    if (legacy !== null) writeRouter(legacy);
  }, [legacy, writeRouter]);

  const update = useCallback(
    (patch: Partial<AnalyticsState>) => {
      if (!rangeOwned) {
        const current = parseAnalyticsState(window.location.search);
        const touchesFilters = FILTER_FIELDS.some((k) => k in patch && patch[k] !== current[k]);
        const next: AnalyticsState = { ...current, ...patch };
        if (touchesFilters && !("page" in patch)) next.page = 1;
        const qs = serializeAnalyticsState(next, window.location.search);
        const url = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
        window.history.replaceState(window.history.state, "", url);
        window.dispatchEvent(new Event(URL_EVENT));
        return;
      }
      const { search: rendered } = latest.current;
      const base = pending.current?.base === rendered ? pending.current.next : rendered;
      const current = withRange(parseAnalyticsState(base), rangeFromParams(new URLSearchParams(base)));
      const touchesFilters = FILTER_FIELDS.some((k) => k in patch && patch[k] !== current[k]);
      const next: AnalyticsState = { ...current, ...patch };
      if (touchesFilters && !("page" in patch)) next.page = 1;
      const sp = new URLSearchParams(serializeAnalyticsState(next, base, { rangeOwned: true }));
      if (RANGE_KEYS.some((k) => k in patch && patch[k] !== current[k])) writeRangeParams(sp, next);
      writeRouter(sp.toString());
    },
    [rangeOwned, writeRouter],
  );

  // A range picked in the header (not through `update`) also resets the table page.
  const rangeKey = `${state.period}|${state.from}|${state.to}`;
  const seenRange = useRef(rangeKey);
  useEffect(() => {
    if (seenRange.current === rangeKey) return;
    seenRange.current = rangeKey;
    if (state.page !== 1) update({ page: 1 });
  }, [rangeKey, state.page, update]);

  const clearFilters = useCallback(() => {
    const reset: Partial<AnalyticsState> = {};
    // The global range is not one of this page's filters: Clear keeps it.
    const keys = rangeOwned ? FILTER_FIELDS.filter((k) => !(RANGE_KEYS as readonly string[]).includes(k)) : FILTER_FIELDS;
    for (const key of keys) Object.assign(reset, { [key]: DEFAULT_STATE[key] });
    update(reset);
  }, [rangeOwned, update]);

  return { state, update, clearFilters };
}
