"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ANALYTICS_TABS, TabGroup } from "@/components/admin/TabGroup";
import { INSIGHTS_CACHE_PREFIX } from "@/lib/insights-api";
import { invalidate } from "@/lib/query-cache";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import { AnalyticsTabPanel } from "./AnalyticsTabPanel";
import type { LabelKind } from "./AnalyticsCards";
import { FilterBar, type ChipLabels } from "./FilterBar";
import { KpiStrip } from "./KpiStrip";
import { PendingState } from "./PendingState";
import { errorText, useInsightsAggregate, useInsightsFilters, useInsightsPending } from "./use-insights";
import { ANALYTICS_TAB_IDS, useAnalyticsUrlState, type AnalyticsTabId } from "./url-state";

/** How often the classification backlog is re-checked while turns are pending. */
export const PENDING_POLL_MS = 30_000;

function isTab(id: string): id is AnalyticsTabId {
  return (ANALYTICS_TAB_IDS as readonly string[]).includes(id);
}

/**
 * Analytics page body: filter bar and KPI strip over the tabbed charts
 * (native port of the Chatbot Insights HTML, newplan §6.4). While the
 * classifier backlog drains, a banner shows progress and the charts keep
 * rendering the labelled turns; they refresh as more turns are labelled.
 */
export function AnalyticsView() {
  const { state, update, clearFilters } = useAnalyticsUrlState();
  const filters = useInsightsFilters(state);
  const summary = useInsightsAggregate("summary", filters);
  const flow = useInsightsAggregate("flow", filters);
  const pending = useInsightsPending();
  const [labels, setLabels] = useState<ChipLabels>({});
  const learnLabel = useCallback((kind: LabelKind, label: string) => setLabels((l) => ({ ...l, [kind]: label })), []);

  const backlog = pending.data?.pending ?? 0;
  const labelled = pending.data?.labelled;
  useVisibilityPolling(() => void pending.refetch(), PENDING_POLL_MS, backlog > 0);

  // Newly labelled turns change every aggregate: refresh the charts.
  const seenLabelled = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (labelled === undefined) return;
    if (seenLabelled.current !== undefined && labelled > seenLabelled.current) {
      invalidate(INSIGHTS_CACHE_PREFIX);
    }
    seenLabelled.current = labelled;
  }, [labelled]);

  const nothingLabelled = summary.data !== undefined && summary.data.conversations === 0 && backlog > 0;
  const onGuide = state.tab === "guide";

  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-4 py-5 sm:px-6">
      <TabGroup
        tabs={ANALYTICS_TABS}
        activeTab={state.tab}
        onChange={(id) => {
          if (isTab(id)) update({ tab: id });
        }}
        ariaLabel="Analytics sections"
        className="self-start"
      />
      {!onGuide && (
        <>
          <FilterBar
            state={state}
            update={update}
            clearFilters={clearFilters}
            userTypes={flow.data?.user_types.map((t) => t.name)}
            chipLabels={labels}
          />
          {backlog > 0 && pending.data && <PendingState pending={pending.data} nothingLabelled={nothingLabelled} />}
          {summary.error ? (
            <p role="alert" className="rounded-xl border border-status-danger/40 bg-status-danger-bg p-3 text-xs text-status-danger-fg">
              {errorText(summary.error)}
            </p>
          ) : (
            <KpiStrip summary={summary.data} loading={summary.isLoading} />
          )}
        </>
      )}
      <div role="tabpanel" aria-label={ANALYTICS_TABS.find((t) => t.id === state.tab)?.label}>
        <AnalyticsTabPanel tab={state.tab} state={state} filters={filters} update={update} learnLabel={learnLabel} />
      </div>
    </div>
  );
}
