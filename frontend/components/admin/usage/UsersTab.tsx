"use client";

import React, { useEffect, useState } from "react";
import type { PageEnvelope, UsageUserRow } from "@/lib/types";
import { api } from "@/lib/api";
import { useTableState } from "@/lib/use-table-state";
import { DataTable, type ColumnDef } from "../DataTable";
import { TableToolbar } from "../TableToolbar";
import { Pagination } from "../Pagination";
import { TableCard } from "../TableCard";
import { StatCard, HighlightCard } from "../insights";
import { Users, Shield, Cpu, Download, Sparkles } from "lucide-react";

import {
  formatNumber,
  formatCurrency,
  formatTokens,
  TableBadge,
} from "../TableFormatters";

interface UsersTabProps {
  token: string;
  onSelectUserIp?: (ip: string) => void;
}

export function UsersTab({ token, onSelectUserIp }: UsersTabProps) {
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
    tableKey: "users",
    defaultSort: "cost_desc",
    allowedSorts: [
      "cost_desc",
      "cost_asc",
      "queries_desc",
      "queries_asc",
      "tokens_desc",
      "tokens_asc",
      "sessions_desc",
      "sessions_asc",
    ],
    defaultFilters: { type: "all" },
  });

  const [data, setData] = useState<PageEnvelope<UsageUserRow> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.getUsageUsers(
        token,
        {
          audience,
          q: search || undefined,
          type: filters.type !== "all" ? filters.type : undefined,
          sort,
          limit: pageSize,
          offset,
        },
        getSignal()
      );
      setData(res);
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setError(err.message || "Failed to load user analytics.");
      }
    } finally {
      setIsLoading(false);
    }
  }, [token, audience, search, filters.type, sort, pageSize, offset, getSignal]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const stats = data?.stats || {};
  const topUsers = (stats.top_3_users || []) as Array<{
    ip_address: string;
    queries: number;
    cost_usd: number;
    tokens: number;
  }>;

  const columns: ColumnDef<UsageUserRow>[] = [
    {
      key: "ip_address",
      header: "Client IP Address",
      render: (r) => (
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium text-slate-800 dark:text-slate-200">
            {r.ip_address}
          </span>
          <TableBadge
            label={r.type}
            variant={r.is_test ? "warning" : "success"}
            title={r.is_test ? "Internal developer/test IP" : "Production user traffic"}
          />
        </div>
      ),
    },
    {
      key: "sessions",
      header: "Sessions",
      sortable: true,
      align: "right",
      render: (r) => formatNumber(r.sessions),
    },
    {
      key: "queries",
      header: "Queries",
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
  ];

  const exportUrl = `/api/proxy/admin/usage/export?tab=users&audience=${audience}&q=${encodeURIComponent(
    search
  )}`;

  return (
    <div className="space-y-6">
      {/* ========================================================================= */}
      {/* PRE-TABLE INTELLIGENCE HEADER */}
      {/* ========================================================================= */}
      <section className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Total Unique Users"
            value={stats.unique_users ?? data?.total ?? 0}
            subtitle={`Audience: ${audience}`}
            icon={Users}
          />

          <StatCard
            title="Active Sessions"
            value={stats.active_sessions ?? 0}
            subtitle="Engaged conversational threads"
            icon={Cpu}
          />

          <StatCard
            title="Total Queries"
            value={stats.total_queries ?? 0}
            subtitle="Turns processed"
            icon={Sparkles}
          />

          <StatCard
            title="Top Consumer Share"
            value={`${stats.top_consumer_share_pct ?? 0}%`}
            subtitle="Tokens consumed by top 3 users"
            icon={Shield}
          />
        </div>

        {/* Top 3 Users Highlight Cards */}
        {topUsers.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold text-fg-muted">
              Top Consumers by Financial Cost (1-Click Drilldown)
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {topUsers.map((u, i) => (
                <HighlightCard
                  key={u.ip_address}
                  rank={i + 1}
                  title={u.ip_address}
                  subtitle={`${u.queries.toLocaleString()} queries`}
                  metricLabel="Total Cost"
                  metricValue={`$${u.cost_usd.toFixed(4)}`}
                  secondaryMetric={`${u.tokens.toLocaleString()} tokens`}
                  onClick={() => {
                    setSearchInput(u.ip_address);
                    onSelectUserIp?.(u.ip_address);
                  }}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ========================================================================= */}
      {/* TABLE CONTROLS & DATA TABLE (Integrated TableCard) */}
      {/* ========================================================================= */}
      <section>
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search IP address..."
              filters={[
                {
                  id: "type",
                  label: "User Type",
                  value: filters.type,
                  options: [
                    { label: "All Users", value: "all" },
                    { label: "Production Only", value: "production" },
                    { label: "Test Users Only", value: "test" },
                  ],
                  onChange: (val) => setFilter("type", val),
                },
              ]}
              extraActions={
                <a
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
            keyField="ip_address"
            isLoading={isLoading}
            error={error}
            onRetry={fetchData}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(r) => {
              setSearchInput(r.ip_address);
              onSelectUserIp?.(r.ip_address);
            }}
          />
        </TableCard>
      </section>
    </div>
  );
}
