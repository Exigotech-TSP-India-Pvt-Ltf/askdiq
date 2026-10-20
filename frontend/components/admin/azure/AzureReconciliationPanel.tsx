"use client";

/**
 * Governance > Monitoring "Azure vs AskDIQ": Azure Monitor platform metrics
 * of each deployment against what AskDIQ recorded (requests, tokens, 429s,
 * latency), for the last 24 hours or per UTC day. Rows whose drift reaches
 * AZURE_RECONCILIATION_DRIFT_PCT are highlighted. Read from the
 * `/admin/azure/metrics/summary` snapshot; refreshed live after each poll.
 */
import React, { useMemo, useState } from "react";
import { Cloud } from "lucide-react";
import { ChartCard } from "@/components/charts/ChartCard";
import { StudioSectionHeader, StudioSegmented } from "@/components/ui/StudioCard";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/query-cache";
import { formatPct } from "@/lib/governance-format";
import {
  AZURE_SUMMARY_DAYS,
  azureApi,
  azureKeys,
  validSummary,
  type AzureMetricsSummary,
  type ReconciliationCell,
  type ReconciliationRow,
} from "@/lib/azure-metrics-api";
import {
  AzureNote,
  AzureStatePills,
  NOT_CONNECTED_MESSAGE,
  RoleChip,
  dayLabel,
  formatCell,
  formatDrift,
  isNotConnected,
} from "@/components/admin/azure/azure-format";

export type ReconciliationMode = "24h" | "daily";

const MODES = [
  { id: "24h" as const, label: "Last 24 h" },
  { id: "daily" as const, label: "Per day" },
];

/** The table rows for a mode (per day: newest day first, as served). */
export function reconciliationRows(summary: AzureMetricsSummary | undefined, mode: ReconciliationMode): ReconciliationRow[] {
  if (!summary) return [];
  return mode === "24h" ? summary.last_24h : summary.daily;
}

/** Drifted rows in a mode (the header badge). */
export function driftedCount(rows: readonly ReconciliationRow[]): number {
  return rows.filter((r) => r.drifted).length;
}

function Compare({
  cell,
  kind = "count",
  testId,
}: {
  cell: ReconciliationCell;
  kind?: "count" | "ms";
  testId?: string;
}) {
  return (
    <td className="px-3 py-2 text-right align-top tabular-nums" data-testid={testId}>
      <div className="font-semibold text-fg">{formatCell(cell.azure, kind)}</div>
      <div className="text-[11px] text-fg-subtle">AskDIQ {formatCell(cell.askdiq, kind)}</div>
      {cell.drift_pct !== null ? (
        <div
          className={`text-[11px] font-semibold ${cell.drifted ? "text-status-warn-fg" : "text-fg-subtle"}`}
          data-drifted={cell.drifted ? "true" : undefined}
        >
          {formatDrift(cell)} drift
        </div>
      ) : null}
    </td>
  );
}

const HEAD = "px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-fg-subtle";

