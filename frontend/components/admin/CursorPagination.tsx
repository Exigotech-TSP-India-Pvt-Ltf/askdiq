"use client";

import React from "react";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

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
  className?: string;
}

const navButton =
  "inline-flex items-center gap-1 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-dark-card px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 transition-colors hover:bg-slate-50 dark:hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500";

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
  className = "",
}: CursorPaginationProps) {
  return (
    <nav
      aria-label="Cursor pagination"
      className={`flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/80 dark:border-white/10 bg-slate-50/40 dark:bg-dark-surface px-5 py-3 ${className}`}
    >
      <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400" aria-live="polite">
        {isLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} aria-hidden="true" /> : null}
        Showing <span className="font-medium text-slate-700 dark:text-slate-200">{pageItemCount.toLocaleString()}</span> {itemLabel}
      </p>
      <div className="flex items-center gap-2">
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
