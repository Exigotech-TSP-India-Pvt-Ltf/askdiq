"use client";

import { Hourglass } from "lucide-react";
import type { PendingClassification } from "@/lib/insights-api";
import { fmtInt } from "./format";

function progress(p: PendingClassification): number {
  if (p.progress_pct !== null) return Math.max(0, Math.min(100, p.progress_pct));
  return p.total > 0 ? (100 * p.labelled) / p.total : 0;
}

/**
 * "N turns pending classification" with a progress bar (planchanges4 F6.3).
 * `variant="empty"` replaces the charts when nothing is labelled yet;
 * `"banner"` sits above them while a backlog is still being classified.
 */
export function PendingState({ pending, variant }: { pending: PendingClassification; variant: "empty" | "banner" }) {
  const pct = progress(pending);
  const title = `${fmtInt(pending.pending)} turns pending classification`;
  const bar = (
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
  );

  if (variant === "banner") {
    return (
      <div role="status" className="flex flex-col gap-2 rounded-xl border border-status-info/40 bg-status-info-bg p-3 sm:flex-row sm:items-center">
        <Hourglass className="h-4 w-4 shrink-0 text-status-info-fg" strokeWidth={1.75} aria-hidden="true" />
        <p className="text-xs text-status-info-fg">
          <span className="font-semibold">{title}.</span> Topics and quality include labelled turns only.
        </p>
        <div className="sm:ml-auto sm:w-48">{bar}</div>
      </div>
    );
  }

  return (
    <div role="status" className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-line bg-surface-card px-6 py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
        <Hourglass className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />
      </div>
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      <p className="max-w-md text-xs text-fg-muted">
        The classifier labels each turn with an intent, answer quality and issue in the background. Charts appear once turns are labelled.
      </p>
      <div className="w-full max-w-sm">{bar}</div>
      <p className="text-[11px] text-fg-subtle">
        {fmtInt(pending.labelled)} of {fmtInt(pending.total)} turns labelled ({pct.toFixed(0)}%)
      </p>
    </div>
  );
}
