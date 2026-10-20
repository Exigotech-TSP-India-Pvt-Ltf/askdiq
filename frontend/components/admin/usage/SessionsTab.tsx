"use client";

import React, { useEffect, useRef } from "react";
import { queryErrorMessage, useUsageList, usePrefetchNextUsagePage } from "@/lib/use-usage-data";
import type { PageEnvelope, UsageSessionRow } from "@/lib/types";
import { useTableState } from "@/lib/use-table-state";
import { DataTable, type ColumnDef } from "../DataTable";
import { TableToolbar } from "../TableToolbar";
import { Pagination } from "../Pagination";
import { TableCard } from "../TableCard";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { StatCard, HighlightCard } from "../insights";
import {
  MessageSquare,
  Clock,
  CircleDollarSign,
  Layers,
  Download,
  ArrowRight,
} from "lucide-react";
import {
  formatNumber,
  formatCurrency,
  formatTokens,
} from "../TableFormatters";

interface SessionsTabProps {
  token: string;
  initialIp?: string;
  onSelectSession?: (sessionId: string) => void;
  /** Sets the IP filter; "" clears it. */
  onSelectIp?: (ip: string) => void;
  /** Global date range, inclusive UTC days (YYYY-MM-DD); "" = all time. */
  fromDay?: string;
  toDay?: string;
}

