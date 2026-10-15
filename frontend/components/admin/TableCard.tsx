"use client";

import React from "react";

export interface TableCardProps {
  title?: React.ReactNode;
  description?: string;
  badge?: React.ReactNode;
  headerActions?: React.ReactNode;
  toolbar?: React.ReactNode;
  children: React.ReactNode;
  pagination?: React.ReactNode;
  className?: string;
}

export function TableCard({
  title,
  description,
  badge,
  headerActions,
  toolbar,
  children,
  pagination,
  className = "",
}: TableCardProps) {
  const hasCardHeader = Boolean(title || description || badge || headerActions);

  return (
    <div
      data-testid="table-card"
      className={`overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 ${className}`}
    >
      {/* Optional Top Card Header (Title / Subtitle / Header Actions) */}
      {hasCardHeader && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-teal-100 dark:border-teal-500/20 bg-white dark:bg-[#0c0d10] px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              {typeof title === "string" ? (
                <h2 className="text-sm font-bold text-slate-900 dark:text-white tracking-tight">{title}</h2>
              ) : (
                title
              )}
              {badge}
            </div>
            {description && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{description}</p>
            )}
          </div>
          {headerActions && (
            <div className="flex items-center gap-2">{headerActions}</div>
          )}
        </div>
      )}

      {/* Toolbar Slot (Integrated Search & Single Filters Popover) */}
      {toolbar && (
        <div className="border-b border-teal-100 dark:border-teal-500/20 bg-teal-50/20 dark:bg-white/[0.02] px-4 py-3 sm:px-5">
          {toolbar}
        </div>
      )}

      {/* Table Grid Slot */}
      <div className="overflow-x-auto min-h-[300px]">
        {children}
      </div>

      {/* Bottom Pagination Slot */}
      {pagination && (
        <div>
          {pagination}
        </div>
      )}
    </div>
  );
}
