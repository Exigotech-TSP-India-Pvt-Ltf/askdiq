"use client";

import React from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";
import { DateRangePicker } from "@/components/ui/DateRangePicker";

export interface BreadcrumbItem {
  label: string;
  /** Omit for the current page (last item). */
  href?: string;
}

export interface DashboardHeaderProps {
  title: string;
  subtitle?: string;
  /** Detail pages: trail back to the list (replaces the subtitle line). */
  breadcrumb?: readonly BreadcrumbItem[];
  icon: React.ComponentType<{ className?: string }>;
  badgeCount?: number | string | null;
  centerSlot?: React.ReactNode;
  showAudienceTabs?: boolean;
  /**
   * Shows the global DateRangePicker (icon button) at the start of the
   * right-corner action cluster. Opt-in until a
   * page reads `useRange()`, so the control never appears without effect.
   */
  showDateRange?: boolean;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export function DashboardHeader({
  title,
  subtitle,
  breadcrumb,
  icon: Icon,
  badgeCount,
  centerSlot,
  showAudienceTabs = true,
  showDateRange = false,
  actions,
  children,
  className = "",
}: DashboardHeaderProps) {
  return (
    <header
      className={`grid h-20 shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-line bg-surface-card px-4 sm:px-6 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] dark:border-brand-500/15 ${className}`}
    >
      {/* Left column: icon badge + title + subtitle. Equal-width side columns
          keep the centre column (audience switcher) at the same position on
          every page, whatever the title or the action cluster holds. */}
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border shadow-sm border-brand-200/80 bg-brand-50 text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/15 dark:text-brand-300">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 title={title} className="truncate text-sm font-bold text-fg tracking-tight">
              {title}
            </h1>
            {badgeCount !== undefined && badgeCount !== null ? (
              <span className="inline-flex items-center rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">
                {badgeCount}
              </span>
            ) : null}
          </div>
          {breadcrumb && breadcrumb.length > 0 ? (
            <nav aria-label="Breadcrumb">
              <ol className="flex min-w-0 items-center gap-1 text-[11px] leading-tight text-fg-subtle">
                {breadcrumb.map((item, i) => (
                  <li key={`${item.label}-${i}`} className="flex min-w-0 items-center gap-1">
                    {i > 0 ? <ChevronRight className="h-3 w-3 shrink-0" strokeWidth={1.75} aria-hidden="true" /> : null}
                    {item.href ? (
                      <Link
                        href={item.href}
                        className="truncate rounded hover:text-accent-fg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                      >
                        {item.label}
                      </Link>
                    ) : (
                      <span aria-current="page" className="truncate text-fg-muted">
                        {item.label}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </nav>
          ) : subtitle && (
            <p title={subtitle} className="truncate text-[11px] text-fg-subtle font-normal leading-tight">
              {subtitle}
            </p>
          )}
        </div>
      </div>

      {/* Centre column: AudienceScopeTabs (or a custom slot), always centred.
          Rendered empty when unused so the side columns stay equal. */}
      <div className="hidden items-center justify-center md:flex">
        {centerSlot !== undefined ? (
          centerSlot
        ) : showAudienceTabs ? (
          <div data-tour="shell-audience">
            <AudienceScopeTabs />
          </div>
        ) : null}
      </div>

      {/* Right corner: compact cluster of 36px icon actions (date range first,
          then page actions). Notifications, tours, theme and sign out live in
          the sidebar account corner. */}
      <div data-header-actions className="flex min-w-0 items-center justify-end gap-2">
        {showDateRange ? (
          <div data-tour="shell-date-range" className="flex">
            <DateRangePicker />
          </div>
        ) : null}
        {actions ?? children}
      </div>
    </header>
  );
}
