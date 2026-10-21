"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { Loader2, Play, Square } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { ActionTooltip } from "@/components/ui/ActionTooltip";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";
import { invalidate, useApiQuery } from "@/lib/query-cache";
import { useLiveConnected } from "@/lib/live-updates";
import {
  cancelEvaluationRun,
  evaluationRunEstimateKey,
  evaluationRunsKey,
  fetchEvaluationRunEstimate,
  fetchEvaluationRuns,
  formatDuration,
  formatUsd,
  isActiveRun,
  startEvaluationRun,
  type EvalRun,
} from "@/lib/evaluation-runs";

/** Fallback refresh while a run is active and the live stream is down. */
export const RUN_POLL_MS = 5_000;

export interface RunEvaluationControlProps {
  token: string;
  /** The viewer holds `eval:run` (the control renders nothing otherwise). */
  canRun: boolean;
  /** Fallback poll interval (tests shorten it). */
  pollMs?: number;
}

function ProgressChip({ run }: { run: EvalRun }) {
  const total = run.cases_total || 0;
  const text = run.status === "queued" ? "Queued" : `${run.cases_done} / ${total} cases`;
  return (
    <span
      data-testid="run-progress-chip"
      className="inline-flex items-center gap-1 rounded-full bg-status-info-bg px-2 py-0.5 text-[11px] font-semibold tabular-nums text-status-info-fg"
    >
      <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" />
      {text}
    </span>
  );
}

interface DialogProps {
  open: boolean;
  token: string;
  onCancel: () => void;
  onRun: (judge: boolean) => Promise<void>;
}

