"use client";

import React, { useEffect, useRef, useState } from "react";
import type { PageEnvelope, UsageQueryRow } from "@/lib/types";
import { api } from "@/lib/api";
import { useTableState } from "@/lib/use-table-state";
import { DataTable, type ColumnDef } from "../DataTable";
import { TableToolbar } from "../TableToolbar";
import { Pagination } from "../Pagination";
import { TableCard } from "../TableCard";
import { RelativeTime } from "@/components/ui/RelativeTime";
import dynamic from "next/dynamic";
import { StatCard, OutcomePillBar } from "../insights";

const QueryInspectorDrawer = dynamic(
  () => import("./QueryInspectorDrawer").then((mod) => mod.QueryInspectorDrawer),
  { ssr: false }
);
import {
  Sparkles,
  Clock,
  ShieldCheck,
  Cpu,
  Download,
  Eye,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
} from "lucide-react";
import {
  formatNumber,
  formatCurrency,
  formatTokens,
  formatLatency,
  OutcomeBadge,
} from "../TableFormatters";

interface QueriesTabProps {
  token: string;
  initialSessionId?: string;
  initialIp?: string;
  /** Day range from a deep link (YYYY-MM-DD, inclusive). */
  fromDay?: string;
  toDay?: string;
  /** Sets the IP filter; "" clears it. */
  onSelectIp?: (ip: string) => void;
  /** Sets the session filter; "" clears it. */
  onSelectSession?: (sessionId: string) => void;
  onClearDays?: () => void;
}

/** Toolbar pills and "Reset" send "all"; the clear option sends "". */
const isClear = (value: string) => value === "" || value === "all";

function dayRangeLabel(from: string, to: string): string {
  if (from && to) return from === to ? `Day: ${from}` : `Days: ${from} – ${to}`;
  return from ? `From ${from}` : `Until ${to}`;
}

