"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import { CONVERSATION_OUTCOMES, EXPERIENCE_RATINGS, MAX_SEARCH_CHARS } from "@/lib/insights-api";
import { FilterChipBar } from "@/components/admin/filters/FilterChipBar";
import type { FilterDescriptor } from "@/components/admin/filters/types";
import { focusRing } from "@/components/ui/styles";
import { hourRangeLabel } from "./format";
import { stripControlChars, type AnalyticsState } from "./url-state";

/** User types the classifier assigns (taxonomy groups) plus the derived ones. */
export const BASE_USER_TYPES = ["Customer / prospect", "Off-scope request", "Mixed", "Greeting only"] as const;

const SEARCH_DEBOUNCE_MS = 300;
const VISITOR_PREFIX = "client-";

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

const choices = (values: readonly string[]) => [{ value: "", label: "All" }, ...values.map((v) => ({ value: v, label: v }))];

/** The page's filters as shared chip descriptors (chip text kept as before). */
export function analyticsFilterDescriptors(
  state: AnalyticsState,
  userTypes: readonly string[],
  labels: ChipLabels,
): FilterDescriptor[] {
  const visitor = state.visitor.startsWith(VISITOR_PREFIX) ? state.visitor.slice(VISITOR_PREFIX.length) : state.visitor;
  return [
    { key: "user_type", label: "User type", value: state.user_type, kind: "single", options: choices(userTypes), chipLabel: state.user_type },
    { key: "outcome", label: "Outcome", value: state.outcome, kind: "single", options: choices(CONVERSATION_OUTCOMES), chipLabel: state.outcome },
    { key: "rating", label: "Rating", value: state.rating, kind: "single", options: choices(EXPERIENCE_RATINGS), chipLabel: state.rating },
    { key: "issue", label: "Issue", value: state.issue, kind: "text", chipLabel: labels.issue ?? state.issue },
    { key: "intent", label: "Topic", value: state.intent, kind: "text", chipLabel: labels.intent ?? state.intent },
    { key: "quality", label: "Quality", value: state.quality, kind: "text", chipLabel: labels.quality ?? state.quality },
    { key: "person", label: "Person", value: state.person, kind: "text", chipLabel: labels.person ?? "Selected person" },
    { key: "visitor", label: "Visitor", value: state.visitor, kind: "text", chipLabel: `Visitor ${visitor}` },
    {
      key: "hour",
      label: "Hour",
      value: state.hour === null ? "" : String(state.hour),
      kind: "text",
      chipLabel: state.hour === null ? undefined : hourRangeLabel(state.hour),
    },
    { key: "q", label: "Search", value: state.q, kind: "text", chipLabel: `“${state.q}”` },
  ];
}

/**
 * Search plus the shared filter chips (OR-13 / VA-25): user type, outcome and
 * rating are edited in the Filters popover or on their chip; chart
 * cross-filters (issue, topic, person, hour…) show as removable chips. All
 * synced to the URL; the date range is the filter at the end of the tab row.
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

  const typeOptions = useMemo(
    () => Array.from(new Set([...BASE_USER_TYPES, ...userTypes, ...(state.user_type ? [state.user_type] : [])])),
    [userTypes, state.user_type],
  );
  const descriptors = analyticsFilterDescriptors(state, typeOptions, chipLabels);

  function onChange(key: string, value: string) {
    if (key === "hour") update({ hour: value === "" ? null : Number(value) });
    else update({ [key]: value } as Partial<AnalyticsState>);
  }

  return (
    <FilterChipBar
      data-tour="filters"
      filters={descriptors}
      onChange={onChange}
      onClearAll={clearFilters}
      leading={
        <div className="relative w-full sm:w-56">
          <label htmlFor={`${id}-q`} className="sr-only">
            Search
          </label>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
          <input
            id={`${id}-q`}
            type="search"
            className={`h-9 w-full rounded-lg border border-line bg-surface-card pl-8 pr-2 text-xs text-fg shadow-sm placeholder:text-fg-subtle ${focusRing}`}
            placeholder="Question, issue or person"
            maxLength={MAX_SEARCH_CHARS}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      }
    />
  );
}