export function ReconciliationTable({
  rows,
  mode,
  threshold,
}: {
  rows: readonly ReconciliationRow[];
  mode: ReconciliationMode;
  threshold: number;
}) {
  return (
    <table className="w-full min-w-[880px] text-sm" data-testid="azure-reconciliation-table">
      <caption className="sr-only">
        Azure Monitor vs AskDIQ per deployment{mode === "daily" ? " per UTC day" : ", last 24 hours"}; rows with drift at or above{" "}
        {formatPct(threshold, 0)} are highlighted.
      </caption>
      <thead className="border-b border-line">
        <tr>
          <th scope="col" className={`${HEAD} text-left`}>
            Deployment
          </th>
          {mode === "daily" ? (
            <th scope="col" className={`${HEAD} text-left`}>
              Day (UTC)
            </th>
          ) : null}
          <th scope="col" className={HEAD}>
            Requests
          </th>
          <th scope="col" className={HEAD}>
            Tokens in
          </th>
          <th scope="col" className={HEAD}>
            Tokens out
          </th>
          <th scope="col" className={HEAD}>
            429s
          </th>
          <th scope="col" className={HEAD}>
            Latency avg
          </th>
          <th scope="col" className={HEAD}>
            Latency max
          </th>
          <th scope="col" className={HEAD}>
            Availability
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={`${row.deployment}|${row.day ?? "24h"}`}
            data-testid="azure-reconciliation-row"
            data-drifted={row.drifted ? "true" : "false"}
            className={`border-b border-line last:border-0 ${row.drifted ? "bg-status-warn-bg/40" : ""}`}
          >
            <th scope="row" className="px-3 py-2 text-left align-top font-medium text-fg">
              <div className="truncate" title={row.deployment}>
                {row.deployment}
              </div>
              <RoleChip role={row.role} />
            </th>
            {mode === "daily" ? (
              <td className="px-3 py-2 align-top text-fg-muted">{row.day ? dayLabel(row.day) : "—"}</td>
            ) : null}
            <Compare cell={row.requests} testId="cell-requests" />
            <Compare cell={row.tokens_in} />
            <Compare cell={row.tokens_out} />
            <Compare cell={row.throttled} testId="cell-429" />
            <Compare cell={row.latency_avg_ms} kind="ms" />
            <Compare cell={row.latency_max_ms} kind="ms" />
            <td className="px-3 py-2 text-right align-top tabular-nums text-fg">
              {formatCell(row.availability_pct, "pct")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AzureReconciliationPanel() {
  const { token } = useAuth();
  const [mode, setMode] = useState<ReconciliationMode>("24h");
  const query = useApiQuery<AzureMetricsSummary>(
    azureKeys.summary(AZURE_SUMMARY_DAYS),
    (signal) => azureApi.summary(token, AZURE_SUMMARY_DAYS, signal),
    { enabled: Boolean(token), revalidateOnFocus: false },
  );
  const summary = validSummary(query.data);
  const rows = useMemo(() => reconciliationRows(summary, mode), [summary, mode]);
  const drifted = driftedCount(rows);
  const notConnected = isNotConnected(summary);
  const threshold = summary?.drift_threshold_pct ?? 10;

  return (
    <section aria-labelledby="azure-recon" data-tour="governance-mon-azure" className="space-y-4">
      <StudioSectionHeader
        icon={Cloud}
        title={<span id="azure-recon">Azure vs AskDIQ</span>}
        subtitle={`Azure Monitor metrics (every caller of the resource) against AskDIQ's records. Rows with ≥ ${formatPct(threshold, 0)} drift are highlighted.`}
        actions={
          <>
            <AzureStatePills summary={summary} />
            <StudioSegmented ariaLabel="Reconciliation window" options={MODES} value={mode} onChange={setMode} />
          </>
        }
      />
      <ChartCard
        title={mode === "24h" ? "Last 24 hours" : `Per day, last ${summary?.days ?? AZURE_SUMMARY_DAYS} days`}
        subtitle="Azure value, AskDIQ value and drift per deployment"
        pill={drifted > 0 ? <span data-testid="azure-drift-count">{drifted} drifted</span> : undefined}
        tone={drifted > 0 ? "warn" : "brand"}
        loading={query.data === undefined && !query.error}
        error={summary ? null : query.error ? "Couldn't load the Azure reconciliation" : null}
        onRetry={() => void query.refetch()}
        empty={!summary || notConnected || rows.length === 0}
        emptyMessage={notConnected ? NOT_CONNECTED_MESSAGE : "No Azure or AskDIQ usage in this window yet"}
        bodyClassName="w-full overflow-x-auto"
      >
        <ReconciliationTable rows={rows} mode={mode} threshold={threshold} />
      </ChartCard>
      <AzureNote>
        Azure counts every caller of the deployment (playground, other apps, SDK retries); AskDIQ counts its own LLM attempts, failed
        calls (audit trail), classifier calls and query embeddings. Latency compares Azure time to last byte with AskDIQ&apos;s stage
        time and is never flagged.
      </AzureNote>
    </section>
  );
}
