"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

/** Simple prev/next + page-number pagination footer for admin tables. */
export function Pagination({
  page,
  pageCount,
  onPageChange,
  totalItems,
  pageSize,
}: {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  totalItems: number;
  pageSize: number;
}) {
  if (pageCount <= 1) return null;

  const start = totalItems === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, totalItems);

  const pageNumbers: number[] = [];
  const windowStart = Math.max(1, page - 2);
  const windowEnd = Math.min(pageCount, windowStart + 4);
  for (let p = windowStart; p <= windowEnd; p++) pageNumbers.push(p);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-purple-50 bg-white px-5 py-3">
      <p className="text-xs text-slate-500">
        Showing {start}–{end} of {totalItems}
      </p>

      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>

        {windowStart > 1 && <span className="px-1 text-xs text-slate-400">…</span>}

        {pageNumbers.map((p) => (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            className={`flex h-7 min-w-7 items-center justify-center rounded-md px-1.5 text-xs font-medium transition ${
              p === page
                ? "bg-purple-600 text-white"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {p}
          </button>
        ))}

        {windowEnd < pageCount && <span className="px-1 text-xs text-slate-400">…</span>}

        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pageCount}
          className="flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
