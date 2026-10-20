"use client";

import { Hourglass } from "lucide-react";
import type { PendingClassification } from "@/lib/insights-api";
import { fmtInt } from "./format";

function progress(p: PendingClassification): number {
  if (p.progress_pct !== null) return Math.max(0, Math.min(100, p.progress_pct));
  return p.total > 0 ? (100 * p.labelled) / p.total : 0;
}

/**
 * "N turns pending classification" with a progress bar.
 * Sits above the charts while a backlog is being classified; the charts keep
 * rendering whatever is already labelled (each shows its own empty state), so
 * a partial backfill never hides the page.
 */
export function PendingState({ pending, nothingLabelled = false }: { pending: PendingClassification; nothingLabelled?: boolean }) {
  const pct = progress(pending);
  const title = `${fmtInt(pending.pending)} turns pending classification`;
  return (
    <div role="status" data-tour="analytics-pending" className="flex flex-col gap-2 rounded-xl border border-status-info/40 bg-status-info-bg p-3 sm:flex-row sm:items-center">
      <Hourglass className="h-4 w-4 shrink-0 text-status-info-fg" strokeWidth={1.75} aria-hidden="true" />
      <p className="text-xs text-status-info-fg">
        <span className="font-semibold">{title}.</span>{" "}
        {nothingLabelled
          ? "The classifier labels each turn in the background; charts fill in as turns are labelled."
          : "Topics and quality include labelled turns only."}
      </p>
      <div className="flex flex-col gap-1 sm:ml-auto sm:w-56">
        <div
          role="progressbar"
          aria-label="Classification progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
          aria-valuetext={`${fmtInt(pending.labelled)} of ${fmtInt(pending.total)} turns labelled`}
          className="h-2 w-full overflow-hidden rounded-full bg-surface-muted"
        >
          <div className="h-full rounded-full bg-brand-600 transition-[width] duration-200 dark:bg-brand-400" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-status-info-fg">
          {fmtInt(pending.labelled)} of {fmtInt(pending.total)} labelled ({pct.toFixed(0)}%)
        </p>
      </div>
    </div>
  );
}