export function QueriesTab({
  token,
  initialSessionId,
  initialIp,
  fromDay = "",
  toDay = "",
  onSelectIp,
  onSelectSession,
  onClearDays,
}: QueriesTabProps) {
  // IP, session and days are owned by the page URL (props), so a filter can
  // be cleared here and set again from a link without the two fighting.
  const sessionFilter = initialSessionId ?? "";
  const ipFilter = initialIp ?? "";
  const {
    page,
    pageSize,
    offset,
    sort,
    search,
    filters,
    audience,
    setPage,
    setPageSize,
    setSort,
    setSearchInput,
    setFilter,
    getSignal,
  } = useTableState({
    tableKey: "queries",
    defaultSort: "created_at_desc",
    allowedSorts: [
      "created_at_desc",
      "created_at_asc",
      "cost_desc",
      "cost_asc",
      "tokens_desc",
      "tokens_asc",
      "latency_desc",
      "latency_asc",
    ],
    defaultFilters: {
      outcome: "all",
      grounded: "all",
    },
  });

  const [data, setData] = useState<PageEnvelope<UsageQueryRow> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inspectRequestId, setInspectRequestId] = useState<string | null>(null);

  // A new IP / session / day filter starts from the first page.
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    setPage(1);
  }, [sessionFilter, ipFilter, fromDay, toDay, setPage]);

  const fetchData = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.getUsageQueries(
        token,
        {
          audience,
          q: search || undefined,
          session_id: sessionFilter || undefined,
          ip: ipFilter || undefined,
          from_date: fromDay || undefined,
          to_date: toDay || undefined,
          outcome: filters.outcome !== "all" ? filters.outcome : undefined,
          grounded:
            filters.grounded === "true"
              ? true
              : filters.grounded === "false"
              ? false
              : undefined,
          sort,
          limit: pageSize,
          offset,
        },
        getSignal()
      );
      setData(res);
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setError(err.message || "Failed to load queries analytics.");
      }
    } finally {
      setIsLoading(false);
    }
  }, [
    token,
    audience,
    search,
    sessionFilter,
    ipFilter,
    fromDay,
    toDay,
    filters.outcome,
    filters.grounded,
    sort,
    pageSize,
    offset,
    getSignal,
  ]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const stats = data?.stats || {};
  const outcomesCount = (stats.outcomes || {}) as Record<string, number>;

  const getOutcomeBadge = (outcome: string) => {
    switch (outcome?.toLowerCase()) {
      case "answered":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 border border-emerald-200">
            <CheckCircle2 className="w-3 h-3" />
            Answered
          </span>
        );
      case "ungrounded":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 border border-amber-200">
            <AlertTriangle className="w-3 h-3" />
            Ungrounded
          </span>
        );
      case "blocked_guardrail":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 border border-rose-200">
            <XCircle className="w-3 h-3" />
            Blocked
          </span>
        );
      case "conversational":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 border border-blue-200">
            <HelpCircle className="w-3 h-3" />
            Conversational
          </span>
        );
      case "clarify":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 border border-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:border-sky-500/30">
            <HelpCircle className="w-3 h-3" />
            Clarifying question
          </span>
        );
      case "llm_failed":
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-fg-muted border border-line-strong">
            <XCircle className="w-3 h-3" />
            Failed
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-fg-muted">
            {outcome || "Unknown"}
          </span>
        );
    }
  };

  const columns: ColumnDef<UsageQueryRow>[] = [
    {
      key: "query",
      header: "User Query Text",
      render: (r) => (
        <div className="flex flex-col max-w-xs sm:max-w-md">
          <span
            className="font-medium text-slate-800 dark:text-slate-200 line-clamp-2 text-xs"
            title={r.query}
          >
            {r.query}
          </span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500 truncate">
            {r.request_id}
          </span>
        </div>
      ),
    },
    {
      key: "created_at",
      header: "Asked",
      sortable: true,
      render: (r) => <RelativeTime value={r.created_at} className="text-xs text-fg-subtle" />,
    },
    {
      key: "ip_address",
      header: "Client IP",
      render: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onSelectIp?.(r.ip_address);
          }}
          className="font-mono text-xs text-brand-600 dark:text-brand-400 hover:underline text-left"
        >
          {r.ip_address}
        </button>
      ),
    },
    {
      key: "session_title",
      header: "Session",
      render: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onSelectSession?.(r.session_id);
          }}
          className="text-xs text-slate-600 dark:text-slate-400 hover:text-brand-600 dark:hover:text-brand-400 text-left truncate max-w-[140px] block"
          title={r.session_title}
        >
          {r.session_title || r.session_id.slice(0, 8)}
        </button>
      ),
    },
    {
      key: "outcome",
      header: "Status / Outcome",
      align: "center",
      render: (r) => <OutcomeBadge outcome={r.outcome} />,
    },
    {
      key: "prompt_tokens",
      header: "Input Tokens",
      align: "right",
      render: (r) => formatTokens(r.prompt_tokens, "input"),
    },
    {
      key: "reasoning_tokens",
      header: "Reasoning Tokens",
      align: "right",
      render: (r) => formatTokens(r.reasoning_tokens, "reasoning"),
    },
    {
      key: "output_tokens",
      header: "Output Tokens",
      align: "right",
      render: (r) => formatTokens(r.output_tokens, "output"),
    },
    {
      key: "cost",
      header: "Cost (USD)",
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.cost_usd, 4),
    },
    {
      key: "latency",
      header: "Latency",
      sortable: true,
      align: "right",
      render: (r) => formatLatency(r.latency_ms),
    },
    {
      key: "actions",
      header: "Inspect",
      align: "right",
      render: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setInspectRequestId(r.request_id);
          }}
          className="inline-flex items-center gap-1 rounded-md border border-slate-200 dark:border-white/10 bg-white dark:bg-dark-card px-2 py-1 text-[11px] font-medium text-slate-700 dark:text-slate-200 shadow-xs hover:bg-slate-50 dark:hover:bg-white/5 transition"
          aria-label={`Inspect query ${r.request_id}`}
        >
          <Eye className="w-3 h-3 text-brand-600 dark:text-brand-400" />
          <span>Inspect</span>
        </button>
      ),
    },
  ];

  const exportParams = new URLSearchParams({ tab: "queries", audience, q: search });
  if (sessionFilter) exportParams.set("session_id", sessionFilter);
  if (ipFilter) exportParams.set("ip", ipFilter);
  if (fromDay) exportParams.set("from_date", fromDay);
  if (toDay) exportParams.set("to_date", toDay);
  if (filters.outcome !== "all") exportParams.set("outcome", filters.outcome);
  if (filters.grounded !== "all") exportParams.set("grounded", filters.grounded);
  const exportUrl = `/api/proxy/admin/usage/export?${exportParams.toString()}`;

  return (
    <div className="space-y-6">
      {/* ========================================================================= */}
      {/* PRE-TABLE INTELLIGENCE HEADER */}
      {/* ========================================================================= */}
      <section className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" data-tour="usage-queries-stats">
          <StatCard
            title="Total Queries"
            value={stats.total_queries ?? data?.total ?? 0}
            subtitle={`Audience: ${audience}`}
            icon={Sparkles}
          />

          <StatCard
            title="Groundedness Pass Rate"
            value={
              stats.groundedness_pass_rate !== undefined
                ? `${Number(stats.groundedness_pass_rate).toFixed(1)}%`
                : "—"
            }
            subtitle={
              stats.groundedness_evaluated_turns
                ? `${stats.groundedness_passed_turns || 0} / ${stats.groundedness_evaluated_turns} generation turns`
                : "Generation turns only"
            }
            icon={ShieldCheck}
          />

          <StatCard
            title="Avg Turn Latency"
            value={stats.avg_latency_ms ? `${Math.round(stats.avg_latency_ms)} ms` : "—"}
            subtitle="P50 / P95 response time"
            icon={Clock}
          />

          <StatCard
            title="Avg Token Budget"
            value={stats.avg_tokens_per_query ? Math.round(stats.avg_tokens_per_query).toLocaleString() : "—"}
            subtitle="Billed tokens / turn"
            icon={Cpu}
          />
        </div>

        {/* Outcome Filter Pill Bar */}
        <div className="space-y-1.5" data-tour="usage-queries-outcomes">
          <span className="text-xs font-semibold text-fg-muted block">
            Filter by Execution Outcome
          </span>
          <OutcomePillBar
            outcomes={outcomesCount}
            selectedOutcome={filters.outcome}
            onSelectOutcome={(val) => setFilter("outcome", val)}
          />
        </div>
      </section>

      {/* ========================================================================= */}
      {/* TABLE CONTROLS & DATA TABLE (Integrated TableCard) */}
      {/* ========================================================================= */}
      <section data-tour="usage-queries-table">
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search query, answer, session title, IP, or request ID..."
              filters={[
                {
                  id: "grounded",
                  label: "Groundedness",
                  value: filters.grounded,
                  options: [
                    { label: "All Turns", value: "all" },
                    { label: "Verified Grounded", value: "true" },
                    { label: "Ungrounded", value: "false" },
                  ],
                  onChange: (val) => setFilter("grounded", val),
                },
                ...(sessionFilter
                  ? [
                      {
                        id: "session_id",
                        label: `Session: ${sessionFilter.slice(0, 8)}...`,
                        value: sessionFilter,
                        options: [
                          { label: `Session: ${sessionFilter}`, value: sessionFilter },
                          { label: "Clear Session Filter", value: "" },
                        ],
                        onChange: (val: string) => {
                          if (isClear(val)) onSelectSession?.("");
                        },
                      },
                    ]
                  : []),
                ...(ipFilter
                  ? [
                      {
                        id: "ip",
                        label: `IP: ${ipFilter}`,
                        value: ipFilter,
                        options: [
                          { label: `IP: ${ipFilter}`, value: ipFilter },
                          { label: "Clear IP Filter", value: "" },
                        ],
                        onChange: (val: string) => {
                          if (isClear(val)) onSelectIp?.("");
                        },
                      },
                    ]
                  : []),
                ...(fromDay || toDay
                  ? [
                      {
                        id: "days",
                        label: dayRangeLabel(fromDay, toDay),
                        value: "days",
                        options: [
                          { label: dayRangeLabel(fromDay, toDay), value: "days" },
                          { label: "Clear Date Filter", value: "" },
                        ],
                        onChange: (val: string) => {
                          if (isClear(val)) onClearDays?.();
                        },
                      },
                    ]
                  : []),
              ]}
              extraActions={
                <a
                  data-tour="usage-queries-export"
                  href={exportUrl}
                  download
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-dark-card px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 shadow-sm hover:bg-slate-50 dark:hover:bg-white/5 transition"
                >
                  <Download className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
                  <span>Export CSV</span>
                </a>
              }
            />
          }
          pagination={
            <Pagination
              page={page}
              pageCount={Math.ceil((data?.total || 0) / pageSize)}
              totalItems={data?.total || 0}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          }
        >
          <DataTable
            columns={columns}
            data={data?.items || []}
            keyField="request_id"
            isLoading={isLoading}
            error={error}
            onRetry={fetchData}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(r) => setInspectRequestId(r.request_id)}
          />
        </TableCard>
      </section>

      {/* Inspector Slide-Over Drawer */}
      {inspectRequestId && (
        <QueryInspectorDrawer
          requestId={inspectRequestId}
          token={token}
          onClose={() => setInspectRequestId(null)}
        />
      )}
    </div>
  );
}
