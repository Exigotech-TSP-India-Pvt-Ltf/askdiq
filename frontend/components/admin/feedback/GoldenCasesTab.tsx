"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { ArrowUpRight, CheckCircle2, Clock, FlaskConical, Loader2, Play, XCircle } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, secondaryButton } from "@/components/ui/styles";
import { pop, useMotionVariants } from "@/lib/motion";
import { useApiQuery } from "@/lib/query-cache";
import { RelativeTime } from "@/components/ui/RelativeTime";
import {
  feedbackKeys,
  listGoldenCases,
  reasonLabel,
  runGoldenCase,
  type GoldenCase,
  type GoldenCasePage,
} from "@/lib/feedback-api";

/** Poll the golden list while a run is queued (no job-status endpoint yet). */
export const GOLDEN_POLL_MS = 3000;
/** Give up polling after this many tries (~2 min) and say it is still running. */
export const GOLDEN_MAX_POLLS = 40;
const GOLDEN_PAGE_SIZE = 100;

type JobPhase = "queued" | "running" | "passed" | "failed" | "slow" | "error";

interface Job {
  phase: JobPhase;
  /** `last_run_at` before the run was requested; a new value means it finished. */
  baseline: string | null;
  polls: number;
}

const CHIP: Record<JobPhase, { label: string; className: string; icon: React.ElementType; spin?: boolean }> = {
  queued: { label: "Queued", className: "bg-status-info-bg text-status-info-fg", icon: Clock },
  running: { label: "Running…", className: "bg-status-info-bg text-status-info-fg", icon: Loader2, spin: true },
  passed: { label: "Passed", className: "bg-status-ok-bg text-status-ok-fg", icon: CheckCircle2 },
  failed: { label: "Failed", className: "bg-status-danger-bg text-status-danger-fg", icon: XCircle },
  slow: { label: "Still running", className: "bg-status-warn-bg text-status-warn-fg", icon: Clock },
  error: { label: "Could not queue", className: "bg-status-danger-bg text-status-danger-fg", icon: XCircle },
};

function JobChip({ phase }: { phase: JobPhase }) {
  const variants = useMotionVariants(pop);
  const { label, className, icon: Icon, spin } = CHIP[phase];
  return (
    <m.span
      key={phase}
      variants={variants}
      initial="hidden"
      animate="visible"
      exit="exit"
      role="status"
      data-testid="job-chip"
      data-phase={phase}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${className}`}
    >
      <Icon className={`h-3 w-3 ${spin ? "animate-spin motion-reduce:animate-none" : ""}`} strokeWidth={1.75} aria-hidden="true" />
      {label}
    </m.span>
  );
}

function LastResult({ c }: { c: GoldenCase }) {
  if (c.last_run_at === null || c.last_run_passed === null) {
    return <span className="text-fg-subtle">Not run yet</span>;
  }
  const detail = c.last_run_detail;
  return (
    <div className="space-y-0.5">
      <span
        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
          c.last_run_passed ? "bg-status-ok-bg text-status-ok-fg" : "bg-status-danger-bg text-status-danger-fg"
        }`}
      >
        {c.last_run_passed ? "Passed" : "Failed"}
      </span>
      <p className="text-[11px] text-fg-subtle">
        <RelativeTime value={c.last_run_at} />
        {detail?.confidence !== undefined && detail?.confidence !== null ? ` · confidence ${detail.confidence.toFixed(2)}` : ""}
        {detail?.error ? ` · ${detail.error}` : ""}
      </p>
    </div>
  );
}

export interface GoldenCasesTabProps {
  token: string;
  canRun: boolean;
  /** Poll interval while a run is queued (tests shorten it). */
  pollMs?: number;
}

