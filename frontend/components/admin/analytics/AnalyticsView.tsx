"use client";

import { useCallback, useState } from "react";
import { ANALYTICS_TABS, TabGroup } from "@/components/admin/TabGroup";
import { useAuth } from "@/lib/auth-context";
import { useLiveConnected } from "@/lib/live-updates";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import { AnalyticsTabPanel } from "./AnalyticsTabPanel";
import type { LabelKind } from "./AnalyticsCards";
import { FilterBar, type ChipLabels } from "./FilterBar";
import { KPIS, KpiStrip } from "./KpiStrip";
import { PendingState } from "./PendingState";
import { prefetchAnalyticsPatch, useAnalyticsPreload } from "./use-analytics-preload";
import { errorText, pageOf, useInsightsBundle, useInsightsFilters } from "./use-insights";
import {
  ANALYTICS_TAB_IDS,
  DEFAULT_STATE,
  FILTER_FIELDS,
  useAnalyticsUrlState,
  type AnalyticsState,
  type AnalyticsTabId,
} from "./url-state";
import { PersonPanel, VisitorBanner } from "./PersonPanel";
import { SectionBoundary } from "./SectionBoundary";

/** How often the classification backlog is re-checked while turns are pending. */
export const PENDING_POLL_MS = 30_000;

/** What each KPI's "View records" opens (warmed while idle). */
const KPI_TARGETS = KPIS.map((k) => k.target);

function isTab(id: string): id is AnalyticsTabId {
  return (ANALYTICS_TAB_IDS as readonly string[]).includes(id);
}

/**
 * Analytics page body: filter bar and KPI strip over the tabbed charts
 * (native port of the Chatbot Insights HTML). While the
 * classifier backlog drains, a banner shows progress and the charts keep
 * rendering the labelled turns; they refresh as more turns are labelled.
 */
export function AnalyticsView() {
  const { state, update, clearFilters } = useAnalyticsUrlState();
  const filters = useInsightsFilters(state);
  // One request answers every card, the KPIs, the backlog and the table page.
  const bundle = useInsightsBundle(filters, pageOf(state));
  const summary = { ...bundle, data: bundle.data?.summary ?? undefined };
  const flowData = bundle.data?.flow ?? undefined;
  const pendingData = bundle.data?.pending ?? undefined;
  const [labels, setLabels] = useState<ChipLabels>({});
  const learnLabel = useCallback((kind: LabelKind, label: string) => setLabels((l) => ({ ...l, [kind]: label })), []);
  useAnalyticsPreload(state, filters, KPI_TARGETS);
  const { token } = useAuth();
  const warmTarget = useCallback(
    (target: Partial<AnalyticsState>) => prefetchAnalyticsPatch(token, state, filters, target),
    [token, state, filters],
  );

  const backlog = pendingData?.pending ?? 0;
  // The live stream pushes newly labelled turns; poll only without it.
  const live = useLiveConnected();
  useVisibilityPolling(() => void bundle.refetch(), PENDING_POLL_MS, backlog > 0 && !live);

  const nothingLabelled = summary.data !== undefined && summary.data.conversations === 0 && backlog > 0;
  const filtered = FILTER_FIELDS.some((k) => state[k] !== DEFAULT_STATE[k]);
  const noMatches = filtered && summary.data !== undefined && summary.data.conversations === 0 && !nothingLabelled;

  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-4 py-5 sm:px-6">
      <div data-tour="analytics-tabs" className="self-start">
      <TabGroup
        tabs={ANALYTICS_TABS}
        activeTab={state.tab}
        onChange={(id) => {
          if (isTab(id)) update({ tab: id });
        }}
        ariaLabel="Analytics sections"
        className="self-start"
      />
      </div>
      <div data-tour="analytics-filters">
      <FilterBar
        state={state}
        update={update}
        clearFilters={clearFilters}
        userTypes={flowData?.user_types.map((t) => t.name)}
        chipLabels={labels}
      />
      </div>
      {backlog > 0 && pendingData && <PendingState pending={pendingData} nothingLabelled={nothingLabelled} />}
      <SectionBoundary label="The selected person" resetKey={`${state.person}|${state.visitor}`}>
        <PersonPanel state={state} filters={filters} update={update} learnLabel={learnLabel} />
        <VisitorBanner state={state} update={update} />
      </SectionBoundary>
      {noMatches && (
        <p role="status" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line bg-surface-card p-3 text-xs text-fg-muted">
          No conversations match these filters.
          <button type="button" onClick={clearFilters} className="rounded-lg border border-line px-2 py-1 font-semibold text-brand-700 hover:border-brand-300 dark:text-brand-300">
            Clear filters
          </button>
        </p>
      )}
      {summary.error ? (
        <p role="alert" className="rounded-xl border border-status-danger/40 bg-status-danger-bg p-3 text-xs text-status-danger-fg">
          {errorText(summary.error)}
        </p>
      ) : (
        <SectionBoundary label="The key figures" resetKey={summary.data}>
          <KpiStrip summary={summary.data} loading={summary.isLoading} onOpen={update} onIntent={warmTarget} />
        </SectionBoundary>
      )}
      <div
        role="tabpanel"
        data-tour="analytics-panel"
        aria-label={ANALYTICS_TABS.find((t) => t.id === state.tab)?.label}
        aria-busy={summary.isPlaceholderData || undefined}
        // Previous results stay visible (dimmed) while the new filters load.
        className={`transition-opacity duration-150 ${summary.isPlaceholderData ? "opacity-60" : ""}`}
      >
        <AnalyticsTabPanel tab={state.tab} state={state} filters={filters} update={update} learnLabel={learnLabel} />
      </div>
    </div>
  );
}
