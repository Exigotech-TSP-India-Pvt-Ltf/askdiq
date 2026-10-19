"use client";

import React from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { ALLOWED_PAGE_SIZES } from "./Pagination";

export interface CursorPaginationProps {
  /** Whether a newer / older page exists (from the API's cursors). */
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  /** Rows on the current page. */
  pageItemCount: number;
  /** e.g. "entries", "events". */
  itemLabel?: string;
  isLoading?: boolean;
  /** 1-based page reached through the cursor stack. */
  page?: number;
  /** Total matching rows (exact or estimate) to show "Page X of Y". */
  totalItems?: number;
  pageSize?: number;
  onPageSizeChange?: (size: number) => void;
  /** Jump back to the newest page. */
  onFirst?: () => void;
  className?: string;
}


const navButton =
  "inline-flex items-center gap-1 rounded-lg border border-line bg-surface-card px-2.5 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500";

/**
 * Footer for cursor-paginated endpoints (logs, traces, events): there is no
 * total count or page number, only "newer" and "older" pages.
 */
export function CursorPagination({
  hasPrev,
  hasNext,
  onPrev,
  onNext,
  pageItemCount,
  itemLabel = "entries",
  isLoading = false,
  page,
  totalItems,
  pageSize,
  onPageSizeChange,
  onFirst,
  className = "",
}: CursorPaginationProps) {
  const pageCount =
    totalItems !== undefined && pageSize ? Math.max(1, Math.ceil(totalItems / pageSize)) : undefined;
  const start = page && pageSize ? (page - 1) * pageSize + (pageItemCount ? 1 : 0) : undefined;
  const end = start !== undefined ? start + Math.max(0, pageItemCount - 1) : undefined;
  return (
    <nav
      aria-label="Cursor pagination"
      className={`flex flex-wrap items-center justify-between gap-3 border-t border-line bg-surface-studio/50 dark:bg-white/[0.02] px-5 py-3 ${className}`}
    >
      <p className="flex items-center gap-1.5 text-xs text-fg-subtle" aria-live="polite">
        {isLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} aria-hidden="true" /> : null}
        {start !== undefined && end !== undefined && totalItems !== undefined ? (
          <>
            Showing <span className="font-medium text-fg">{start.toLocaleString()}</span>–
            <span className="font-medium text-fg">{end.toLocaleString()}</span> of{" "}
            <span className="font-medium text-fg">{totalItems.toLocaleString()}</span> {itemLabel}
          </>
        ) : (
          <>
            Showing <span className="font-medium text-fg">{pageItemCount.toLocaleString()}</span> {itemLabel}
          </>
        )}
        {onPageSizeChange && pageSize ? (
          <span className="ml-3 flex items-center gap-1.5 border-l border-line pl-3">
            <label htmlFor="cursor-page-size" className="text-fg-subtle">
              Rows per page:
            </label>
            <select
              id="cursor-page-size"
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              className="rounded-md border border-line bg-surface-card px-2 py-1 text-xs font-medium text-fg focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            >
              {ALLOWED_PAGE_SIZES.map((sz) => (
                <option key={sz} value={sz} className="bg-surface-elevated text-fg">
                  {sz}
                </option>
              ))}
            </select>
          </span>
        ) : null}
      </p>
      <div className="flex items-center gap-2">
        {page ? (
          <span className="text-xs text-fg-subtle tabular-nums" data-testid="cursor-page">
            Page <span className="font-medium text-fg">{page}</span>
            {pageCount ? (
              <>
                {" "}of <span className="font-medium text-fg">{pageCount.toLocaleString()}</span>
              </>
            ) : null}
          </span>
        ) : null}
        {onFirst ? (
          <button type="button" onClick={onFirst} disabled={!hasPrev || isLoading} className={navButton}>
            Newest
          </button>
        ) : null}
        <button type="button" onClick={onPrev} disabled={!hasPrev || isLoading} className={navButton}>
          <ChevronLeft className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Newer
        </button>
        <button type="button" onClick={onNext} disabled={!hasNext || isLoading} className={navButton}>
          Older
          <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