/** Golden Cases tab (planchanges4 F5.4): last evaluation result and "Run now" with a job chip. */
export function GoldenCasesTab({ token, canRun, pollMs = GOLDEN_POLL_MS }: GoldenCasesTabProps) {
  const toast = useToast();
  const fetcher = useCallback((signal: AbortSignal) => listGoldenCases(token, { limit: GOLDEN_PAGE_SIZE }, signal), [token]);
  const { data, error, isLoading, refetch } = useApiQuery<GoldenCasePage>(feedbackKeys.golden, fetcher);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;

  const active = Object.values(jobs).some((j) => j.phase === "queued" || j.phase === "running");

  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(async () => {
      const page = await refetch();
      setJobs((current) => {
        const next = { ...current };
        for (const [caseId, job] of Object.entries(current)) {
          if (job.phase !== "queued" && job.phase !== "running") continue;
          const c = page?.items.find((it) => it.id === caseId);
          if (c && c.last_run_at && c.last_run_at !== job.baseline) {
            next[caseId] = { ...job, phase: c.last_run_passed ? "passed" : "failed" };
          } else if (job.polls + 1 >= GOLDEN_MAX_POLLS) {
            next[caseId] = { ...job, phase: "slow", polls: job.polls + 1 };
          } else {
            next[caseId] = { ...job, phase: "running", polls: job.polls + 1 };
          }
        }
        return next;
      });
    }, pollMs);
    return () => clearInterval(timer);
  }, [active, refetch, pollMs]);

  const run = async (c: GoldenCase) => {
    const job = jobsRef.current[c.id];
    if (job && (job.phase === "queued" || job.phase === "running")) return;
    setJobs((j) => ({ ...j, [c.id]: { phase: "queued", baseline: c.last_run_at, polls: 0 } }));
    try {
      const res = await runGoldenCase(token, c.id);
      toast.show({
        title: res.status === "queued" ? "Evaluation queued" : "Already queued",
        description: res.status === "queued" ? `Running golden case ${c.id}.` : `A run of ${c.id} is already in progress.`,
        tone: "info",
      });
    } catch (err) {
      setJobs((j) => ({ ...j, [c.id]: { phase: "error", baseline: c.last_run_at, polls: 0 } }));
      toast.show({ title: "Could not queue the run", description: err instanceof Error ? err.message : undefined, tone: "error" });
    }
  };

  const items = data?.items ?? [];
  let body: React.ReactNode;
  if (error && !data) {
    body = <ErrorState title="Could not load golden cases" message={error instanceof Error ? error.message : undefined} onRetry={() => void refetch()} />;
  } else if (isLoading) {
    body = <SkeletonRows rows={4} columns={5} label="Loading golden cases" />;
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon={FlaskConical}
        title="No golden cases yet"
        description="Promote a rated answer from the Review Queue (p) to add it to the regression set."
      />
    );
  } else {
    body = (
      <table className="w-full text-left text-xs" aria-label="Golden cases">
        <thead className="border-b border-line-brand/70 bg-surface-studio dark:border-brand-500/20 text-[11px] uppercase tracking-wide text-fg-subtle">
          <tr>
            <th scope="col" className="px-4 py-2.5">Case</th>
            <th scope="col" className="px-3 py-2.5">Question</th>
            <th scope="col" className="px-3 py-2.5">Expectation</th>
            <th scope="col" className="px-3 py-2.5">Promoted</th>
            <th scope="col" className="px-3 py-2.5">Last evaluation</th>
            <th scope="col" className="px-4 py-2.5 text-right">Run</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => {
            const job = jobs[c.id];
            const running = job?.phase === "queued" || job?.phase === "running";
            return (
              <tr key={c.id} className="border-b border-line/70 last:border-0 hover:bg-brand-50/60 dark:hover:bg-brand-500/[0.07]">
                <td className="whitespace-nowrap px-4 py-3 font-mono text-[11px] text-fg-muted">{c.id}</td>
                <td className="max-w-md px-3 py-3">
                  <p className="line-clamp-2 text-fg">{c.query}</p>
                  {c.reason_tags.length > 0 ? (
                    <p className="mt-0.5 text-[11px] text-fg-subtle">{c.reason_tags.map(reasonLabel).join(", ")}</p>
                  ) : null}
                </td>
                <td className="whitespace-nowrap px-3 py-3 text-fg-muted">
                  {c.expect_grounded ? "Grounded answer" : "Not grounded"}
                  {c.expected_outcome ? ` · ${c.expected_outcome}` : ""}
                  {c.reference_answer ? <p className="text-[11px] text-status-ok-fg">Has reference answer</p> : null}
                </td>
                <td className="whitespace-nowrap px-3 py-3 text-fg-subtle">
                  <RelativeTime value={c.promoted_at} />
                  <p className="text-[11px]">{c.promoted_by}</p>
                </td>
                <td className="px-3 py-3">
                  <LastResult c={c} />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2">
                    <AnimatePresence mode="wait">{job ? <JobChip key={job.phase} phase={job.phase} /> : null}</AnimatePresence>
                    {canRun ? (
                      <button
                        type="button"
                        disabled={running}
                        onClick={() => void run(c)}
                        className={secondaryButton}
                        aria-label={`Run golden case ${c.id} now`}
                      >
                        <Play className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                        Run now
                      </button>
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  return (
    <TableCard
      title="Golden cases"
      description="Answers promoted from feedback into the regression suite, with their last evaluation."
      badge={
        data ? (
          <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">{data.total.toLocaleString()}</span>
        ) : null
      }
      headerActions={
        <Link
          href="/admin/evaluation-live"
          className={`group inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-brand-700 transition-colors hover:bg-surface-muted dark:text-brand-300 ${focusRing}`}
        >
          Evaluation (Live)
          <ArrowUpRight
            className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </Link>
      }
    >
      {body}
    </TableCard>
  );
}
