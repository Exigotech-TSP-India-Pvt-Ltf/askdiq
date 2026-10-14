"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { MessageSquare, ThumbsUp, ThumbsDown, Star } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { useTableState } from "@/lib/use-table-state";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PageEnvelope } from "@/lib/types";

interface FeedbackRow {
  id: string;
  rating?: number;
  comment?: string;
  ip_address?: string;
  conversation_id?: string;
  message_id?: string;
  created_at?: string;
}

function FeedbackContent() {
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
    tableKey: "fb",
    defaultSort: "created_at_desc",
    allowedSorts: ["created_at_desc", "created_at_asc", "rating_desc", "rating_asc"],
    defaultFilters: { rating: "all" },
  });

  const [data, setData] = useState<PageEnvelope<FeedbackRow> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchFeedback = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getFeedbackList(
        token,
        {
          audience,
          q: search || undefined,
          rating: filters.rating !== "all" ? Number(filters.rating) : undefined,
          sort,
          limit: pageSize,
          offset,
        },
        getSignal()
      );
      setData(res);
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setError(err.message || "Failed to load feedback records.");
      }
    } finally {
      setLoading(false);
    }
  }, [token, audience, search, filters.rating, sort, pageSize, offset, getSignal]);

  useEffect(() => {
    fetchFeedback();
  }, [fetchFeedback]);

  const items = data?.items || [];
  const total = data?.total || 0;

  const positiveCount = items.filter((x) => (x.rating || 0) >= 4).length;
  const negativeCount = items.filter((x) => (x.rating || 0) <= 2 && (x.rating || 0) > 0).length;

  const columns: ColumnDef<FeedbackRow>[] = [
    {
      key: "ip_address",
      header: "Client IP",
      render: (f) => (
        <span className="font-mono text-xs font-medium text-slate-800">
          {f.ip_address || "—"}
        </span>
      ),
    },
    {
      key: "rating",
      header: "Rating",
      sortable: true,
      align: "center",
      render: (f) => {
        const rating = f.rating ?? 0;
        return (
          <div className="flex items-center justify-center gap-1">
            <span
              className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${
                rating >= 4
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : rating === 3
                  ? "bg-amber-50 text-amber-700 border border-amber-200"
                  : rating > 0
                  ? "bg-rose-50 text-rose-700 border border-rose-200"
                  : "bg-slate-100 text-slate-500"
              }`}
            >
              <Star className="h-3 w-3 fill-current" />
              <span>{rating > 0 ? rating : "—"}</span>
            </span>
          </div>
        );
      },
    },
    {
      key: "comment",
      header: "User Comment / Sentiment",
      render: (f) => (
        <p className="line-clamp-2 text-xs text-slate-700 max-w-md">
          {f.comment || <span className="italic text-slate-400">No comment provided</span>}
        </p>
      ),
    },
    {
      key: "conversation_id",
      header: "Session Ref",
      render: (f) =>
        f.conversation_id ? (
          <Link
            href={`/admin/conversations/${f.conversation_id}`}
            className="font-mono text-xs text-brand-indigo hover:underline"
            title={`View conversation ${f.conversation_id}`}
          >
            {f.conversation_id.slice(0, 8)}…
          </Link>
        ) : (
          <span className="text-slate-400">—</span>
        ),
    },
    {
      key: "message_id",
      header: "Turn ID",
      render: (f) => (
        <span className="font-mono text-[11px] text-slate-400 truncate max-w-[100px] block" title={f.message_id}>
          {f.message_id ? `${f.message_id.slice(0, 8)}…` : "—"}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "Submitted At",
      sortable: true,
      render: (f) => (
        <span className="text-xs text-slate-500 whitespace-nowrap">
          {f.created_at ? new Date(f.created_at).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-slate-50/50">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="User Feedback"
        subtitle="Audience-scoped ratings and feedback sentiments on response quality"
        icon={ThumbsUp}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Hero Stat Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Total Filtered Feedback</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900">{total.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo border border-indigo-100">
              <MessageSquare className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Positive Feedback (4–5 ★)</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600">{positiveCount.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-emerald-50 p-2.5 text-emerald-600 border border-emerald-100">
              <ThumbsUp className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Needs Improvement (1–2 ★)</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-rose-600">{negativeCount.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-rose-50 p-2.5 text-rose-600 border border-rose-100">
              <ThumbsDown className="h-5 w-5" />
            </div>
          </div>
        </div>

        {/* Integrated TableCard Section */}
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search comment, IP, or IDs…"
              filters={[
                {
                  id: "rating",
                  label: "Rating Filter",
                  value: filters.rating,
                  options: [
                    { label: "All Ratings", value: "all" },
                    { label: "5 Stars (Excellent)", value: "5" },
                    { label: "4 Stars (Good)", value: "4" },
                    { label: "3 Stars (Average)", value: "3" },
                    { label: "2 Stars (Poor)", value: "2" },
                    { label: "1 Star (Critical)", value: "1" },
                  ],
                  onChange: (v) => setFilter("rating", v),
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
            onRetry={fetchFeedback}
            currentSort={sort}
            onSortChange={setSort}
            emptyMessage="No feedback records found matching current audience and filters."
          />
        </TableCard>
      </div>
    </div>
  );
}

export default function FeedbackPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-slate-400">Loading feedback...</div>}>
      <FeedbackContent />
    </React.Suspense>
  );
}