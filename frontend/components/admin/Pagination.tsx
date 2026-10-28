"use client";

import React from "react";
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
export const ALLOWED_PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof ALLOWED_PAGE_SIZES)[number];

export interface PaginationProps {
  page: number;
  pageCount: number;
  totalItems: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: PageSize) => void;
  onPrefetchNext?: () => void;
  className?: string;
}

/** Standardized, always-visible pagination footer for all admin tables (Task 6.5.2). */
export function Pagination({
  page,
  pageCount,
  totalItems,
  pageSize,
  onPageChange,
  onPageSizeChange,
  onPrefetchNext,
  className = "",
}: PaginationProps) {
  const safePageCount = Math.max(1, pageCount);
  const start = totalItems === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalItems);

  // Compute 5-page window around current page
  const pageNumbers: number[] = [];
  const windowStart = Math.max(1, page - 2);
  const windowEnd = Math.min(safePageCount, windowStart + 4);
  for (let p = windowStart; p <= windowEnd; p++) {
    pageNumbers.push(p);
  }

  return (
    <nav
      aria-label="Pagination Navigation"
      className={`flex flex-wrap items-center justify-between gap-3 border-t border-line bg-surface-studio/50 dark:bg-white/2 px-4 py-3 sm:px-5 ${className}`}
    >
      <div className="flex items-center gap-4 text-xs text-fg-subtle">
        {/* VA-35: on phones only "x–y of N" (no "Showing" / "Rows per page"). */}
        <p className="whitespace-nowrap">
          <span className="hidden sm:inline">Showing </span>
          <span className="font-medium text-fg">{start}</span>–
          <span className="font-medium text-fg">{end}</span> of{" "}
          <span className="font-medium text-fg">{totalItems.toLocaleString()}</span>
          <span className="hidden sm:inline"> entries</span>
        </p>

        {onPageSizeChange && (
          <div className="hidden items-center gap-1.5 border-l border-line pl-2 sm:flex">
            <label htmlFor="page-size-select" className="text-fg-subtle">
              Rows per page:
            </label>
            <select
              id="page-size-select"
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value) as PageSize)}
              className="rounded-md border border-line bg-surface-card px-2 py-1 text-xs font-medium text-fg focus:border-brand-500 focus:outline-hidden focus:ring-1 focus:ring-brand-500 transition-colors"
            >
              {ALLOWED_PAGE_SIZES.map((sz) => (
                <option key={sz} value={sz} className="bg-surface-elevated text-fg">
                  {sz}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 max-sm:mr-auto">
        {/* Jump to first */}
        <button
          onClick={() => onPageChange(1)}
          disabled={page <= 1}
          aria-label="First page"
          title="First page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors duration-150 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronsLeft className="h-4 w-4" />
        </button>

        {/* Previous */}
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
          title="Previous page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors duration-150 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        {windowStart > 1 && <span className="px-1 text-xs text-fg-subtle">…</span>}

        {pageNumbers.map((p) => {
          const isCurrent = p === page;
          return (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              aria-current={isCurrent ? "page" : undefined}
              className={`flex h-7 min-w-7 items-center justify-center rounded-md px-1.5 text-xs font-medium transition-colors duration-150 ${
                isCurrent
                  ? "bg-brand-600 dark:bg-brand-500 text-white shadow-2xs font-semibold"
                  : "text-fg-muted hover:bg-surface-muted hover:text-fg"
              }`}
            >
              {p}
            </button>
          );
        })}

        {windowEnd < safePageCount && <span className="px-1 text-xs text-fg-subtle">…</span>}

        {/* Next */}
        <button
          onClick={() => onPageChange(page + 1)}
          onMouseEnter={() => {
            if (page < safePageCount && onPrefetchNext) {
              try {
                onPrefetchNext();
              } catch {
                // Ignore prefetch errors gracefully
              }
            }
          }}
          disabled={page >= safePageCount}
          aria-label="Next page"
          title="Next page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors duration-150 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronRight className="h-4 w-4" />
        </button>

        {/* Jump to last */}
        <button
          onClick={() => onPageChange(safePageCount)}
          disabled={page >= safePageCount}
          aria-label="Last page"
          title="Last page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-fg-subtle transition-colors duration-150 hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronsRight className="h-4 w-4" />
        </button>
      </div>
    </nav>
  );
}
