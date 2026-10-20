"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Timer } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useApiQuery } from "@/lib/query-cache";
import { useDeepLink } from "@/lib/use-deep-link";
import { useRange } from "@/lib/range-context";
import { useCursorStack } from "@/lib/use-cursor-stack";
import { useAutoRefresh } from "@/lib/use-governance-stream";
import {
  LATENCY_BUCKETS,
  TRACE_OUTCOMES,
  TRACE_STATUSES,
  governanceApi,
  governanceKeys,
  type GovernanceTraceItem,
  type GovernanceTracePage,
} from "@/lib/governance-api";
import { formatCount, formatMs, formatUsd, humanize } from "@/lib/governance-format";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { parseOne, rangeBounds } from "@/lib/governance-links";
import { prefetchTrace, prefetchTraceNeighbours } from "@/lib/governance-prefetch";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { TraceStatusBadge } from "@/components/admin/governance/badges";
import { TraceDrawer, stageLabel } from "@/components/admin/governance/TraceDrawer";
import { focusRing } from "@/components/ui/styles";

export const TRACES_PAGE_SIZE = 10;

const LATENCY_LABEL: Record<string, string> = {
  fast: "Fast",
  moderate: "Moderate",
  slow: "Slow",
  very_slow: "Very slow",
};

const LATENCY_TONE: Record<string, string> = {
  fast: "bg-status-ok-bg text-status-ok-fg",
  moderate: "bg-status-info-bg text-status-info-fg",
  slow: "bg-status-warn-bg text-status-warn-fg",
  very_slow: "bg-status-danger-bg text-status-danger-fg",
};

function shortId(id: string | null, head = 10): string {
  if (!id) return "—";
  return id.length > head ? `${id.slice(0, head)}…` : id;
}

function traceColumns(open: (id: string) => void): ColumnDef<GovernanceTraceItem>[] {
  return [
    {
      key: "request_id",
      header: "Request",
      render: (t) => (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            open(t.request_id);
          }}
          title={`Open trace ${t.request_id}`}
          className={`rounded font-mono text-[11px] text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
        >
          {shortId(t.request_id, 12)}
        </button>
      ),
    },
    {
      key: "timestamp",
      header: "Time",
      render: (t) => <RelativeTime value={t.timestamp} className="text-xs text-fg-muted" />,
    },
    {
      key: "ip_address",
      header: "IP",
      render: (t) => <span className="font-mono text-xs text-fg-muted">{t.ip_address ?? "—"}</span>,
    },
    {
      key: "query",
      header: "Query",
      render: (t) => (
        <span className="block max-w-[18rem] truncate text-xs text-fg" title={t.query ?? undefined}>
          {t.query ?? "—"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status / outcome",
      render: (t) => (
        <span className="flex flex-col items-start gap-0.5">
          <TraceStatusBadge status={t.status} />
          {t.outcome ? <span className="text-[10px] text-fg-subtle">{humanize(t.outcome)}</span> : null}
        </span>
      ),
    },
    {
      key: "total_ms",
      header: "Latency",
      align: "right",
      render: (t) => (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${
            LATENCY_TONE[t.latency_bucket ?? ""] ?? "bg-surface-muted text-fg-muted"
          }`}
        >
          <Timer className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
          {formatMs(t.total_ms)}
        </span>
      ),
    },
    {
      key: "slowest_stage",
      header: "Slowest stage",
      render: (t) => (
        <span className="whitespace-nowrap text-xs text-fg-muted">
          {t.slowest_stage ? `${stageLabel(t.slowest_stage.stage)} · ${formatMs(t.slowest_stage.ms)}` : "—"}
        </span>
      ),
    },
    {
      key: "tokens",
      header: "Tokens",
      align: "right",
      render: (t) => <span className="text-xs tabular-nums text-fg-muted">{formatCount(t.tokens.total)}</span>,
    },
    {
      key: "cost_usd",
      header: "Cost",
      align: "right",
      render: (t) => <span className="text-xs tabular-nums text-fg-muted">{formatUsd(t.cost_usd)}</span>,
    },
  ];
}

const option = (value: string, label: string) => ({ value, label });

