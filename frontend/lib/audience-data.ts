"use client";

/**
 * Data of the Visitors & Conversations page and its detail pages: every query
 * key and fetcher is built here, so the panels, the console preload
 * (`route-prefetch`) and the hover / idle prefetches always share one key.
 *
 * - `warmAudiencePage`: both tabs' default views (console preload).
 * - `useIdlePrefetch`: what the next click most likely needs (next page,
 *   sort toggles, other audiences and range presets), warmed one request at
 *   a time while the browser is idle.
 * - `useRowIntent`: hovering a table row warms its detail page / preview.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { INTENT_DELAY_MS, prefetch, runWhenIdle, serializeKey, type Fetcher, type QueryKey } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange, rangeUtcDays } from "@/lib/range-context";
import {
  getConversation,
  getConversationStats,
  getConversations,
  getVisitor,
  getVisitorStats,
  getVisitorTimeline,
  getVisitors,
  type ConversationDetail,
  type ConversationRow,
  type ConversationStats,
  type RangeParams,
  type VisitorDetail,
  type VisitorRow,
  type VisitorStats,
  type VisitorTimeline,
} from "@/lib/entity-stats-api";
import type { Audience, PageEnvelope } from "@/lib/types";

export interface Query<T> {
  key: QueryKey;
  fetch: Fetcher<T>;
}

export const AUDIENCES: readonly Audience[] = ["overview", "users", "testing"];
export const AUDIENCE_PAGE_SIZE = 10;
export const VISITOR_DEFAULT_SORT = "last_active_desc";
export const CONVERSATION_DEFAULT_SORT = "last_activity_desc";
export const VISITOR_DEFAULT_FILTERS: { status: string; type: string } = { status: "all", type: "all" };
/** Sortable columns of each table (DataTable toggles `<key>_desc` / `<key>_asc`). */
export const VISITOR_SORT_COLUMNS = ["sessions", "queries", "first_seen", "last_active"] as const;
export const CONVERSATION_SORT_COLUMNS = ["created_at", "last_activity"] as const;

/** The sort a click on `column`'s header selects (same rule as DataTable). */
export function nextSort(current: string, column: string): string {
  return current === `${column}_desc` ? `${column}_asc` : `${column}_desc`;
}

// ---------------------------------------------------------------------------
// Visitors
// ---------------------------------------------------------------------------

export interface VisitorListState {
  audience: Audience;
  search: string;
  status: string;
  type: string;
  sort: string;
  pageSize: number;
  offset: number;
  heat: { weekday: number; hour: number } | null;
  range: RangeParams;
}

export function visitorListQuery(token: string, s: VisitorListState): Query<PageEnvelope<VisitorRow>> {
  const { heat, range } = s;
  return {
    key: [
      "visitors",
      "list",
      s.audience,
      s.search,
      s.status,
      s.type,
      s.sort,
      s.pageSize,
      s.offset,
      heat?.weekday,
      heat?.hour,
      heat ? range.from : null,
      heat ? range.to : null,
    ],
    fetch: (signal) =>
      getVisitors(
        token,
        {
          audience: s.audience,
          q: s.search || undefined,
          status: s.status,
          type: s.type,
          sort: s.sort,
          limit: s.pageSize,
          offset: s.offset,
          ...(heat ? { weekday: heat.weekday, hour: heat.hour, from: range.from, to: range.to } : {}),
        },
        signal,
      ),
  };
}

export function visitorStatsQuery(token: string, audience: Audience, range: RangeParams): Query<VisitorStats | null> {
  return {
    key: ["visitors", "stats", audience, range.from, range.to],
    fetch: (signal) => getVisitorStats(token, { audience, from: range.from, to: range.to }, signal),
  };
}

export function visitorQuery(token: string, id: string): Query<VisitorDetail> {
  return { key: ["visitor", id], fetch: (signal) => getVisitor(token, id, signal) };
}

