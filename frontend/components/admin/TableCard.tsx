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
          column popovers above the table slot (same-z siblings paint in DOM order). */}
      {hasCardHeader || toolbar ? (
        <div
          data-testid="table-card-header"
          className={`relative z-30 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-b px-4 sm:px-5 ${
            toolbar ? "bg-surface-studio/40 py-3 dark:bg-white/[0.02]" : "py-4"
          } ${studioDivider}`}
        >
          {/* The title side takes the leftover width (its description wraps);
              the controls keep their natural one-row width and wrap as a
              whole under the title only when the card is too narrow. */}
          {hasCardHeader ? (
            <div className="min-w-0 flex-1 basis-64">
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
              className={`ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-2 ${hasCardHeader ? "" : "w-full"}`}
            >
              {toolbar ? <div className={`min-w-0 max-w-full ${hasCardHeader ? "" : "flex-1"}`}>{toolbar}</div> : null}
              {headerActions ? <div className="flex items-center gap-2">{headerActions}</div> : null}
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
