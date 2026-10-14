"use client";

import React from "react";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";

export interface DashboardHeaderProps {
  title: string;
  subtitle?: string;
  icon: React.ComponentType<{ className?: string }>;
  badgeCount?: number | string | null;
  centerSlot?: React.ReactNode;
  showAudienceTabs?: boolean;
  actions?: React.ReactNode;
  className?: string;
}

export function DashboardHeader({
  title,
  subtitle,
  icon: Icon,
  badgeCount,
  centerSlot,
  showAudienceTabs = true,
  actions,
  className = "",
}: DashboardHeaderProps) {
  return (
    <header
      className={`h-16 shrink-0 border-b border-ink/10 bg-white px-6 flex items-center justify-between gap-4 ${className}`}
    >
      {/* Left Column: Icon Badge + Standardized text-sm bold title */}
      <div className="flex items-center gap-3 min-w-0 sm:min-w-[200px]">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-50 border border-brand-500/20 text-brand-600 shadow-sm">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-sm font-bold text-slate-900 tracking-tight">
              {title}
            </h1>
            {badgeCount !== undefined && badgeCount !== null ? (
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">
                {badgeCount}
              </span>
            ) : null}
          </div>
          {subtitle && (
            <p className="truncate text-[11px] text-slate-500 font-normal leading-tight">
              {subtitle}
            </p>
          )}
        </div>
      </div>

      {/* Center Column: Centered AudienceScopeTabs or custom slot */}
      <div className="flex-1 hidden md:flex items-center justify-center">
        {centerSlot !== undefined ? (
          centerSlot
        ) : showAudienceTabs ? (
          <AudienceScopeTabs />
        ) : null}
      </div>

      {/* Right Column: Actions (Date pickers, export buttons, actions) */}
      <div className="flex items-center gap-2.5 min-w-0 sm:min-w-[200px] justify-end">
        {actions}
      </div>
    </header>
  );
}