export function visitorTimelineQuery(token: string, id: string, range: RangeParams): Query<VisitorTimeline | null> {
  return {
    key: ["visitor", id, "timeline", range.from, range.to],
    fetch: (signal) => getVisitorTimeline(token, id, { from: range.from, to: range.to }, signal),
  };
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

export interface ConversationListState {
  audience: Audience;
  search: string;
  ip: string | null;
  minTurns: number | null;
  maxTurns: number | null;
  outcome: string | null;
  day: string | null;
  range: RangeParams;
  sort: string;
  pageSize: number;
  offset: number;
}

export function conversationListQuery(token: string, s: ConversationListState): Query<PageEnvelope<ConversationRow>> {
  return {
    key: [
      "conversations",
      "list",
      s.audience,
      s.search,
      s.ip,
      s.minTurns,
      s.maxTurns,
      s.outcome,
      s.day,
      s.range.from,
      s.range.to,
      s.sort,
      s.pageSize,
      s.offset,
    ],
    fetch: (signal) =>
      getConversations(
        token,
        {
          audience: s.audience,
          q: s.search || undefined,
          ip: s.ip ?? undefined,
          min_turns: s.minTurns,
          max_turns: s.maxTurns,
          outcome: s.outcome,
          day: s.day,
          // The list matches the stats: conversations started in the range.
          from: s.range.from,
          to: s.range.to,
          sort: s.sort,
          limit: s.pageSize,
          offset: s.offset,
        },
        signal,
      ),
  };
}

export interface ConversationStatsState {
  audience: Audience;
  ip: string | null;
  search: string;
  range: RangeParams;
}

export function conversationStatsQuery(token: string, s: ConversationStatsState): Query<ConversationStats | null> {
  return {
    key: ["conversations", "stats", s.audience, s.ip, s.search, s.range.from, s.range.to],
    fetch: (signal) =>
      getConversationStats(
        token,
        { audience: s.audience, ip: s.ip ?? undefined, q: s.search || undefined, from: s.range.from, to: s.range.to },
        signal,
      ),
  };
}

/** One conversation (detail page and preview drawer share the key; `turn` only echoes `focus_turn`). */
export function conversationQuery(token: string, id: string, turn: number | null = null): Query<ConversationDetail> {
  return { key: ["conversation", id, "detail"], fetch: (signal) => getConversation(token, id, turn, signal) };
}

// ---------------------------------------------------------------------------
// Defaults and prefetch
// ---------------------------------------------------------------------------

export function defaultVisitorList(audience: Audience, range: RangeParams = {}): VisitorListState {
  return {
    audience,
    search: "",
    ...VISITOR_DEFAULT_FILTERS,
    sort: VISITOR_DEFAULT_SORT,
    pageSize: AUDIENCE_PAGE_SIZE,
    offset: 0,
    heat: null,
    range,
  };
}

export function defaultConversationList(audience: Audience, range: RangeParams): ConversationListState {
  return {
    audience,
    search: "",
    ip: null,
    minTurns: null,
    maxTurns: null,
    outcome: null,
    day: null,
    range,
    sort: CONVERSATION_DEFAULT_SORT,
    pageSize: AUDIENCE_PAGE_SIZE,
    offset: 0,
  };
}

/** Every query of both tabs' default view (what the first click on the page reads). */
export function audiencePageQueries(token: string, audience: Audience, range: RangeParams): Query<unknown>[] {
  return [
    visitorStatsQuery(token, audience, range),
    visitorListQuery(token, defaultVisitorList(audience, range)),
    conversationStatsQuery(token, { audience, ip: null, search: "", range }),
    conversationListQuery(token, defaultConversationList(audience, range)),
  ];
}

type Warm = (key: QueryKey, fetcher: Fetcher<unknown>) => void;

/** Console preload of the page: both tabs' default views. */
export function warmAudiencePage(warm: Warm, token: string, audience: Audience, range: RangeParams): void {
  for (const q of audiencePageQueries(token, audience, range)) warm(q.key, q.fetch);
}

/** The global range presets as inclusive UTC days (what a preset click selects). */
export function presetDayRanges(now: Date = new Date()): RangeParams[] {
  return RANGE_PRESETS.map((preset) => rangeUtcDays(presetRange(preset, now)));
}

/** Prefetch hit on a range other than the current one. */
export function isOtherRange(range: RangeParams, current: RangeParams): boolean {
  return range.from !== current.from || range.to !== current.to;
}

/**
 * Warms `queries` one at a time while the browser is idle (after the
 * visible data, so it never competes with it). Re-runs when the set of keys
 * changes; a change cancels the rest of the previous run.
 */
export function useIdlePrefetch(queries: readonly Query<unknown>[] | null, enabled = true): void {
  const latest = useRef(queries);
  latest.current = queries;
  const signature = queries ? queries.map((q) => serializeKey(q.key)).join("|") : "";
  useEffect(() => {
    const list = latest.current;
    if (!enabled || !list || list.length === 0) return undefined;
    let cancelled = false;
    let cancelIdle: () => void = () => undefined;
    const step = (i: number) => {
      if (cancelled || i >= list.length) return;
      cancelIdle = runWhenIdle(() => {
        if (cancelled) return;
        void prefetch(list[i].key, list[i].fetch).finally(() => step(i + 1));
      });
    };
    step(0);
    return () => {
      cancelled = true;
      cancelIdle();
    };
  }, [signature, enabled]);
}

/**
 * Hover / focus intent on table rows, delegated from the table wrapper (the
 * shared DataTable stays untouched): after `INTENT_DELAY_MS` on a body row,
 * `onIntent(rowIndex)` warms that row's next view.
 */
export function useRowIntent(onIntent: (rowIndex: number) => void, delay = INTENT_DELAY_MS) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const row = useRef<number>(-1);
  const intentRef = useRef(onIntent);
  intentRef.current = onIntent;

  const cancel = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    row.current = -1;
  }, []);

  const track = useCallback(
    (target: EventTarget | null) => {
      const tr = target instanceof Element ? target.closest("tbody tr") : null;
      const body = tr?.parentElement;
      const index = tr && body ? Array.prototype.indexOf.call(body.children, tr) : -1;
      if (index === row.current) return;
      cancel();
      if (index < 0) return;
      row.current = index;
      timer.current = setTimeout(() => {
        timer.current = null;
        intentRef.current(index);
      }, delay);
    },
    [cancel, delay],
  );

  useEffect(() => cancel, [cancel]);

  return useMemo(
    () => ({
      onPointerOver: (e: { target: EventTarget | null }) => track(e.target),
      onFocus: (e: { target: EventTarget | null }) => track(e.target),
      onPointerLeave: cancel,
    }),
    [track, cancel],
  );
}

/** Warms a visitor's detail page (profile and timeline for `range`). */
export function prefetchVisitor(token: string, id: string, range: RangeParams): void {
  const visitor = visitorQuery(token, id);
  const timeline = visitorTimelineQuery(token, id, range);
  void prefetch(visitor.key, visitor.fetch);
  void prefetch(timeline.key, timeline.fetch);
}

/** Warms a conversation's preview / detail page. */
export function prefetchConversation(token: string, id: string): void {
  const q = conversationQuery(token, id);
  void prefetch(q.key, q.fetch);
}
