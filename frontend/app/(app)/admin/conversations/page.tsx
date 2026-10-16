"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { MessagesSquare, Users, MessageCircle } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber } from "@/components/admin/TableFormatters";
import { useTableState } from "@/lib/use-table-state";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PageEnvelope } from "@/lib/types";

interface ConversationRow {
  id: string;
  title?: string;
  created_at?: string;
  last_activity_at?: string;
  message_count?: number;
  created_ip?: string | null;
}

function ConversationsContent() {
  const router = useRouter();
  const { token } = useAuth();

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
    tableKey: "convs",
    defaultSort: "created_at_desc",
    allowedSorts: [
      "created_at_desc",
      "created_at_asc",
      "last_activity_at_desc",
      "last_activity_at_asc",
      "message_count_desc",
      "message_count_asc",
    ],
    defaultFilters: { ip: "" },
  });

  const [data, setData] = useState<PageEnvelope<ConversationRow> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchConversations = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getConversationsList(
        token,
        {
          audience,
          q: search || undefined,
          ip: filters.ip || undefined,
          sort,
          limit: pageSize,
          offset,
        },
        getSignal()
      );
      setData(res);
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setError(err.message || "Failed to load conversation logs.");
      }
    } finally {
      setLoading(false);
    }
  }, [token, audience, search, filters.ip, sort, pageSize, offset, getSignal]);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const items = data?.items || [];
  const total = data?.total || 0;

  const totalMessages = items.reduce((sum, c) => sum + (c.message_count ?? 0), 0);
  const uniqueIpsList = Array.from(new Set(items.map((c) => c.created_ip).filter(Boolean) as string[]));
  const uniqueIps = uniqueIpsList.length;

  const columns: ColumnDef<ConversationRow>[] = [
    {
      key: "title",
      header: "Conversation Title & ID",
      render: (c) => (
        <div className="flex flex-col gap-0.5">
          <span className="font-semibold text-slate-800 dark:text-slate-200">{c.title || "Untitled Session"}</span>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500">{c.id}</span>
        </div>
      ),
    },
    {
      key: "created_ip",
      header: "Origin IP",
      render: (c) => (
        <span className="font-mono text-xs text-slate-600 dark:text-slate-300">
          {c.created_ip || "—"}
        </span>
      ),
    },
    {
      key: "message_count",
      header: "Messages",
      sortable: true,
      align: "right",
      render: (c) => formatNumber(c.message_count ?? 0),
    },
    {
      key: "created_at",
      header: "Created",
      sortable: true,
      render: (c) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {c.created_at ? new Date(c.created_at).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      key: "last_activity_at",
      header: "Last Active",
      sortable: true,
      render: (c) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {c.last_activity_at ? new Date(c.last_activity_at).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Conversations"
        subtitle="Audience-scoped conversational sessions, message density, and turn timeline"
        icon={MessagesSquare}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Hero Stat Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Total Filtered Sessions</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{total.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-800">
              <MessagesSquare className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Unique Page Client IPs</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600 dark:text-emerald-400">{uniqueIps.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/60 p-2.5 text-emerald-600 dark:text-emerald-400 border border-emerald-100 dark:border-emerald-800">
              <Users className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Page Messages Processed</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{totalMessages.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-teal-50 dark:bg-teal-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-teal-100 dark:border-teal-800">
              <MessageCircle className="h-5 w-5" />
            </div>
          </div>
        </div>

        {/* Integrated TableCard Section */}
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search title, ID, or created IP…"
              filters={[
                {
                  id: "ip",
                  label: "Origin IP",
                  value: filters.ip,
                  options: [
                    { label: "All Origin IPs", value: "" },
                    ...uniqueIpsList.map((ip) => ({ label: ip, value: ip })),
                  ],
                  onChange: (v) => setFilter("ip", v),
                },
              ]}
            />
          }
          pagination={
            <Pagination
              page={page}
              pageCount={Math.ceil(total / pageSize)}
              totalItems={total}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          }
        >
          <DataTable
            columns={columns}
            data={items}
            keyField="id"
            isLoading={loading}
            error={error}
            onRetry={fetchConversations}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(c) => router.push(`/admin/conversations/${c.id}`)}
            emptyMessage="No conversations found matching current audience and filters."
          />
        </TableCard>
      </div>
    </div>
  );
}

export default function ConversationsPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-slate-400">Loading conversations...</div>}>
      <ConversationsContent />
    </React.Suspense>
  );
}
