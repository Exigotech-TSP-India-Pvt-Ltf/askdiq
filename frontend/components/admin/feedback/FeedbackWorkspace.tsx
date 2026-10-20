"use client";

import React, { useCallback, useEffect, useMemo } from "react";
import { FileJson, FlaskConical, Inbox, LayoutDashboard, SearchX, ThumbsUp } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { prefetch, runWhenIdle, useApiQuery } from "@/lib/query-cache";
import { useCan } from "@/lib/use-can";
import {
  feedbackKeys,
  feedbackListPath,
  getFeedbackAnalytics,
  listFeedback,
  listGoldenCases,
  previewPairs,
  type FeedbackAnalytics,
} from "@/lib/feedback-api";
import { hasClientFilters, type FilterPatch } from "@/lib/feedback-filters";
import { FilterChips } from "./FilterChips";
import { FeedbackOverview } from "./FeedbackOverview";
import { ReviewQueue, queueBaseParams } from "./ReviewQueue";
import { GOLDEN_PAGE_SIZE, GoldenCasesTab } from "./GoldenCasesTab";
import { PREVIEW_LIMIT, PreferenceDataTab } from "./PreferenceDataTab";
import { KnowledgeGapsTab } from "./KnowledgeGapsTab";
import { useFeedbackUrl, type FeedbackTab } from "./use-feedback-url";
import { useRangeParams } from "@/components/admin/entity/shared";

/**
 * Feedback / RLHF workspace (newplan §5.4, planchanges4 F5): Overview,
 * Review Queue, Golden Cases, Preference Data and Knowledge Gaps.
 */
export function FeedbackWorkspace() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const canTriage = useCan("feedback:triage");
  const canExport = useCan("feedback:export");
  const { tab, filters, chips, setTab, applyFilters, removeChip, clearAll } = useFeedbackUrl();
  // The global date range (header picker) bounds every tab: KPIs, queue,
  // golden cases and preference pairs.
  const { from, to } = useRangeParams();
  const days = useMemo(() => ({ from, to }), [from, to]);

  const analyticsFetcher = useCallback(
    (signal: AbortSignal) => getFeedbackAnalytics(token ?? "", audience, signal, days),
    [token, audience, days],
  );
  const analytics = useApiQuery<FeedbackAnalytics>(
    token ? feedbackKeys.analytics(audience, days) : null,
    analyticsFetcher,
    // Range / audience switches swap the numbers in place (no skeletons).
    { keepPreviousData: true },
  );

  // Every tab's first view is warmed while idle, under the exact keys the
  // tabs read, so opening one paints at once.
  useEffect(() => {
    if (!token) return undefined;
    return runWhenIdle(() => {
      const list = { ...queueBaseParams(audience, filters, days), cursor: null };
      prefetch(feedbackKeys.list(feedbackListPath(list)), (signal) => listFeedback(token, list, signal));
      prefetch(feedbackKeys.goldenFor(days), (signal) =>
        listGoldenCases(token, { limit: GOLDEN_PAGE_SIZE, from: days.from, to: days.to }, signal),
      );
      if (canExport) {
        prefetch(feedbackKeys.pairs(false, days), (signal) =>
          previewPairs(token, { complete_only: false, from: days.from, to: days.to }, PREVIEW_LIMIT, signal),
        );
      }
    });
  }, [token, audience, filters, days, canExport]);

  const filterQueue = useCallback((patch: FilterPatch) => applyFilters(patch, { openQueue: true }), [applyFilters]);

  const newCount = analytics.data?.triage_funnel.new;
  const tabs: TabItem<FeedbackTab>[] = [
    { id: "overview", label: "Overview", icon: LayoutDashboard },
    { id: "queue", label: "Review Queue", icon: Inbox, badgeCount: newCount ?? null },
    { id: "golden", label: "Golden Cases", icon: FlaskConical, badgeCount: analytics.data?.triage_funnel.promoted ?? null },
    { id: "pairs", label: "Preference Data", icon: FileJson },
    { id: "gaps", label: "Knowledge Gaps", icon: SearchX, badgeCount: analytics.data?.knowledge_gaps.length ?? null },
  ];

  const showChips = tab === "overview" || tab === "queue";

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Feedback"
        subtitle="Human feedback loop: triage ratings, promote golden cases, export preference pairs"
        icon={ThumbsUp}
        showDateRange
      />
      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-5 p-4 sm:p-6 lg:p-8">
        <div data-tour="feedback-tabs">
          <TabGroup tabs={tabs} activeTab={tab} onChange={setTab} ariaLabel="Feedback sections" />
        </div>
        {showChips ? (
          <FilterChips
            chips={chips}
            onRemove={removeChip}
            onClearAll={clearAll}
            note={hasClientFilters(filters) ? "Reason and sentiment narrow the loaded page." : undefined}
          />
        ) : null}

        <div data-tour="feedback-panel" role="tabpanel" aria-label={tabs.find((t) => t.id === tab)?.label}>
          {tab === "overview" ? (
            <FeedbackOverview
              analytics={analytics.data}
              loading={analytics.isLoading}
              error={analytics.error}
              onRetry={() => void analytics.refetch()}
              onFilter={filterQueue}
            />
          ) : null}
          {tab === "queue" && token ? (
            <ReviewQueue
              token={token}
              audience={audience}
              filters={filters}
              onFilter={applyFilters}
              canTriage={canTriage}
              days={days}
            />
          ) : null}
          {tab === "golden" && token ? <GoldenCasesTab token={token} canRun={canTriage} days={days} /> : null}
          {tab === "pairs" && token ? <PreferenceDataTab token={token} canExport={canExport} days={days} /> : null}
          {tab === "gaps" ? (
            <KnowledgeGapsTab
              analytics={analytics.data}
              loading={analytics.isLoading}
              error={analytics.error}
              onRetry={() => void analytics.refetch()}
              onFilter={filterQueue}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
