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
      className={`flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/80 bg-white px-5 py-3 ${className}`}
    >
      <div className="flex items-center gap-4 text-xs text-slate-500">
        <p>
          Showing <span className="font-medium text-slate-700">{start}</span>–
          <span className="font-medium text-slate-700">{end}</span> of{" "}
          <span className="font-medium text-slate-700">{totalItems.toLocaleString()}</span> entries
        </p>

        {onPageSizeChange && (
          <div className="flex items-center gap-1.5 pl-2 border-l border-slate-200">
            <label htmlFor="page-size-select" className="text-slate-500">
              Rows per page:
            </label>
            <select
              id="page-size-select"
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value) as PageSize)}
              className="rounded-md border border-slate-200 bg-slate-50/50 px-2 py-1 text-xs font-medium text-slate-700 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            >
              {ALLOWED_PAGE_SIZES.map((sz) => (
                <option key={sz} value={sz}>
                  {sz}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1">
        {/* Jump to first */}
        <button
          onClick={() => onPageChange(1)}
          disabled={page <= 1}
          aria-label="First page"
          title="First page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronsLeft className="h-4 w-4" />
        </button>

        {/* Previous */}
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
          title="Previous page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        {windowStart > 1 && <span className="px-1 text-xs text-slate-400">…</span>}

        {pageNumbers.map((p) => {
          const isCurrent = p === page;
          return (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              aria-current={isCurrent ? "page" : undefined}
              className={`flex h-7 min-w-7 items-center justify-center rounded-md px-1.5 text-xs font-medium transition-colors duration-150 ${
                isCurrent
                  ? "bg-brand-gradient text-white shadow-brand-glow font-semibold"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              }`}
            >
              {p}
            </button>
          );
        })}

        {windowEnd < safePageCount && <span className="px-1 text-xs text-slate-400">…</span>}

        {/* Next */}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= safePageCount}
          aria-label="Next page"
          title="Next page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight className="h-4 w-4" />
        </button>

        {/* Jump to last */}
        <button
          onClick={() => onPageChange(safePageCount)}
          disabled={page >= safePageCount}
          aria-label="Last page"
          title="Last page"
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronsRight className="h-4 w-4" />
        </button>
      </div>
    </nav>
  );
}
