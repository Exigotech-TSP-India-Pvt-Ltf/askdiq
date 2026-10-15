"use client";

import React, { useId, useRef } from "react";
import { ActivePill } from "@/components/ui/ActivePill";
import { useAudience } from "@/lib/audience-context";
import type { Audience, AudienceCounts } from "@/lib/types";
import { Users, TestTube, Globe } from "lucide-react";

interface AudienceScopeTabsProps {
  token?: string | null;
  className?: string;
  counts?: AudienceCounts | null;
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
      className={`inline-flex p-1 bg-slate-100/90 dark:bg-dark-card rounded-xl border border-slate-200/90 dark:border-dark-border shadow-inner gap-1 select-none ${className}`}
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
            className={`relative flex items-center gap-2 px-3.5 py-1.5 text-xs font-semibold rounded-lg transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1 select-none ${
              isActive
                ? "text-white"
                : "text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-200/60 dark:hover:bg-white/10"
            }`}
          >
            {isActive && <ActivePill layoutId={pillId} />}
            <Icon
              className={`relative w-4 h-4 flex-shrink-0 ${isActive ? "text-white" : "text-slate-500 dark:text-slate-400"}`}
              strokeWidth={1.75}
            />
            <span className="relative whitespace-nowrap">{tab.label}</span>
            {badgeCount !== null && badgeCount !== undefined ? (
              <span
                className={`relative text-[10px] min-w-[1.25rem] px-1.5 py-0.5 rounded-full font-semibold text-center transition-colors ${
                  isActive
                    ? "bg-white/20 text-white"
                    : "bg-slate-200/80 dark:bg-white/10 text-slate-600 dark:text-slate-300"
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

