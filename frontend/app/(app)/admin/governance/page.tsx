"use client";

import React, { useCallback } from "react";
import { m } from "motion/react";
import { ShieldCheck } from "lucide-react";
import { crossfade, useMotionVariants } from "@/lib/motion";
import { useDeepLink } from "@/lib/use-deep-link";
import { TAB_PARAMS, isGovernanceTab, type GovernanceTab } from "@/lib/governance-links";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, GOVERNANCE_TABS } from "@/components/admin/TabGroup";
import OverallView from "./overall/OverallView";
import MonitoringView from "./monitoring/MonitoringView";
import TracesView from "./traces/TracesView";
import LogsView from "./logs/LogsView";

/** Tabs whose endpoints accept `from` / `to`, so the date range applies. */
const RANGED_TABS: ReadonlySet<GovernanceTab> = new Set(["Traces", "Logs"]);

const SUBTITLE: Record<GovernanceTab, string> = {
  Overall: "Health at a glance: status, KPIs, incidents",
  Monitoring: "Pipeline latency percentiles, traffic, resources and quality, live",
  Traces: "Request journeys across the RAG pipeline",
  Logs: "Audit, security and pipeline events",
};

export default function GovernancePage() {
  const { get, set } = useDeepLink();
  const raw = get("tab");
  const activeTab: GovernanceTab = isGovernanceTab(raw) ? raw : "Overall";
  const variants = useMotionVariants(crossfade);

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
        <TabGroup tabs={GOVERNANCE_TABS} activeTab={activeTab} onChange={onTabChange} ariaLabel="Governance sub-tabs" />

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
