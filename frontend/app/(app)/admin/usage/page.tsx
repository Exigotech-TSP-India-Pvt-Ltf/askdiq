"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useUsageSummary, warmUsageOverview } from "@/lib/use-usage-data";
import { DEFAULT_PRESET, useRange } from "@/lib/range-context";
import { useRangeParams } from "@/components/admin/entity/shared";
import { InteractiveOverviewTab } from "@/components/admin/usage/InteractiveOverviewTab";
import { UsersTab } from "@/components/admin/usage/UsersTab";
import { SessionsTab } from "@/components/admin/usage/SessionsTab";
import { QueriesTab } from "@/components/admin/usage/QueriesTab";
import { TestIpManager } from "@/components/admin/usage/TestIpManager";
import { CostInsights } from "@/components/admin/usage/CostInsights";
import { AzureTokensPanel } from "@/components/admin/azure/AzureTokensPanel";
import { BilledVsEstimated } from "@/components/admin/usage/BilledVsEstimated";
import { warmAzureCosts } from "@/lib/azure-costs-api";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import {
  DEFAULT_USAGE_TAB,
  parseUsageDay,
  parseUsageTab,
  type UsageTabId,
} from "@/lib/usage-tabs";
import { HeaderIconButton } from "@/components/ui/HeaderIconButton";
import {
  CircleDollarSign,
  Network,
  Sparkles,
  Users,
  MessageSquare,
  Cpu,
} from "lucide-react";

type TabId = UsageTabId;

// Live Telemetry Studio is the Overview tab; the old Overview tab is gone.
const USAGE_CONSOLE_TABS: TabItem<TabId>[] = [
  { id: "overview", label: "Overview", icon: Sparkles, badgeCount: "Live" },
  { id: "users", label: "All Users", icon: Users },
  { id: "sessions", label: "All Sessions", icon: MessageSquare },
  { id: "queries", label: "All Queries", icon: Cpu },
];

