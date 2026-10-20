"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { CONVERSATION_OUTCOMES, EXPERIENCE_RATINGS, MAX_SEARCH_CHARS } from "@/lib/insights-api";
import { hourRangeLabel } from "./format";
import { stripControlChars, type AnalyticsState } from "./url-state";

/** User types the classifier assigns (taxonomy groups) plus the derived ones. */
export const BASE_USER_TYPES = ["Customer / prospect", "Off-scope request", "Mixed", "Greeting only"] as const;

const SEARCH_DEBOUNCE_MS = 300;

const FIELD = "flex flex-col gap-0.5";
const LABEL = "text-[10.5px] font-semibold uppercase tracking-wide text-fg-muted";
const CONTROL =
  "min-w-[118px] rounded-lg border border-line-strong bg-surface-card px-2 py-1.5 text-xs text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500";

export interface ChipLabels {
  issue?: string;
  intent?: string;
  quality?: string;
  person?: string;
}

export interface FilterBarProps {
  state: AnalyticsState;
  update: (patch: Partial<AnalyticsState>) => void;
  clearFilters: () => void;
  /** Extra user-type options seen in the data (flow nodes). */
  userTypes?: readonly string[];
  /** Readable names for code / pseudonym filters (chips). */
  chipLabels?: ChipLabels;
}

/**
 * User type, outcome, rating and search, all synced to the URL.
 * The date range comes from the global picker in the page header.
 */
export function FilterBar({ state, update, clearFilters, userTypes = [], chipLabels = {} }: FilterBarProps) {
  const id = useId();
  const [search, setSearch] = useState(state.q);
  const pushed = useRef(state.q);

  // Keep the box in sync when the URL changes elsewhere (chip removed, Clear,
  // back / forward), but not when it echoes what this box just wrote.
  useEffect(() => {
    if (state.q !== pushed.current) {
      pushed.current = state.q;
      setSearch(state.q);
    }
  }, [state.q]);

  useEffect(() => {
    const next = stripControlChars(search).trim().slice(0, MAX_SEARCH_CHARS);
    if (next === pushed.current) return undefined;
    const timer = setTimeout(() => {
      pushed.current = next;
      update({ q: next });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search, update]);

  const typeOptions = Array.from(new Set([...BASE_USER_TYPES, ...userTypes, ...(state.user_type ? [state.user_type] : [])]));

  return (
    <div className="flex flex-col gap-2">
      <div
        role="group"
        aria-label="Filters"
        data-tour="filters"
        className="flex flex-col gap-2 rounded-xl border border-line bg-surface-muted/60 p-3 sm:flex-row sm:flex-wrap sm:items-end"
      >
        <div className={FIELD}>
          <label htmlFor={`${id}-type`} className={LABEL}>
            User type
          </label>
          <select id={`${id}-type`} className={CONTROL} value={state.user_type} onChange={(e) => update({ user_type: e.target.value })}>
            <option value="">All</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className={FIELD}>
          <label htmlFor={`${id}-outcome`} className={LABEL}>
            Outcome
          </label>
          <select
            id={`${id}-outcome`}
            className={CONTROL}
            value={state.outcome}
            onChange={(e) => update({ outcome: e.target.value as AnalyticsState["outcome"] })}
          >
            <option value="">All</option>
            {CONVERSATION_OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div className={FIELD}>
          <label htmlFor={`${id}-rating`} className={LABEL}>
            Rating
          </label>
          <select
            id={`${id}-rating`}
            className={CONTROL}
            value={state.rating}
            onChange={(e) => update({ rating: e.target.value as AnalyticsState["rating"] })}
          >
            <option value="">All</option>
            {EXPERIENCE_RATINGS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div className={`${FIELD} sm:min-w-[200px] sm:flex-1`}>
          <label htmlFor={`${id}-q`} className={LABEL}>
            Search
          </label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
            <input
              id={`${id}-q`}
              type="search"
              className={`${CONTROL} w-full pl-7`}
              placeholder="Question, issue or person"
              maxLength={MAX_SEARCH_CHARS}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        <button
          type="button"
          onClick={clearFilters}
          className="rounded-lg border border-line bg-surface-card px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors duration-150 hover:border-brand-300 hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 sm:ml-auto dark:hover:border-brand-700/60"
        >
          Clear filters
        </button>
      </div>
      <FilterChips state={state} update={update} labels={chipLabels} />
    </div>
  );
}

interface Chip {
  key: string;
  text: string;
  clear: Partial<AnalyticsState>;
}

/** Active filters as removable chips (the HTML's `chips()`). */
export function FilterChips({
  state,
  update,
  labels,
}: {
  state: AnalyticsState;
  update: (patch: Partial<AnalyticsState>) => void;
  labels: ChipLabels;
}) {
  // The date range is shown (and changed) by the picker in the page header.
  const chips: Chip[] = [];
  if (state.user_type) chips.push({ key: "user_type", text: state.user_type, clear: { user_type: "" } });
  if (state.outcome) chips.push({ key: "outcome", text: state.outcome, clear: { outcome: "" } });
  if (state.rating) chips.push({ key: "rating", text: state.rating, clear: { rating: "" } });
  if (state.issue) chips.push({ key: "issue", text: labels.issue ?? state.issue, clear: { issue: "" } });
  if (state.intent) chips.push({ key: "intent", text: labels.intent ?? state.intent, clear: { intent: "" } });
  if (state.quality) chips.push({ key: "quality", text: labels.quality ?? state.quality, clear: { quality: "" } });
  if (state.person) chips.push({ key: "person", text: labels.person ?? "Selected person", clear: { person: "" } });
  if (state.visitor) chips.push({ key: "visitor", text: `Visitor ${state.visitor.replace(/^client-/, "")}`, clear: { visitor: "" } });
  if (state.hour !== null) chips.push({ key: "hour", text: hourRangeLabel(state.hour), clear: { hour: null } });
  if (state.q) chips.push({ key: "q", text: `“${state.q}”`, clear: { q: "" } });

  if (chips.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Active filters">
      {chips.map((c) => (
        <li key={c.key}>
          <button
            type="button"
            aria-label={`Remove filter ${c.text}`}
            onClick={() => update(c.clear)}
            className="inline-flex items-center gap-1 rounded-full border border-brand-500 bg-brand-50 py-0.5 pl-2 pr-2.5 text-[11.5px] font-medium text-brand-800 transition-colors duration-150 hover:bg-brand-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:border-brand-400/60 dark:bg-brand-950/60 dark:text-brand-200 dark:hover:bg-brand-900/60"
          >
            <X className="h-3 w-3" strokeWidth={2.4} aria-hidden="true" />
            {c.text}
          </button>
        </li>
      ))}
    </ul>
  );
}
