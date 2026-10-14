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
      className={`overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-sm transition-all duration-200 ${className}`}
    >
      {/* Optional Top Card Header (Title / Subtitle / Header Actions) */}
      {hasCardHeader && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 bg-white px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              {typeof title === "string" ? (
                <h2 className="text-sm font-bold text-slate-900 tracking-tight">{title}</h2>
              ) : (
                title
              )}
              {badge}
            </div>
            {description && (
              <p className="mt-0.5 text-xs text-slate-500">{description}</p>
            )}
          </div>
          {headerActions && (
            <div className="flex items-center gap-2">{headerActions}</div>
          )}
        </div>
      )}

      {/* Toolbar Slot (Integrated Search & Single Filters Popover) */}
      {toolbar && (
        <div className="border-b border-slate-200/90 bg-slate-50/50 px-4 py-3 sm:px-5">
          {toolbar}
        </div>
      )}

      {/* Table Grid Slot */}
      <div className="overflow-x-auto min-h-[300px]">
        {children}
      </div>

      {/* Bottom Pagination Slot */}
      {pagination && (
        <div className="border-t border-slate-200/90 bg-slate-50/30 px-4 py-2 sm:px-5">
          {pagination}
        </div>
      )}
    </div>
  );
}