export function SessionsTab({
  token,
  initialIp,
  onSelectSession,
  onSelectIp,
  fromDay = "",
  toDay = "",
}: SessionsTabProps) {
  const {
    page,
    pageSize,
    offset,
    sort,
    search,
    audience,
    setPage,
    setPageSize,
    setSort,
    setSearchInput,
  } = useTableState({
    tableKey: "sessions",
    defaultSort: "cost_desc",
    allowedSorts: [
      "cost_desc",
      "cost_asc",
      "queries_desc",
      "queries_asc",
      "tokens_desc",
      "tokens_asc",
      "last_active_desc",
      "last_active_asc",
    ],
  });
  // The IP filter is owned by the page URL (prop) so it can be cleared here.
  const ipFilter = initialIp ?? "";


  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    setPage(1);
  }, [ipFilter, fromDay, toDay, setPage]);

  // Cached per parameter set (app-wide query cache): tab switches, paging
  // back and revisits render at once; the previous page stays on screen
  // while a new one loads.
  const listParams = {
      audience,
      q: search || undefined,
      ip: ipFilter || undefined,
      from_date: fromDay || undefined,
      to_date: toDay || undefined,
      sort,
      limit: pageSize,
      offset,
    };
  const query = useUsageList<PageEnvelope<UsageSessionRow>>("sessions", token, listParams);
  usePrefetchNextUsagePage("sessions", token, listParams, query.data?.total);
  const data = query.data ?? null;
  const isLoading = query.isLoading;
  const error = queryErrorMessage(query.error, "Failed to load session analytics.");
  const fetchData = query.refetch;

  const stats = data?.stats || {};
  const topSessions = (stats.top_sessions || []) as Array<{
    id: string;
    title: string;
    ip_address: string;
    queries: number;
    cost_usd: number;
    tokens: number;
  }>;

  const columns: ColumnDef<UsageSessionRow>[] = [
    {
      key: "title",
      header: "Session Title & ID",
      sortable: false,
      render: (r) => (
        <div className="flex flex-col">
          <span className="font-medium text-slate-800 dark:text-slate-200 line-clamp-1" title={r.title}>
            {r.title || "Untitled Session"}
          </span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500">
            {r.id}
          </span>
        </div>
      ),
    },
    {
      key: "ip_address",
      header: "Client IP",
      sortable: false,
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
      key: "queries",
      header: "Turns",
      sortable: true,
      align: "right",
      render: (r) => formatNumber(r.queries),
    },
    {
      key: "embed_tokens",
      header: "Embed Tokens",
      align: "right",
      render: (r) => formatTokens(r.embed_tokens, "muted"),
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
      key: "tokens",
      header: "Total Tokens",
      sortable: true,
      align: "right",
      render: (r) => formatTokens(r.total_tokens, "total"),
    },
    {
      key: "cost",
      header: "Cost (USD)",
      sortable: true,
      align: "right",
      render: (r) => formatCurrency(r.cost_usd, 4),
    },
    {
      key: "last_active",
      header: "Last Active",
      sortable: true,
      render: (r) => <RelativeTime value={r.last_active} className="text-xs text-fg-subtle" />,
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
            onSelectSession?.(r.id);
          }}
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 transition"
        >
          <span>Queries</span>
          <ArrowRight className="w-3 h-3" />
        </button>
      ),
    },
  ];

  const exportUrl = `/api/proxy/admin/usage/export?tab=sessions&audience=${audience}&q=${encodeURIComponent(
    search
  )}${ipFilter ? `&ip=${encodeURIComponent(ipFilter)}` : ""}${fromDay ? `&from_date=${fromDay}` : ""}${toDay ? `&to_date=${toDay}` : ""}`;

  return (
    <div className="space-y-6">
      {/* ========================================================================= */}
      {/* PRE-TABLE INTELLIGENCE HEADER */}
      {/* ========================================================================= */}
      <section className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" data-tour="usage-sessions-stats">
          <StatCard
            title="Total Sessions"
            value={stats.total_sessions ?? data?.total ?? 0}
            subtitle={`Audience: ${audience}`}
            icon={MessageSquare}
          />

          <StatCard
            title="Avg Turns / Session"
            value={stats.avg_turns_per_session ? Number(stats.avg_turns_per_session).toFixed(1) : "—"}
            subtitle="Conversation depth"
            icon={Layers}
          />

          <StatCard
            title="Avg Cost / Session"
            value={stats.avg_cost_per_session ? `$${Number(stats.avg_cost_per_session).toFixed(4)}` : "—"}
            subtitle="Blended execution cost"
            icon={CircleDollarSign}
          />

          <StatCard
            title="Max Depth (Turns)"
            value={stats.max_depth ?? 0}
            subtitle="Deepest conversation thread"
            icon={Clock}
          />
        </div>

        {/* Top 3 High-Spend Sessions Highlight Cards */}
        {topSessions.length > 0 && (
          <div className="space-y-2" data-tour="usage-sessions-top">
            <h4 className="text-xs font-semibold text-fg-muted">
              Top Sessions by Financial Cost (1-Click Drilldown)
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {topSessions.map((s, i) => (
                <HighlightCard
                  key={s.id}
                  rank={i + 1}
                  title={s.title || "Untitled Session"}
                  subtitle={`Client IP: ${s.ip_address}`}
                  metricLabel="Total Cost"
                  metricValue={`$${s.cost_usd.toFixed(4)}`}
                  secondaryMetric={`${s.queries.toLocaleString()} turns • ${Number(s.tokens ?? 0).toLocaleString()} tokens`}
                  onClick={() => onSelectSession?.(s.id)}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ========================================================================= */}
      {/* TABLE CONTROLS & DATA TABLE (Integrated TableCard) */}
      {/* ========================================================================= */}
      <section data-tour="usage-sessions-table">
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search session title, ID, or IP..."
              filters={
                ipFilter
                  ? [
                      {
                        id: "ip",
                        label: `Filtered IP: ${ipFilter}`,
                        value: ipFilter,
                        options: [
                          { label: `IP: ${ipFilter}`, value: ipFilter },
                          { label: "Clear IP Filter", value: "" },
                        ],
                        // Pills / Reset send "all", the option sends "".
                        onChange: (val) => {
                          if (val === "" || val === "all") onSelectIp?.("");
                        },
                      },
                    ]
                  : []
              }
              extraActions={
                <a
                  data-tour="usage-sessions-export"
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
            keyField="id"
            isLoading={isLoading}
            error={error}
            onRetry={fetchData}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(r) => onSelectSession?.(r.id)}
          />
        </TableCard>
      </section>
    </div>
  );
}
