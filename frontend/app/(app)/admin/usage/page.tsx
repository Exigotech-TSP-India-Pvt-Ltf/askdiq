"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useUsageSummary } from "@/lib/use-usage-data";
import { InteractiveOverviewTab } from "@/components/admin/usage/InteractiveOverviewTab";
import { UsersTab } from "@/components/admin/usage/UsersTab";
import { SessionsTab } from "@/components/admin/usage/SessionsTab";
import { QueriesTab } from "@/components/admin/usage/QueriesTab";
import { TestIpManager } from "@/components/admin/usage/TestIpManager";
import { CostInsights } from "@/components/admin/usage/CostInsights";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import {
  DEFAULT_USAGE_TAB,
  parseUsageDay,
  parseUsageTab,
  type UsageTabId,
} from "@/lib/usage-tabs";
import {
  CircleDollarSign,
  Settings as SettingsIcon,
  Sparkles,
  Users,
  MessageSquare,
  Cpu,
} from "lucide-react";

type TabId = UsageTabId;

// Live Telemetry Studio is the Overview tab (newplan §3); the old Overview tab is gone.
const USAGE_CONSOLE_TABS: TabItem<TabId>[] = [
  { id: "overview", label: "Overview", icon: Sparkles, badgeCount: "Live" },
  { id: "users", label: "All Users", icon: Users },
  { id: "sessions", label: "All Sessions", icon: MessageSquare },
  { id: "queries", label: "All Queries", icon: Cpu },
];

function UsageCostContent() {
  const { token } = useAuth();
  const { audience } = useAudience();

  const [activeTab, setActiveTab] = useState<TabId>(DEFAULT_USAGE_TAB);
  const [selectedIp, setSelectedIp] = useState<string>("");
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [fromDay, setFromDay] = useState<string>("");
  const [toDay, setToDay] = useState<string>("");
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  // Re-read the URL on every client navigation, not only on mount, so a link
  // to /admin/usage?tab=... while already here switches tab and filters.
  const searchKey = useSearchParams()?.toString() ?? "";

  // Hook for Tab 1 summary
  const { summary, isLoading, error, refresh } = useUsageSummary(
    token,
    audience
  );

  // The URL is the source of truth: tab, ip, session and the from/to day
  // range (dashboard day links send `from` = `to` = YYYY-MM-DD).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    // Old bookmarks (?tab=live_telemetry, ?tab=overall) resolve to "overview".
    setActiveTab(parseUsageTab(sp.get("tab")));
    setSelectedIp(sp.get("ip") ?? "");
    setSelectedSession(sp.get("session") ?? "");
    setFromDay(parseUsageDay(sp.get("from")));
    setToDay(parseUsageDay(sp.get("to")));
  }, [searchKey]);

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

  const handleClearDays = () => {
    setFromDay("");
    setToDay("");
    updateUrl({ from: "", to: "" });
  };

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Usage & Financial Economics"
        subtitle="Audience-segmented cost accounting, token volume telemetry, and turn-level LLM economics"
        icon={CircleDollarSign}
        actions={
          <button
            type="button"
            data-tour="usage-test-ips"
            onClick={() => setIsConfigOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-dark-border bg-white dark:bg-dark-surface px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200 shadow-xs hover:bg-slate-50 dark:hover:bg-white/5 hover:text-slate-900 dark:hover:text-white transition"
          >
            <SettingsIcon className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
            <span>Configure Test IPs</span>
          </button>
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
            <div role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" className="space-y-6">
              <CostInsights summary={summary} isLoading={isLoading} error={error} onRetry={refresh} />
              <InteractiveOverviewTab
                summary={summary}
                isLoading={isLoading}
                error={error}
                onRetry={refresh}
                token={token || ""}
                audience={audience}
              />
            </div>
          )}

          {activeTab === "users" && (
            <div role="tabpanel" id="panel-users" aria-labelledby="tab-users">
              <UsersTab
                token={token || ""}
                onSelectUserIp={handleSelectUserIp}
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
