"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useUsageSummary } from "@/lib/use-usage-data";
import { OverviewTab } from "@/components/admin/usage/OverviewTab";
import { InteractiveOverviewTab } from "@/components/admin/usage/InteractiveOverviewTab";
import { UsersTab } from "@/components/admin/usage/UsersTab";
import { SessionsTab } from "@/components/admin/usage/SessionsTab";
import { QueriesTab } from "@/components/admin/usage/QueriesTab";
import { TestIpManager } from "@/components/admin/usage/TestIpManager";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import {
  CircleDollarSign,
  Settings as SettingsIcon,
  Layers,
  Sparkles,
  Users,
  MessageSquare,
  Cpu,
} from "lucide-react";

type TabId = "overview" | "live_telemetry" | "users" | "sessions" | "queries";

const USAGE_CONSOLE_TABS: TabItem<TabId>[] = [
  { id: "overview", label: "Overview", icon: Layers },
  { id: "live_telemetry", label: "Live Telemetry Studio", icon: Sparkles, badgeCount: "Live" },
  { id: "users", label: "All Users", icon: Users },
  { id: "sessions", label: "All Sessions", icon: MessageSquare },
  { id: "queries", label: "All Queries", icon: Cpu },
];

function UsageCostContent() {
  const { token } = useAuth();
  const { audience } = useAudience();

  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [selectedIp, setSelectedIp] = useState<string>("");
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [isConfigOpen, setIsConfigOpen] = useState(false);

  // Hook for Tab 1 summary
  const { summary, isLoading, error, refresh } = useUsageSummary(
    token,
    audience
  );

  // Read URL search params on mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    const tabParam = sp.get("tab") as TabId;
    if (tabParam && USAGE_CONSOLE_TABS.some((t) => t.id === tabParam)) {
      setActiveTab(tabParam);
    } else if (tabParam === ("overall" as any)) {
      setActiveTab("overview");
    }

    if (sp.get("ip")) setSelectedIp(sp.get("ip")!);
    if (sp.get("session")) setSelectedSession(sp.get("session")!);
  }, []);

  // Update URL search params
  const updateUrl = useCallback((tab: TabId, ip?: string, session?: string) => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    sp.set("tab", tab);
    if (ip) sp.set("ip", ip);
    else sp.delete("ip");
    if (session) sp.set("session", session);
    else sp.delete("session");
    const newUrl = `${window.location.pathname}?${sp.toString()}`;
    window.history.replaceState(null, "", newUrl);
  }, []);

  const handleTabChange = (t: string) => {
    const validTab = t as TabId;
    setActiveTab(validTab);
    updateUrl(validTab, selectedIp, selectedSession);
  };

  const handleSelectUserIp = (ip: string) => {
    setSelectedIp(ip);
    setActiveTab("sessions");
    updateUrl("sessions", ip, selectedSession);
  };

  const handleSelectSession = (sessionId: string) => {
    setSelectedSession(sessionId);
    setActiveTab("queries");
    updateUrl("queries", selectedIp, sessionId);
  };

  return (
    <div className="flex flex-col min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Usage & Financial Economics"
        subtitle="Audience-segmented cost accounting, token volume telemetry, and turn-level LLM economics"
        icon={CircleDollarSign}
        actions={
          <button
            type="button"
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
        <div className="flex items-center justify-between">
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
            <div role="tabpanel" id="panel-overview" aria-labelledby="tab-overview">
              <OverviewTab
                summary={summary}
                isLoading={isLoading}
                error={error}
                onRetry={refresh}
              />
            </div>
          )}

          {activeTab === "live_telemetry" && (
            <div role="tabpanel" id="panel-live-telemetry" aria-labelledby="tab-live-telemetry">
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
                onSelectIp={(ip) => setSelectedIp(ip)}
              />
            </div>
          )}

          {activeTab === "queries" && (
            <div role="tabpanel" id="panel-queries" aria-labelledby="tab-queries">
              <QueriesTab
                token={token || ""}
                initialSessionId={selectedSession}
                initialIp={selectedIp}
                onSelectIp={(ip) => setSelectedIp(ip)}
                onSelectSession={(id) => setSelectedSession(id)}
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
