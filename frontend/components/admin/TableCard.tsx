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
  const hasCardHeader = Boolean(title || description || badge);

  return (
    <div
      data-testid="table-card"
      className={`${CARD} ${className}`}
    >
      {/* One header row: title (+ badge, description) on the left, search / filters
          and header actions on the right, so a table never shows its title in one
          band and its search alone on the row below. z-30 keeps the filter and
          column popovers above the table slot (same-z siblings paint in DOM order).
          The header is a size container: once it is at least 48rem wide the search,
          filters and actions stay on ONE row (beside the title when both fit,
          otherwise under it as a unit); narrower, they wrap after the search. */}
      {hasCardHeader || toolbar ? (
        <div
          data-testid="table-card-header"
          className={`relative z-30 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b px-4 [container-type:inline-size] sm:px-5 ${
            toolbar ? "bg-surface-studio/40 py-3 dark:bg-white/[0.02]" : "py-4"
          } ${studioDivider}`}
        >
          {hasCardHeader ? (
            <div className={`min-w-0 ${toolbar ? "flex-[1_1_16rem]" : "shrink"}`}>
              <div className="flex min-w-0 items-center gap-2.5">
                {typeof title === "string" ? <h2 className={`truncate ${studioTitle}`}>{title}</h2> : title}
                {badge}
              </div>
              {description && (
                <p className="mt-0.5 max-w-prose text-xs text-fg-subtle dark:text-brand-200/70">{description}</p>
              )}
            </div>
          ) : null}
          {toolbar || headerActions ? (
            <div
              data-testid="table-card-actions"
              className={`flex flex-wrap items-center justify-end gap-2 [@container(min-width:48rem)]:flex-nowrap ${
                hasCardHeader ? "" : "w-full"
              } ${toolbar ? "w-full [@container(min-width:48rem)]:w-auto [@container(min-width:48rem)]:grow" : ""}`}
            >
              {toolbar ? (
                <div className="w-full min-w-0 [@container(min-width:48rem)]:w-auto [@container(min-width:48rem)]:min-w-fit [@container(min-width:48rem)]:flex-1">
                  {toolbar}
                </div>
              ) : null}
              {headerActions ? (
                <div className="flex shrink-0 flex-wrap items-center gap-2 whitespace-nowrap">{headerActions}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

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
