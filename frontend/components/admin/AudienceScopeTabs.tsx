"use client";

import React, { useId, useRef } from "react";
import { ActivePill } from "@/components/ui/ActivePill";
import { segmentTrack } from "@/components/ui/styles";
import { useAudience } from "@/lib/audience-context";
import type { Audience, AudienceCounts } from "@/lib/types";
import { Users, TestTube, Globe } from "lucide-react";

interface AudienceScopeTabsProps {
  token?: string | null;
  className?: string;
  counts?: AudienceCounts | null;
  /** Below `md`, a full-width segmented row (VA-33): the tabs share the width. */
  fill?: boolean;
}

const TABS: Array<{
  id: Audience;
  label: string;
  icon: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  description: string;
}> = [
  {
    id: "overview",
    label: "Overview",
    icon: Globe,
    description: "Complete platform aggregate including all end users and internal tests",
  },
  {
    id: "users",
    label: "Users",
    icon: Users,
    description: "Authentic production usage filtered to exclude configured test IPs",
  },
  {
    id: "testing",
    label: "Testing",
    icon: TestTube,
    description: "Internal benchmarking, evaluation runs, and queries originating from test IPs",
  },
];

export function AudienceScopeTabs({
  className = "",
  counts: externalCounts,
  fill = false,
}: AudienceScopeTabsProps) {
  const { audience, setAudience, counts: contextCounts } = useAudience();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const pillId = `audience-pill-${useId()}`;

  const counts = externalCounts ?? contextCounts;

  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    let nextIndex = index;
    if (e.key === "ArrowRight") {
      nextIndex = (index + 1) % TABS.length;
    } else if (e.key === "ArrowLeft") {
      nextIndex = (index - 1 + TABS.length) % TABS.length;
    } else {
      return;
    }
    e.preventDefault();
    setAudience(TABS[nextIndex].id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Audience Scope"
      className={`${segmentTrack} select-none ${fill ? "flex w-full md:inline-flex md:w-auto" : ""} ${className}`}
    >
      {TABS.map((tab, idx) => {
        const isActive = audience === tab.id;
        const Icon = tab.icon;
        const badgeCount = counts ? counts[tab.id]?.ips : null;

        const tooltip = counts?.[tab.id]
          ? `${tab.label}: ${counts[tab.id].ips} ${tab.id === "testing" ? "Testers" : "Users"} · ${counts[tab.id].queries.toLocaleString()} Queries · ${counts[tab.id].sessions.toLocaleString()} Sessions\n${tab.description}`
          : tab.description;

        return (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[idx] = el;
            }}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => setAudience(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            title={tooltip}
            className={`relative flex items-center gap-2 ${fill ? "min-w-0 flex-1 justify-center px-2 md:flex-none md:px-3.5" : "px-3.5"} py-1.5 text-xs font-semibold rounded-lg transition-colors duration-150 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1 select-none ${
              isActive
                ? "text-white"
                : "text-fg-muted hover:text-fg hover:bg-surface-card/80 dark:hover:bg-white/6"
            }`}
          >
            {isActive && <ActivePill layoutId={pillId} />}
            <Icon
              className={`relative w-4 h-4 shrink-0 ${isActive ? "text-white" : "text-fg-subtle"}`}
              strokeWidth={1.75}
            />
            <span className="relative whitespace-nowrap">{tab.label}</span>
            {badgeCount !== null && badgeCount !== undefined ? (
              <span
                className={`relative text-[10px] min-w-5 px-1.5 py-0.5 rounded-full font-semibold text-center transition-colors ${
                  isActive
                    ? "bg-white/20 text-white"
                    : "bg-surface-card text-fg-muted dark:bg-white/10"
                }`}
              >
                {badgeCount.toLocaleString()}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

