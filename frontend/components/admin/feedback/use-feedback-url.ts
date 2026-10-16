"use client";

import { useCallback, useMemo } from "react";
import { useDeepLink } from "@/lib/use-deep-link";
import {
  FILTER_URL_KEYS,
  activeChips,
  parseFilters,
  patchToUrl,
  type FeedbackFilters,
  type FilterChip,
  type FilterPatch,
} from "@/lib/feedback-filters";

export const FEEDBACK_TABS = ["overview", "queue", "golden", "pairs", "gaps"] as const;
export type FeedbackTab = (typeof FEEDBACK_TABS)[number];

function isTab(value: string | null): value is FeedbackTab {
  return value !== null && (FEEDBACK_TABS as readonly string[]).includes(value);
}

export interface FeedbackUrlState {
  tab: FeedbackTab;
  filters: FeedbackFilters;
  chips: FilterChip[];
  setTab: (tab: FeedbackTab) => void;
  /** Applies a filter patch; `openQueue` also switches to the Review Queue (chart clicks). */
  applyFilters: (patch: FilterPatch, options?: { openQueue?: boolean }) => void;
  removeChip: (chip: FilterChip) => void;
  clearAll: () => void;
}

/**
 * Tab and queue filters for the Feedback page, kept in the URL (newplan §5.4:
 * "active filters show as removable chips and sync to the URL").
 */
export function useFeedbackUrl(): FeedbackUrlState {
  const { get, set, search } = useDeepLink();
  const rawTab = get("tab");
  // A deep link to a message (EntityLink "feedback") lands on the queue.
  const tab: FeedbackTab = isTab(rawTab) ? rawTab : get("message_id") ? "queue" : "overview";
  const filters = useMemo(() => parseFilters(new URLSearchParams(search)), [search]);
  const chips = useMemo(() => activeChips(filters), [filters]);

  // Switching tab drops a message deep link; otherwise `message_id` would keep
  // resolving the page back to the queue and Overview could not be reached.
  const setTab = useCallback(
    (next: FeedbackTab) => set({ tab: next === "overview" ? null : next, message_id: null }),
    [set],
  );

  const applyFilters = useCallback(
    (patch: FilterPatch, { openQueue = false }: { openQueue?: boolean } = {}) => {
      set({ ...patchToUrl(patch), ...(openQueue ? { tab: "queue" } : {}) }, { history: openQueue ? "push" : "replace" });
    },
    [set],
  );

  const removeChip = useCallback(
    (chip: FilterChip) => set(patchToUrl({ [chip.key]: chip.resetTo ?? null })),
    [set],
  );

  const clearAll = useCallback(() => {
    const update: Record<string, string | null> = {};
    for (const key of FILTER_URL_KEYS) update[key] = null;
    update.status = "all";
    set(update);
  }, [set]);

  return { tab, filters, chips, setTab, applyFilters, removeChip, clearAll };
}
