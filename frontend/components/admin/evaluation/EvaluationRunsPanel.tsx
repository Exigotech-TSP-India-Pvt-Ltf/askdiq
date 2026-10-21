"use client";

import React, { useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, History } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { focusRing, secondaryButton, studioSurface } from "@/components/ui/styles";
import { useApiQuery } from "@/lib/query-cache";
import {
  RUNS_PAGE_SIZE,
  STATUS_LABEL,
  evaluationRunKey,
  evaluationRunsKey,
  fetchEvaluationRun,
  fetchEvaluationRuns,
  formatDuration,
  formatUsd,
  type EvalRun,
  type EvalRunStatus,
} from "@/lib/evaluation-runs";

const STATUS_CLASS: Record<EvalRunStatus, string> = {
  queued: "bg-status-info-bg text-status-info-fg",
  running: "bg-status-info-bg text-status-info-fg",
  succeeded: "bg-status-ok-bg text-status-ok-fg",
  failed: "bg-status-danger-bg text-status-danger-fg",
  cancelled: "bg-status-warn-bg text-status-warn-fg",
};

const pct = (v: number | null | undefined) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");

function StatusBadge({ status }: { status: EvalRunStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_CLASS[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

function progressText(run: EvalRun): string {
  return run.status === "running" ? `${run.cases_done} / ${run.cases_total}` : String(run.cases_total);
}

export interface EvaluationRunsPanelProps {
  token: string;
  /** Run whose results are shown (deep link), or null for the history. */
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/** "Runs" tab: full evaluation history, and one run's results. */
export function EvaluationRunsPanel({ token, selectedId, onSelect }: EvaluationRunsPanelProps) {
  return selectedId ? (
    <RunResults token={token} id={selectedId} onBack={() => onSelect(null)} />
  ) : (
    <RunHistory token={token} onSelect={onSelect} />
  );
}

function RunHistory({ token, onSelect }: { token: string; onSelect: (id: string) => void }) {
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading, refetch } = useApiQuery(
    evaluationRunsKey(offset),
    (signal) => fetchEvaluationRuns(token, offset, RUNS_PAGE_SIZE, signal),
    { keepPreviousData: true },
  );
  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  let body: React.ReactNode;
  if (error && !data) {
    body = (
      <ErrorState
        title="Could not load evaluation runs"
        message={error instanceof Error ? error.message : undefined}
        onRetry={() => void refetch()}
      />
    );
  } else if (isLoading) {
    body = <SkeletonRows rows={4} columns={6} label="Loading evaluation runs" />;
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon={History}
        title="No evaluation runs yet"
        description="Start one with Run evaluation; its results are kept here."
      />
    );
  } else {
    body = (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs" aria-label="Evaluation runs">
          <thead className="border-b border-line-brand/70 bg-surface-studio text-[11px] uppercase tracking-wide text-fg-subtle dark:border-brand-500/20">
            <tr>
              <th scope="col" className="px-4 py-2.5">Started</th>
              <th scope="col" className="px-3 py-2.5">By</th>
              <th scope="col" className="px-3 py-2.5">Status</th>
              <th scope="col" className="px-3 py-2.5 text-right">Cases</th>
              <th scope="col" className="px-3 py-2.5 text-right">Pass rate</th>
              <th scope="col" className="px-3 py-2.5 text-right">Duration</th>
              <th scope="col" className="px-3 py-2.5 text-right">Cost</th>
              <th scope="col" className="px-4 py-2.5 text-right">
                <span className="sr-only">Results</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((run) => (
              <tr
                key={run.id}
                data-testid="run-row"
                className="border-b border-line/70 last:border-0 hover:bg-brand-50/60 dark:hover:bg-brand-500/[0.07]"
              >
                <td className="whitespace-nowrap px-4 py-3 text-fg">
                  <RelativeTime value={run.created_at} />
                  {run.options?.judge ? <p className="text-[11px] text-fg-subtle">LLM judge</p> : null}
                </td>
                <td className="max-w-[180px] truncate px-3 py-3 text-fg-muted" title={run.started_by}>
                  {run.started_by}
                </td>
                <td className="px-3 py-3">
                  <StatusBadge status={run.status} />
                </td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-fg-muted">{progressText(run)}</td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-fg">{pct(run.pass_rate)}</td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-fg-muted">
                  {formatDuration(run.duration_seconds)}
                </td>
                <td className="px-3 py-3 text-right font-mono tabular-nums text-fg-muted">{formatUsd(run.cost_usd)}</td>
                <td className="px-4 py-3 text-right">
                  {run.status === "succeeded" ? (
                    <button
                      type="button"
                      onClick={() => onSelect(run.id)}
                      className={`rounded-md px-2 py-1 text-[11px] font-semibold text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
                    >
                      View results
                      <span className="sr-only"> of the run started {run.created_at}</span>
                    </button>
                  ) : run.error ? (
                    <span className="text-[11px] text-status-danger-fg" title={run.error}>
                      Error
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const page = Math.floor(offset / RUNS_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / RUNS_PAGE_SIZE));
  return (
    <section aria-labelledby="eval-runs-title" className={`${studioSurface} p-0`}>
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <h2 id="eval-runs-title" className="text-sm font-semibold text-fg">
          Evaluation runs
        </h2>
        <nav aria-label="Run history pages" className="flex items-center gap-2 text-[11px] text-fg-subtle">
          <span aria-live="polite">
            Page {page} of {pages}
          </span>
          <button
            type="button"
            aria-label="Previous page"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - RUNS_PAGE_SIZE))}
            className={`${secondaryButton} px-2 py-1 disabled:opacity-40`}
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Next page"
            disabled={offset + RUNS_PAGE_SIZE >= total}
            onClick={() => setOffset(offset + RUNS_PAGE_SIZE)}
            className={`${secondaryButton} px-2 py-1 disabled:opacity-40`}
          >
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </nav>
      </div>
      {body}
    </section>
  );
}

function RunResults({ token, id, onBack }: { token: string; id: string; onBack: () => void }) {
  const { data, error, isLoading, refetch } = useApiQuery(evaluationRunKey(id), (signal) =>
    fetchEvaluationRun(token, id, signal),
  );
  const cases = data?.case_results ?? [];
  const summary = data?.summary ?? {};
  return (
    <section aria-labelledby="eval-run-title" className={`${studioSurface} p-4 space-y-4`}>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className={`${secondaryButton} px-2.5 py-1.5`}>
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          All runs
        </button>
        <h2 id="eval-run-title" className="text-sm font-semibold text-fg">
          Run <span className="font-mono">{id.slice(0, 8)}</span>
        </h2>
        {data ? <StatusBadge status={data.status} /> : null}
      </div>
      {error && !data ? (
        <ErrorState
          title="Could not load the run"
          message={error instanceof Error ? error.message : undefined}
          onRetry={() => void refetch()}
          compact
        />
      ) : isLoading || !data ? (
        <SkeletonRows rows={3} columns={4} label="Loading run results" />
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ["Pass rate", pct(data.pass_rate)],
              ["Groundedness", pct(summary.groundedness_accuracy)],
              ["Duration", formatDuration(data.duration_seconds)],
              ["Cost", formatUsd(data.cost_usd)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line bg-surface-muted/40 px-3 py-2">
                <dt className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{k}</dt>
                <dd className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-fg">{v}</dd>
              </div>
            ))}
          </dl>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs" aria-label="Case results">
              <thead className="border-b border-line text-[11px] uppercase tracking-wide text-fg-subtle">
                <tr>
                  <th scope="col" className="px-3 py-2">Case</th>
                  <th scope="col" className="px-3 py-2">Type</th>
                  <th scope="col" className="px-3 py-2">Question</th>
                  <th scope="col" className="px-3 py-2">Result</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c, i) => (
                  <tr key={`${String(c.id)}-${i}`} className="border-b border-line/70 last:border-0">
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-fg-muted">{String(c.id)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-fg-muted">{String(c.type ?? "")}</td>
                    <td className="max-w-md px-3 py-2 text-fg">
                      <p className="line-clamp-2">{String(c.query ?? "")}</p>
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                          c.passed ? "bg-status-ok-bg text-status-ok-fg" : "bg-status-danger-bg text-status-danger-fg"
                        }`}
                      >
                        {c.passed ? "Passed" : "Failed"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
