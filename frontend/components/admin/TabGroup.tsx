"use client";

import React, { useRef } from "react";
import {
  BarChart3,
  Users,
  Clock,
  Search,
  LayoutGrid,
  Scale,
  FileCheck,
  CheckCircle2,
  ShieldAlert,
  ThumbsUp,
  Percent,
  Crosshair,
  Binary,
  Clock4,
  DollarSign,
  GitBranch,
  ShieldCheck,
  Sliders,
  Network,
  FileText,
  Layers,
  MessageSquare,
  Sparkles,
  Globe,
  Zap,
  ListChecks,
  MoreHorizontal,
  Activity,
  Gauge,
  Timer,
  ScrollText,
} from "lucide-react";

export interface TabItem<T extends string = string> {
  id: T;
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
  badgeCount?: number | string | null;
  description?: string;
}

export interface TabGroupProps<T extends string = string> {
  tabs: TabItem<T>[];
  activeTab: T;
  onChange: (id: T) => void;
  className?: string;
  ariaLabel?: string;
}

export function TabGroup<T extends string = string>({
  tabs,
  activeTab,
  onChange,
  className = "",
  ariaLabel = "Tab Navigation",
}: TabGroupProps<T>) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    let nextIndex = index;
    if (e.key === "ArrowRight") {
      nextIndex = (index + 1) % tabs.length;
    } else if (e.key === "ArrowLeft") {
      nextIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (e.key === "Home") {
      nextIndex = 0;
    } else if (e.key === "End") {
      nextIndex = tabs.length - 1;
    } else {
      return;
    }
    e.preventDefault();
    onChange(tabs[nextIndex].id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`inline-flex p-1 bg-slate-100/90 rounded-xl border border-slate-200/90 shadow-inner gap-1 flex-wrap select-none ${className}`}
    >
      {tabs.map((tab, idx) => {
        const isActive = activeTab === tab.id;
        const Icon = tab.icon;

        return (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[idx] = el;
            }}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            title={tab.description || tab.label}
            className={`relative flex items-center gap-2 px-3.5 py-1.5 text-xs rounded-lg transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1 select-none ${
              isActive
                ? "bg-brand-gradient text-white shadow-brand-glow font-semibold"
                : "text-slate-600 hover:text-slate-900 hover:bg-slate-200/60 font-medium"
            }`}
          >
            {Icon && (
              <Icon
                className={`w-3.5 h-3.5 flex-shrink-0 ${
                  isActive ? "text-white" : "text-slate-500"
                }`}
              />
            )}
            <span className="whitespace-nowrap">{tab.label}</span>
            {tab.badgeCount !== undefined && tab.badgeCount !== null ? (
              <span
                className={`text-[10px] min-w-[1.25rem] px-1.5 py-0.5 rounded-full font-semibold text-center transition-colors ${
                  isActive
                    ? "bg-white/20 text-white"
                    : "bg-slate-200/80 text-slate-600"
                }`}
              >
                {typeof tab.badgeCount === "number"
                  ? tab.badgeCount.toLocaleString()
                  : tab.badgeCount}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================================
// 20-Tab Complete Catalog with Dedicated Lucide Icons (Subtask 5.3.2)
// ============================================================================

export const USAGE_TABS: TabItem[] = [
  { id: "overview", label: "Overview", icon: Layers },
  { id: "users", label: "All Users", icon: Users },
  { id: "sessions", label: "All Sessions", icon: MessageSquare },
  { id: "queries", label: "All Queries", icon: Sparkles },
];

export const EVALUATION_TABS: TabItem[] = [
  { id: "Overall", label: "Overall", icon: Globe },
  { id: "Retrieval", label: "Retrieval", icon: Search },
  { id: "Citation", label: "Citation", icon: FileText },
  { id: "Answer quality", label: "Answer quality", icon: Sparkles },
  { id: "Correctness", label: "Correctness", icon: CheckCircle2 },
  { id: "Helpfulness", label: "Helpfulness", icon: ThumbsUp },
  { id: "Abstention", label: "Abstention", icon: ShieldAlert },
  { id: "Safety", label: "Safety", icon: ShieldCheck },
  { id: "Robustness", label: "Robustness", icon: Zap },
  { id: "Results by case type", label: "Results by case type", icon: Layers },
  { id: "All test cases", label: "All test cases", icon: ListChecks },
  { id: "Other", label: "Other", icon: MoreHorizontal },
];

export const GOVERNANCE_TABS: TabItem[] = [
  { id: "Overall", label: "Overall", icon: Activity },
  { id: "Monitoring", label: "Monitoring", icon: Gauge },
  { id: "Traces", label: "Traces", icon: Timer },
  { id: "Logs", label: "Logs", icon: ScrollText },
];

export const DOCUMENTS_TABS: TabItem[] = [
  { id: "overview", label: "Overview", icon: Layers },
  { id: "inventory", label: "Document Inventory", icon: FileText },
];
