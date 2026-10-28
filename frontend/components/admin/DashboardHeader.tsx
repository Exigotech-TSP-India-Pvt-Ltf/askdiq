"use client";

import React from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";

export interface BreadcrumbItem {
  label: string;
  /** Omit for the current page (last item). */
  href?: string;
}

/**
 * Page header: icon, title and subtitle (or breadcrumb), plus the centred
 * audience switcher where the page has one. It holds no actions (OR-2):
 * every page action and the date filter live on the tab row (`PageToolbar`).
 */
export interface DashboardHeaderProps {
  title: string;
  subtitle?: string;
  /** Detail pages: trail back to the list (replaces the subtitle line). */
  breadcrumb?: readonly BreadcrumbItem[];
  icon: React.ComponentType<{ className?: string }>;
  badgeCount?: number | string | null;
  centerSlot?: React.ReactNode;
  showAudienceTabs?: boolean;
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
  className = "",
}: DashboardHeaderProps) {
  const centre =
    centerSlot !== undefined ? (
      centerSlot
    ) : showAudienceTabs ? (
      <div data-tour="shell-audience">
        <AudienceScopeTabs fill />
      </div>
    ) : null;
  const hasCentre = centre !== null && centre !== false;

  return (
    <header
      className={`grid shrink-0 grid-cols-[minmax(0,1fr)] items-center border-b border-line bg-surface-card px-4 sm:px-6 ${
        hasCentre
          ? "min-h-20 gap-x-4 gap-y-2 py-3 md:h-20 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:py-0"
          : "h-20 gap-4"
      } dark:border-brand-500/15 ${className}`}
    >
      {/* Left column: icon badge + title + subtitle. Equal-width side columns
          keep the centre column (audience switcher) at the same position on
          every page, whatever the title holds. */}
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border shadow-xs border-brand-200/80 bg-brand-50 text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/15 dark:text-brand-300">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 title={title} className="truncate text-sm font-bold text-fg tracking-tight">
              {title}
            </h1>
            {badgeCount !== undefined && badgeCount !== null ? (
              <span className="inline-flex shrink-0 items-center rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">
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
                        title={item.label}
                        className="truncate rounded-sm hover:text-accent-fg hover:underline focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500"
                      >
                        {item.label}
                      </Link>
                    ) : (
                      <span aria-current="page" title={item.label} className="truncate text-fg-muted">
                        {item.label}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </nav>
          ) : (
            subtitle && (
              // VA-43: up to two lines, then an ellipsis; the full text is the tooltip.
              <p title={subtitle} data-testid="header-subtitle" className="line-clamp-2 text-[11px] font-normal leading-tight text-fg-subtle">
                {subtitle}
              </p>
            )
          )}
        </div>
      </div>

      {/* Centre column: AudienceScopeTabs (or a custom slot), always centred.
          VA-33: below md it is a full-width second row, so the audience can
          still be changed on a phone. */}
      {hasCentre ? <div data-header-centre className="flex min-w-0 items-center justify-center *:w-full md:*:w-auto">{centre}</div> : null}
      {/* Empty right column keeps the side columns equal, so the switcher stays centred. */}
      {hasCentre ? <div aria-hidden="true" className="hidden md:block" /> : null}
    </header>
  );
}
