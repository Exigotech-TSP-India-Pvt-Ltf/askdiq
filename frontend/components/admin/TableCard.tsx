"use client";

import React from "react";
import { CursorPagination, type CursorPaginationProps } from "@/components/admin/CursorPagination";
import { studioDivider, studioSurface, studioTitle } from "@/components/ui/styles";

// Studio surface without the hover lift (tables are work surfaces): edge glows on hover / focus.
const CARD = `${studioSurface} transition-[box-shadow,border-color] duration-150 hover:border-brand-400/70 hover:shadow-studio-hover focus-within:border-brand-400/70 motion-reduce:transition-none dark:hover:border-brand-400/50`;

export interface TableCardProps {
  title?: React.ReactNode;
  description?: string;
  badge?: React.ReactNode;
  headerActions?: React.ReactNode;
  toolbar?: React.ReactNode;
  children: React.ReactNode;
  pagination?: React.ReactNode;
  /** Cursor-pagination variant (used when `pagination` is not given). */
  cursor?: CursorPaginationProps;
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
  cursor,
  className = "",
}: TableCardProps) {
  const footer = pagination ?? (cursor ? <CursorPagination {...cursor} /> : null);
  const hasCardHeader = Boolean(title || description || badge || headerActions);

  return (
    <div
      data-testid="table-card"
      className={`${CARD} ${className}`}
    >
      {/* Optional Top Card Header (Title / Subtitle / Header Actions) */}
      {hasCardHeader && (
        <div className={`relative z-10 flex flex-wrap items-center justify-between gap-3 border-b px-5 py-4 ${studioDivider}`}>
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              {typeof title === "string" ? (
                <h2 className={studioTitle}>{title}</h2>
              ) : (
                title
              )}
              {badge}
            </div>
            {description && (
              <p className="mt-0.5 text-xs text-fg-subtle dark:text-brand-200/70">{description}</p>
            )}
          </div>
          {headerActions && (
            <div className="flex items-center gap-2">{headerActions}</div>
          )}
        </div>
      )}

      {/* Toolbar Slot (Integrated Search & Single Filters Popover) */}
      {toolbar && (
        <div className={`relative z-10 border-b bg-surface-studio/40 px-4 py-3 dark:bg-white/[0.02] sm:px-5 ${studioDivider}`}>
          {toolbar}
        </div>
      )}

      {/* Table Grid Slot */}
      <div className="relative z-10 min-h-[300px] overflow-x-auto">
        {children}
      </div>

      {/* Bottom Pagination Slot */}
      {footer && (
        <div className="relative z-10">
          {footer}
        </div>
      )}
    </div>
  );
}
