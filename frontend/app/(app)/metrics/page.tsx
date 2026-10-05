"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { LatencyReport } from "@/lib/types";

const LATENCY_BUDGET_MS = 200;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink/10 px-4 py-3.5">
      <p className="text-[11px] uppercase tracking-wide text-ink/40">{label}</p>
      <p className="mt-1 text-xl font-semibold text-ink">{value}</p>
    </div>
  );
}

export default function MetricsPage() {
  const { token } = useAuth();
  const [report, setReport] = useState<LatencyReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .latencyReport(token)
      .then(setReport)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load latency metrics.")
      );
  }, [token]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-6">
        <h1 className="text-sm font-semibold text-ink">Query latency</h1>
        <p className="text-xs text-ink/50">
          Retrieval + guardrails are measured against the {LATENCY_BUDGET_MS}ms budget.
          End-to-end and LLM generation are reported separately for visibility only —
          a real LLM call regularly takes several seconds.
        </p>
      </header>

      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {!error && !report && (
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      )}

      {report && (
        <div className="space-y-8">
          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink/40">
              Retrieval + guardrails (budgeted)
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <Stat label="Samples" value={String(report.pipeline_ex_llm.sample_size)} />
              <Stat label="P50" value={`${Math.round(report.pipeline_ex_llm.p50_ms)} ms`} />
              <Stat label="P70" value={`${Math.round(report.pipeline_ex_llm.p70_ms)} ms`} />
              <Stat label="P100" value={`${Math.round(report.pipeline_ex_llm.p100_ms)} ms`} />
              <Stat
                label="Within budget"
                value={`${report.pipeline_ex_llm.within_budget_pct.toFixed(1)}%`}
              />
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink/40">
              End-to-end (informational, includes LLM)
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Samples" value={String(report.end_to_end.sample_size)} />
              <Stat label="P50" value={`${Math.round(report.end_to_end.p50_ms)} ms`} />
              <Stat label="P70" value={`${Math.round(report.end_to_end.p70_ms)} ms`} />
              <Stat label="P100" value={`${Math.round(report.end_to_end.p100_ms)} ms`} />
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink/40">
              LLM generation (informational)
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Samples" value={String(report.llm.sample_size)} />
              <Stat label="P50" value={`${Math.round(report.llm.p50_ms)} ms`} />
              <Stat label="P70" value={`${Math.round(report.llm.p70_ms)} ms`} />
              <Stat label="P100" value={`${Math.round(report.llm.p100_ms)} ms`} />
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink/40">
              By pipeline stage
            </h2>
            <div className="overflow-hidden rounded-xl border border-ink/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-ink/[0.03] text-xs uppercase tracking-wide text-ink/40">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Stage</th>
                    <th className="px-4 py-2.5 font-medium">P50</th>
                    <th className="px-4 py-2.5 font-medium">P70</th>
                    <th className="px-4 py-2.5 font-medium">P100</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink/5">
                  {report.by_stage.map((stage) => (
                    <tr key={stage.stage}>
                      <td className="px-4 py-2.5 font-medium capitalize text-ink">
                        {stage.stage.replace(/_/g, " ")}
                      </td>
                      <td className="px-4 py-2.5 text-ink/60">{Math.round(stage.p50_ms)} ms</td>
                      <td className="px-4 py-2.5 text-ink/60">{Math.round(stage.p70_ms)} ms</td>
                      <td className="px-4 py-2.5 text-ink/60">{Math.round(stage.p100_ms)} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