/** Confirmation: case count, estimated cost and duration, judge toggle. */
export function RunEvaluationDialog({ open, token, onCancel, onRun }: DialogProps) {
  const [judge, setJudge] = useState(false);
  const [busy, setBusy] = useState(false);
  const titleId = useId();
  const descId = useId();
  const judgeId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const estimate = useApiQuery(
    open ? evaluationRunEstimateKey(judge) : null,
    (signal) => fetchEvaluationRunEstimate(token, judge, signal),
    { keepPreviousData: true },
  );
  const e = estimate.data;

  useEffect(() => {
    if (!open) {
      setBusy(false);
      setJudge(false);
    }
  }, [open]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await onRun(judge);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? undefined : onCancel}
      labelledBy={titleId}
      describedBy={descId}
      initialFocusRef={cancelRef}
      className="max-w-md p-5"
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-fg">
            Run the full evaluation?
          </h2>
          <p id={descId} className="mt-1 text-xs leading-relaxed text-fg-muted">
            Every benchmark and golden case runs through the live pipeline in the background. Results replace the
            benchmark on this page when the run finishes.
          </p>
        </div>

        <dl className="grid grid-cols-3 gap-2" aria-live="polite" aria-busy={estimate.isLoading}>
          {[
            { label: "Cases", value: e ? String(e.case_count) : "…", testId: "estimate-cases" },
            { label: "Est. cost", value: e ? formatUsd(e.estimated_cost_usd) : "…", testId: "estimate-cost" },
            {
              label: "Est. duration",
              value: e ? formatDuration(e.estimated_duration_seconds) : "…",
              testId: "estimate-duration",
            },
          ].map((item) => (
            <div key={item.label} className="rounded-lg border border-line bg-surface-muted/40 px-3 py-2">
              <dt className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{item.label}</dt>
              <dd data-testid={item.testId} className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-fg">
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
        {e ? (
          <p className="text-[11px] text-fg-subtle">
            {e.basis === "history"
              ? `Based on the average of the last ${e.sample_runs} run${e.sample_runs === 1 ? "" : "s"}.`
              : "Rough guess: no finished run to average yet."}
          </p>
        ) : null}
        {estimate.error && !e ? (
          <p role="alert" className="text-xs text-status-danger-fg">
            Couldn&apos;t load the estimate.
          </p>
        ) : null}

        <label htmlFor={judgeId} className="flex cursor-pointer items-start gap-3 rounded-lg border border-line p-3">
          <input
            id={judgeId}
            type="checkbox"
            role="switch"
            aria-checked={judge}
            checked={judge}
            onChange={(event) => setJudge(event.target.checked)}
            className={`mt-0.5 h-4 w-4 shrink-0 accent-brand-600 ${focusRing}`}
          />
          <span>
            <span className="block text-xs font-semibold text-fg">LLM judge</span>
            <span className="block text-[11px] text-fg-muted">
              Scores faithfulness, relevance and completeness: one extra model call per case.
            </span>
          </span>
        </label>

        <div className="flex justify-end gap-2">
          <button ref={cancelRef} type="button" onClick={onCancel} disabled={busy} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" disabled={busy} aria-busy={busy} className={primaryButton}>
            <Play className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            Run
          </button>
        </div>
      </form>
    </Modal>
  );
}

const FINISH_TOAST: Record<string, { title: string; tone: "success" | "error" | "info" }> = {
  succeeded: { title: "Evaluation finished", tone: "success" },
  failed: { title: "Evaluation failed", tone: "error" },
  cancelled: { title: "Evaluation cancelled", tone: "info" },
};

/**
 * "Run evaluation" on the Evaluation page: confirmation dialog, then a live
 * progress chip (x / N cases, refreshed by the console live stream) with a
 * cancel option. While any run is active the button is disabled for
 * everyone, with the reason in its tooltip.
 */
export function RunEvaluationControl({ token, canRun, pollMs = RUN_POLL_MS }: RunEvaluationControlProps) {
  const toast = useToast();
  const live = useLiveConnected();
  const [open, setOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const reasonId = useId();
  const runs = useApiQuery(
    canRun ? evaluationRunsKey() : null,
    (signal) => fetchEvaluationRuns(token, 0, undefined, signal),
    { keepPreviousData: true },
  );
  const active = runs.data?.active ?? null;
  const running = isActiveRun(active);
  const watched = useRef<string | null>(null);
  const { refetch } = runs;

  // Remember the active run; when it is gone, announce how it ended.
  useEffect(() => {
    if (!runs.data) return;
    if (active) {
      watched.current = active.id;
      return;
    }
    const id = watched.current;
    if (!id) return;
    watched.current = null;
    const done = runs.data.items.find((r) => r.id === id);
    const outcome = FINISH_TOAST[done?.status ?? ""] ?? FINISH_TOAST.succeeded;
    const passRate = typeof done?.pass_rate === "number" ? ` Pass rate ${(done.pass_rate * 100).toFixed(1)}%.` : "";
    toast.show({
      title: outcome.title,
      description: done?.status === "failed" ? done.error ?? undefined : `Results are on this page.${passRate}`,
      tone: outcome.tone,
    });
    invalidate("metrics/evaluation");
  }, [runs.data, active, toast]);

  // Live stream down: poll while a run is active.
  useEffect(() => {
    if (!running || live) return undefined;
    const timer = setInterval(() => void refetch(), pollMs);
    return () => clearInterval(timer);
  }, [running, live, pollMs, refetch]);

  if (!canRun) return null;

  async function start(judge: boolean) {
    try {
      const res = await startEvaluationRun(token, judge);
      watched.current = res.run_id;
      setOpen(false);
      toast.show({
        title: "Evaluation started",
        description: `${res.run.cases_total} cases queued${judge ? " with the LLM judge" : ""}.`,
        tone: "info",
      });
    } catch (err) {
      toast.show({
        title: "Could not start the evaluation",
        description: err instanceof Error ? err.message : undefined,
        tone: "error",
      });
    }
    await refetch();
  }

  async function cancel() {
    if (!active || cancelling) return;
    setCancelling(true);
    try {
      await cancelEvaluationRun(token, active.id);
      toast.show({ title: "Cancelling the evaluation", description: "It stops within a few seconds.", tone: "info" });
    } catch (err) {
      toast.show({
        title: "Could not cancel the evaluation",
        description: err instanceof Error ? err.message : undefined,
        tone: "error",
      });
    } finally {
      setCancelling(false);
      await refetch();
    }
  }

  const reason = running && active ? `An evaluation is running (started by ${active.started_by}).` : "";

  return (
    <div className="flex items-center gap-2">
      <ActionTooltip label={reason || "Run the full benchmark"} align="end" side="bottom">
        <button
          type="button"
          data-testid="run-evaluation-button"
          aria-disabled={running || undefined}
          aria-describedby={running ? reasonId : undefined}
          onClick={() => {
            if (!running) setOpen(true);
          }}
          className={`${primaryButton} h-9 ${running ? "cursor-not-allowed opacity-70 hover:bg-brand-600" : ""}`}
        >
          <Play className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          <span>Run evaluation</span>
          {running && active ? <ProgressChip run={active} /> : null}
        </button>
      </ActionTooltip>
      {running ? (
        <span id={reasonId} className="sr-only">
          {reason}
        </span>
      ) : null}
      <span className="sr-only" role="status" aria-live="polite">
        {running && active ? `Evaluation ${active.cases_done} of ${active.cases_total} cases done` : ""}
      </span>
      {running && active ? (
        <button
          type="button"
          onClick={() => void cancel()}
          disabled={cancelling || active.cancel_requested}
          className={`${secondaryButton} h-9`}
        >
          <Square className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
          {active.cancel_requested ? "Stopping…" : "Stop run"}
        </button>
      ) : null}
      <RunEvaluationDialog open={open} token={token} onCancel={() => setOpen(false)} onRun={start} />
    </div>
  );
}