export default function TracesView() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const { get, set } = useDeepLink();
  const rangeCtx = useRange();
  const { from, to } = rangeBounds(rangeCtx?.range);

  const status = parseOne(get("status"), TRACE_STATUSES);
  const outcome = parseOne(get("outcome"), TRACE_OUTCOMES);
  const latencyBucket = parseOne(get("latency_bucket"), LATENCY_BUCKETS);
  const q = (get("q") ?? "").slice(0, 200);
  const requestId = get("request_id");
  const [autoRefresh, setAutoRefresh] = React.useState(false);

  const [pageSize, setPageSize] = useState<number>(TRACES_PAGE_SIZE);
  const filterKey = governanceKeys.tracesFilter({ audience, status, outcome, latencyBucket, q, from, to, pageSize });
  const pager = useCursorStack(filterKey);

  const query = useApiQuery<GovernanceTracePage>(
    governanceKeys.traces(filterKey, pager.cursor),
    (signal) =>
      governanceApi.traces(
        token,
        {
          audience,
          limit: pageSize,
          cursor: pager.cursor,
          status: status ? [status] : undefined,
          outcome: outcome ? [outcome] : undefined,
          latency_bucket: latencyBucket ? [latencyBucket] : undefined,
          q: q || undefined,
          from,
          to,
        },
        signal,
      ),
    { enabled: Boolean(token) },
  );
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page]);
  const { refetch } = query;
  const live = useAutoRefresh(autoRefresh && !pager.hasPrev, () => void refetch());

  const openTrace = useCallback((id: string | null) => set({ request_id: id }, { history: id ? "push" : "replace" }), [set]);
  const idx = requestId ? items.findIndex((t) => t.request_id === requestId) : -1;
  const onPrev = idx > 0 ? () => openTrace(items[idx - 1].request_id) : undefined;
  const onNext = idx >= 0 && idx < items.length - 1 ? () => openTrace(items[idx + 1].request_id) : undefined;
  // Drawer next / previous open from cache.
  useEffect(() => {
    if (idx >= 0) prefetchTraceNeighbours(token, items, idx, audience);
  }, [audience, idx, items, token]);
  const onRowIntent = useCallback((row: GovernanceTraceItem) => prefetchTrace(token, row.request_id, audience), [audience, token]);

  const columns = useMemo(() => traceColumns((id) => openTrace(id)), [openTrace]);
  const total = page ? `${page.total_is_estimate ? "≈ " : ""}${formatCount(page.total)} traces` : undefined;

  return (
    <>
      <div data-tour="governance-traces-table">
      <TableCard
        title="Request traces"
        description="Every /query request with its derived status, latency, tokens and cost. Select a row for the stage waterfall."
        badge={
          total ? (
            <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">{total}</span>
          ) : null
        }
        headerActions={
          <button
            type="button"
            data-tour="governance-traces-autorefresh"
            aria-pressed={autoRefresh}
            onClick={() => setAutoRefresh((v) => !v)}
            className={`inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium transition-colors ${
              autoRefresh ? "bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300" : "text-fg-muted hover:text-fg"
            } ${focusRing}`}
          >
            <RefreshCw className={`h-4 w-4 ${live ? "motion-safe:animate-spin" : ""}`} strokeWidth={1.75} aria-hidden="true" />
            Auto-refresh {autoRefresh ? (live ? "on" : "paused") : "off"}
          </button>
        }
        toolbar={
          <div data-tour="governance-traces-filters">
          <TableToolbar
            search={q}
            onSearchChange={(v) => set({ q: v || null })}
            debounceMs={300}
            searchPlaceholder="Search query text or request ID…"
            filters={[
              {
                id: "status",
                label: "Status",
                value: status ?? "all",
                options: [option("all", "All statuses"), ...TRACE_STATUSES.map((s) => option(s, humanize(s)))],
                onChange: (v) => set({ status: v === "all" ? null : v }),
              },
              {
                id: "outcome",
                label: "Outcome",
                value: outcome ?? "all",
                options: [option("all", "All outcomes"), ...TRACE_OUTCOMES.map((s) => option(s, humanize(s)))],
                onChange: (v) => set({ outcome: v === "all" ? null : v }),
              },
              {
                id: "latency_bucket",
                label: "Latency",
                value: latencyBucket ?? "all",
                options: [option("all", "Any latency"), ...LATENCY_BUCKETS.map((s) => option(s, LATENCY_LABEL[s]))],
                onChange: (v) => set({ latency_bucket: v === "all" ? null : v }),
              },
            ]}
            countLabel={rangeCtx ? `Range: ${rangeCtx.range.label}` : undefined}
          />
          </div>
        }
        cursor={{
          hasPrev: pager.hasPrev,
          hasNext: Boolean(page?.has_more && page.next_cursor),
          onPrev: pager.prev,
          onNext: () => pager.next(page?.next_cursor),
          pageItemCount: items.length,
          itemLabel: "traces",
          isLoading: query.isValidating,
          page: pager.page,
          totalItems: page?.total,
          pageSize,
          onPageSizeChange: setPageSize,
          onFirst: pager.reset,
        }}
      >
        <DataTable
          columns={columns}
          data={items}
          keyField="request_id"
          isLoading={query.isLoading}
          error={query.error && !page ? "Couldn't load traces." : null}
          onRetry={() => void refetch()}
          onRowClick={(row) => openTrace(row.request_id)}
          onRowIntent={onRowIntent}
          emptyMessage="No request traces match these filters."
        />
      </TableCard>
      </div>

      <TraceDrawer
        requestId={requestId}
        audience={audience}
        token={token}
        onClose={() => openTrace(null)}
        onPrev={onPrev}
        onNext={onNext}
      />
    </>
  );
}
