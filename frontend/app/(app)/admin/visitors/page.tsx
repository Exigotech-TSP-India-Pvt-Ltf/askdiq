"use client";

import React, { useCallback } from "react";
import { MessagesSquare, Users as UsersIcon } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { PageToolbar } from "@/components/admin/PageToolbar";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { VisitorsPanel } from "@/components/admin/audience/VisitorsPanel";
import { ConversationsPanel } from "@/components/admin/audience/ConversationsPanel";
import { AUDIENCE_PAGE_LABEL, parseAudienceTab, type AudienceTab } from "@/lib/entity-routes";
import { useDeepLink } from "@/lib/use-deep-link";

const TABS: TabItem<AudienceTab>[] = [
  { id: "visitors", label: "Visitors", icon: UsersIcon, description: "Who uses the assistant: visitors by client IP" },
  { id: "conversations", label: "Conversations", icon: MessagesSquare, description: "What they asked: sessions, turns and outcomes" },
];

const SUBTITLE: Record<AudienceTab, string> = {
  visitors: "Visitors by client IP: activity, sessions and engagement",
  conversations: "Conversation logs: turn counts, outcomes and transcripts",
};

/** Params owned by one tab; cleared when switching so the other tab starts unfiltered. */
const TAB_PARAMS: Record<AudienceTab, readonly string[]> = {
  visitors: ["weekday", "hour", "users_page", "users_q", "users_sort", "users_limit"],
  conversations: ["ip", "min_turns", "max_turns", "outcome", "day", "preview", "convs_page", "convs_q", "convs_sort", "convs_limit"],
};

function AudienceContent() {
  const { get, set } = useDeepLink();
  const tab = parseAudienceTab(get("tab"));

  const changeTab = useCallback(
    (next: AudienceTab) => {
      if (next === tab) return;
      const clear: Record<string, null> = {};
      for (const key of TAB_PARAMS[tab]) clear[key] = null;
      set({ ...clear, tab: next === "visitors" ? null : next }, { history: "push" });
    },
    [set, tab],
  );

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <div data-tour={tab === "visitors" ? "visitors-header" : "conversations-header"}>
        <DashboardHeader
          title={AUDIENCE_PAGE_LABEL}
          subtitle={SUBTITLE[tab]}
          icon={tab === "visitors" ? UsersIcon : MessagesSquare}
        />
      </div>

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-4 sm:p-8">
        <PageToolbar
          tabsTour="audience-tabs"
          tabs={<TabGroup tabs={TABS} activeTab={tab} onChange={changeTab} ariaLabel="Visitors and conversations" />}
          dateRange
        />

        <div role="tabpanel" aria-label={tab === "visitors" ? "Visitors" : "Conversations"}>
          {tab === "visitors" ? <VisitorsPanel /> : <ConversationsPanel />}
        </div>
      </div>
    </div>
  );
}

export default function VisitorsAndConversationsPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading visitors…</div>}>
      <AudienceContent />
    </React.Suspense>
  );
}
