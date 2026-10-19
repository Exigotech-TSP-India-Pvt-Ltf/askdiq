"use client";

import type { ReactNode } from "react";
import { ChartBoundary } from "@/components/charts/ChartBoundary";

/** Keeps a failing Analytics section (KPI strip, table, person panel) inside its own box. */
export function SectionBoundary({ label, resetKey, children }: { label: string; resetKey?: unknown; children: ReactNode }) {
  return (
    <ChartBoundary
      resetKey={resetKey}
      fallback={(reset) => (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-2xl border border-dashed border-status-danger/40 bg-status-danger-bg p-4 text-xs text-status-danger-fg"
        >
          <span>{label} could not be displayed.</span>
          <button type="button" onClick={reset} className="rounded-lg border border-current px-2 py-1 font-semibold">
            Retry
          </button>
        </div>
      )}
    >
      {children}
    </ChartBoundary>
  );
}
