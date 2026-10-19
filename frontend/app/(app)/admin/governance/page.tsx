"use client";

import React, { useCallback, useEffect } from "react";
import dynamic from "next/dynamic";
import { m } from "motion/react";
import { ShieldCheck } from "lucide-react";
import { crossfade, useMotionVariants } from "@/lib/motion";
import { useDeepLink } from "@/lib/use-deep-link";
import { TAB_PARAMS, isGovernanceTab, type GovernanceTab } from "@/lib/governance-links";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, GOVERNANCE_TABS } from "@/components/admin/TabGroup";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { runWhenIdle } from "@/lib/query-cache";
import OverallView from "./overall/OverallView";

// Only the default tab ships with the page; the others (Monitoring pulls the
// polar Recharts modules) are split out and preloaded once the browser is idle,
// so a tab switch never waits on the network.
const loadMonitoring = () => import("./monitoring/MonitoringView");
const loadTraces = () => import("./traces/TracesView");
const loadLogs = () => import("./logs/LogsView");
const tabLoading = () => <SkeletonCard label="Loading tab" className="min-h-96" />;
const MonitoringView = dynamic(loadMonitoring, { loading: tabLoading });
const TracesView = dynamic(loadTraces, { loading: tabLoading });
const LogsView = dynamic(loadLogs, { loading: tabLoading });

function usePreloadTabs(): void {
  useEffect(
    () =>
      runWhenIdle(() => {
        for (const load of [loadMonitoring, loadTraces, loadLogs]) load().catch(() => undefined);
      }),
    [],
  );
}

/**
 * Tabs whose endpoints accept `from` / `to`, so the date range applies.
 * Overall and Monitoring are real-time health views (rolling last 24 hours
 * and live process stats); they stay "as of now" and say so in the subtitle.
 */
const RANGED_TABS: ReadonlySet<GovernanceTab> = new Set(["Traces", "Logs"]);

const SUBTITLE: Record<GovernanceTab, string> = {
  Overall: "Health at a glance: status, KPIs, incidents. Live, last 24 hours (not affected by the date range)",
  Monitoring: "Pipeline latency percentiles, traffic, resources and quality. Live, last 24 hours (not affected by the date range)",
  Traces: "Request journeys across the RAG pipeline",
  Logs: "Audit, security and pipeline events",
};

function GovernanceContent() {
  const { get, set } = useDeepLink();
  const raw = get("tab");
  const activeTab: GovernanceTab = isGovernanceTab(raw) ? raw : "Overall";
  const variants = useMotionVariants(crossfade);
  usePreloadTabs();

  const onTabChange = useCallback(
    (tab: string) => {
      if (!isGovernanceTab(tab) || tab === activeTab) return;
      const cleared = Object.fromEntries(TAB_PARAMS.map((p) => [p, null]));
      set({ ...cleared, tab: tab === "Overall" ? null : tab }, { history: "push" });
    },
    [activeTab, set],
  );

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Admin Governance"
        subtitle={SUBTITLE[activeTab]}
        icon={ShieldCheck}
        showDateRange={RANGED_TABS.has(activeTab)}
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div data-tour="governance-tabs">
          <TabGroup tabs={GOVERNANCE_TABS} activeTab={activeTab} onChange={onTabChange} ariaLabel="Governance sub-tabs" />
        </div>

        <m.div key={activeTab} variants={variants} initial="hidden" animate="visible">
          {activeTab === "Overall" && <OverallView />}
          {activeTab === "Monitoring" && <MonitoringView />}
          {activeTab === "Traces" && <TracesView />}
          {activeTab === "Logs" && <LogsView />}
        </m.div>
      </div>
    </div>
  );
}

export default function GovernancePage() {
  return (
    <React.Suspense fallback={<div className="min-h-screen bg-surface-page" aria-busy="true" />}>
      <GovernanceContent />
    </React.Suspense>
  );
}