/** A `YYYY-MM-DD` as a local calendar date (what the range picker holds). */
function localDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function UsageCostContent() {
  const { token } = useAuth();
  const { audience } = useAudience();

  const [activeTab, setActiveTab] = useState<TabId>(DEFAULT_USAGE_TAB);
  const [selectedIp, setSelectedIp] = useState<string>("");
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  // Re-read the URL on every client navigation, not only on mount, so a link
  // to /admin/usage?tab=... while already here switches tab and filters.
  const searchKey = useSearchParams()?.toString() ?? "";

  // One source of truth for the dates: the global range (header picker,
  // `?range=` / `?from=&to=`), sent to every tab as inclusive UTC days.
  // Dashboard day links (`from` = `to` = YYYY-MM-DD) are a custom range.
  const rangeCtx = useRange();
  const { from: fromDay = "", to: toDay = "" } = useRangeParams();
  const days = { from: fromDay || undefined, to: toDay || undefined };

  // Overview summary: cached per audience + range; a switch keeps the
  // previous one on screen until the new one arrives (never skeletons).
  const { summary, isLoading, isUpdating, error, refresh } = useUsageSummary(token, audience, days);

  // Once the Overview shows data, warm what one click can show next (other
  // presets, other audiences) while the browser is idle.
  const overviewReady = activeTab === "overview" && summary !== null;
  useEffect(() => {
    if (!overviewReady) return undefined;
    const stopUsage = warmUsageOverview(token, audience, { from: fromDay || undefined, to: toDay || undefined });
    const stopAzure = warmAzureCosts(token);
    return () => {
      stopUsage();
      stopAzure();
    };
  }, [overviewReady, token, audience, fromDay, toDay]);

  // The URL is the source of truth: tab, ip and session.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    // Old bookmarks (?tab=live_telemetry, ?tab=overall) resolve to "overview".
    setActiveTab(parseUsageTab(sp.get("tab")));
    setSelectedIp(sp.get("ip") ?? "");
    setSelectedSession(sp.get("session") ?? "");
  }, [searchKey]);

  // Old Usage links could send only one end (`?from=` or `?to=`), which the
  // global range ignores: turn them into the custom range they meant.
  const setCustom = rangeCtx?.setCustom;
  useEffect(() => {
    if (typeof window === "undefined" || !setCustom) return;
    const sp = new URLSearchParams(window.location.search);
    const from = parseUsageDay(sp.get("from"));
    const to = parseUsageDay(sp.get("to"));
    if (from && !to) setCustom(localDay(from), new Date());
    else if (to && !from) setCustom(localDay(to), localDay(to));
  }, [searchKey, setCustom]);

  const updateUrl = useCallback(
    (patch: { tab?: TabId; ip?: string; session?: string; from?: string; to?: string }) => {
      if (typeof window === "undefined") return;
      const sp = new URLSearchParams(window.location.search);
      for (const [key, value] of Object.entries(patch)) {
        if (value) sp.set(key, value);
        else sp.delete(key);
      }
      window.history.replaceState(null, "", `${window.location.pathname}?${sp.toString()}`);
    },
    [],
  );

  const handleTabChange = (t: string) => {
    const validTab = t as TabId;
    setActiveTab(validTab);
    updateUrl({ tab: validTab });
  };

  const handleSelectUserIp = (ip: string) => {
    setSelectedIp(ip);
    setActiveTab("sessions");
    updateUrl({ tab: "sessions", ip });
  };

  const handleSelectSession = (sessionId: string) => {
    setSelectedSession(sessionId);
    setActiveTab("queries");
    updateUrl({ tab: "queries", session: sessionId });
  };

  /** Sets or clears ("") the IP filter without leaving the current tab. */
  const handleIpFilter = (ip: string) => {
    setSelectedIp(ip);
    updateUrl({ ip });
  };

  const handleSessionFilter = (sessionId: string) => {
    setSelectedSession(sessionId);
    updateUrl({ session: sessionId });
  };

  // Clearing the deep-linked day chip returns to the default range.
  const handleClearDays =
    rangeCtx && rangeCtx.range.kind === "custom" ? () => rangeCtx.setPreset(DEFAULT_PRESET) : undefined;

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Usage & Financial Economics"
        subtitle="Audience-segmented cost accounting, token volume telemetry, and turn-level LLM economics"
        icon={CircleDollarSign}
        showDateRange
        actions={
          <HeaderIconButton
            icon={Network}
            label="Configure Test IPs"
            data-tour="usage-test-ips"
            aria-haspopup="dialog"
            onClick={() => setIsConfigOpen(true)}
          />
        }
      />

      {/* Main Responsive Body with Strict Width Bounds */}
      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Boxed Tab Navigation Strip */}
        <div className="flex items-center justify-between" data-tour="usage-tabs">
          <TabGroup
            tabs={USAGE_CONSOLE_TABS}
            activeTab={activeTab}
            onChange={handleTabChange}
            ariaLabel="Usage console navigation"
          />
        </div>

        {/* Tab Panels */}
        <div className="pt-1">
          {activeTab === "overview" && (
            <div
              role="tabpanel"
              id="panel-overview"
              aria-labelledby="tab-overview"
              data-usage-key={isUpdating || !summary ? "pending" : `${audience}|${fromDay}|${toDay}`}
              className={`space-y-6 transition-opacity duration-150 ${isUpdating ? "opacity-70" : ""}`}
            >
              <CostInsights summary={summary} isLoading={isLoading} error={error} onRetry={refresh} />
              <BilledVsEstimated token={token} days={days} />
              <AzureTokensPanel />
              <InteractiveOverviewTab
                summary={summary}
                isLoading={isLoading}
                error={error}
                onRetry={refresh}
                token={token || ""}
                audience={audience}
                fromDay={fromDay}
                toDay={toDay}
              />
            </div>
          )}

          {activeTab === "users" && (
            <div role="tabpanel" id="panel-users" aria-labelledby="tab-users">
              <UsersTab
                token={token || ""}
                onSelectUserIp={handleSelectUserIp}
                fromDay={fromDay}
                toDay={toDay}
              />
            </div>
          )}

          {activeTab === "sessions" && (
            <div role="tabpanel" id="panel-sessions" aria-labelledby="tab-sessions">
              <SessionsTab
                token={token || ""}
                initialIp={selectedIp}
                onSelectSession={handleSelectSession}
                onSelectIp={handleIpFilter}
                fromDay={fromDay}
                toDay={toDay}
              />
            </div>
          )}

          {activeTab === "queries" && (
            <div role="tabpanel" id="panel-queries" aria-labelledby="tab-queries">
              <QueriesTab
                token={token || ""}
                initialSessionId={selectedSession}
                initialIp={selectedIp}
                fromDay={fromDay}
                toDay={toDay}
                onSelectIp={handleIpFilter}
                onSelectSession={handleSessionFilter}
                onClearDays={handleClearDays}
              />
            </div>
          )}
        </div>
      </div>

      {/* Test IP Configuration Modal */}
      <TestIpManager
        token={token || ""}
        isOpen={isConfigOpen}
        onClose={() => setIsConfigOpen(false)}
        onConfigChanged={refresh}
      />
    </div>
  );
}

export default function UsageCostPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-slate-400">Loading enterprise usage console...</div>}>
      <UsageCostContent />
    </React.Suspense>
  );
}
