"use client";

import React from "react";
import { Cloud } from "lucide-react";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { Skeleton } from "@/components/ui/Skeleton";
import { useApiQuery } from "@/lib/query-cache";
import { azureApi, azureKeys, type AzureSourceState, type AzureSourceStatus, type AzureStatus } from "@/lib/azure-api";

const PILL = "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold";
const TONE: Record<AzureSourceState, string> = {
  ok: "border-status-ok/40 bg-status-ok-bg text-status-ok-fg",
  error: "border-status-danger/40 bg-status-danger-bg text-status-danger-fg",
  budget_exhausted: "border-status-danger/40 bg-status-danger-bg text-status-danger-fg",
  throttled: "border-status-warn/40 bg-status-warn-bg text-status-warn-fg",
  unsupported: "border-line bg-surface-muted text-fg-muted",
  never_run: "border-line bg-surface-muted text-fg-muted",
  not_configured: "border-line bg-surface-muted text-fg-muted",
};
const STATE_LABEL: Record<AzureSourceState, string> = {
  ok: "OK",
  error: "Error",
  budget_exhausted: "Call cap reached",
  throttled: "Throttled",
  unsupported: "Not offered for this subscription",
  never_run: "Not run yet",
  not_configured: "Not connected",
};

function SourceRow({ source }: { source: AzureSourceStatus }) {
  const error = source.state !== "ok" && source.last_error_code;
  return (
    <tr className="align-top">
      <th scope="row" className="py-2 pl-2 pr-3 text-left font-medium text-fg">
        {source.label}
        {source.needs_credentials ? null : <span className="ml-1 text-[11px] font-normal text-fg-subtle">(public)</span>}
      </th>
      <td className="py-2 pl-2 pr-3">
        <span className={`${PILL} ${TONE[source.state] ?? TONE.never_run}`}>{STATE_LABEL[source.state] ?? source.state}</span>
        {error ? (
          <p className="mt-1 max-w-xs wrap-break-word text-[11px] text-status-danger-fg" title={source.last_error_message ?? undefined}>
            {source.last_error_code}
            {source.last_error_message ? `: ${source.last_error_message}` : ""}
          </p>
        ) : null}
      </td>
      <td className="py-2 pl-2 pr-3 text-fg-muted">
        <RelativeTime value={source.last_success_at} fallback="Never" />
      </td>
      <td className="py-2 pl-2 text-fg-muted">
        {source.scheduled ? <RelativeTime value={source.next_run_at} fallback="Pending" /> : "Not scheduled"}
      </td>
    </tr>
  );
}

/** Pure body (tested): the stored Azure Insights status. */
export function AzureConnectionBody({ status }: { status: AzureStatus }) {
  const cap = status.metrics_call_cap;
  const used = status.metrics_calls_this_month;
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div className="space-y-4">
      {status.connected ? null : (
        <p role="status" className="rounded-lg bg-surface-muted px-3 py-2 text-xs text-fg-muted">
          Not connected. Set AZURE_INSIGHTS_ENABLED and the Azure ids on the server (see foundry-usage-data-review.md, “How
          to enable”). Official retail prices still sync on demand in Settings &gt; Models.
        </p>
      )}
      <div>
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-fg-muted">Metrics calls this month</span>
          <span className="tabular-nums text-fg">
            {used.toLocaleString("en-US")} / {cap.toLocaleString("en-US")}
          </span>
        </div>
        <div
          className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted"
          role="progressbar"
          aria-label="Metrics calls used of the monthly cap"
          aria-valuemin={0}
          aria-valuemax={cap}
          aria-valuenow={used}
        >
          <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
        </div>
      </div>
      {status.sources.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-fg-subtle">
                <th scope="col" className="pb-1 pl-2 pr-3 font-medium">Source</th>
                <th scope="col" className="pb-1 pl-2 pr-3 font-medium">Status</th>
                <th scope="col" className="pb-1 pl-2 pr-3 font-medium">Last success</th>
                <th scope="col" className="pb-1 pl-2 font-medium">Next run</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {status.sources.map((source) => (
                <SourceRow key={source.source} source={source} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

/** True for a well-formed `GET /admin/azure/status` body. */
export function isAzureStatus(value: unknown): value is AzureStatus {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Partial<AzureStatus>;
  return (
    typeof v.connected === "boolean" &&
    typeof v.metrics_call_cap === "number" &&
    typeof v.metrics_calls_this_month === "number" &&
    Array.isArray(v.sources)
  );
}

/** Governance card: Azure Insights connection, per-source sync status and the metrics call cap. */
export function AzureConnectionCard() {
  const query = useApiQuery<AzureStatus>(azureKeys.status, (signal) => azureApi.status(signal), {
    staleTime: 60_000,
    revalidateOnFocus: false,
    keepPreviousData: true,
  });
  const status = isAzureStatus(query.data) ? query.data : undefined;
  return (
    <section aria-labelledby="gov-azure" className="rounded-xl border border-line bg-surface-card p-6">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 id="gov-azure" className="flex items-center gap-2 text-base font-semibold text-fg">
          <Cloud className="h-4 w-4 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
          Azure connection
        </h2>
        {status ? (
          <span className={`${PILL} ${status.connected ? TONE.ok : TONE.not_configured}`}>
            {status.connected ? `Connected${status.region ? ` · ${status.region}` : ""}` : "Not connected"}
          </span>
        ) : null}
      </div>
      {status ? (
        <AzureConnectionBody status={status} />
      ) : query.error || query.data !== undefined ? (
        <p className="text-xs text-fg-muted">Azure status is unavailable right now.</p>
      ) : (
        <Skeleton className="h-24 w-full rounded-lg" />
      )}
    </section>
  );
}
