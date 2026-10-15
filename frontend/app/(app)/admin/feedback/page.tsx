"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  MessageSquare,
  ThumbsUp,
  ThumbsDown,
  Star,
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  Loader2,
} from "lucide-react";
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

interface FeedbackAnalytics {
  total_feedback: number;
  positive_count: number;
  negative_count: number;
  neutral_count: number;
  positive_ratio: number;
  negative_ratio: number;
  feedback_rate: number;
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
  const [analytics, setAnalytics] = useState<FeedbackAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [promotedMap, setPromotedMap] = useState<Record<string, string>>({});

  const fetchFeedback = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [listRes, analyticsRes] = await Promise.all([
        api.getFeedbackList(
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
        ),
        api.getFeedbackAnalytics(token, audience, getSignal()).catch(() => null),
      ]);
      setData(listRes);
      if (analyticsRes) {
        setAnalytics(analyticsRes);
      }
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

  const handlePromoteToTest = async (messageId: string) => {
    if (!token || promotingId) return;
    setPromotingId(messageId);
    try {
      const res = await api.promoteFeedbackToTest(token, messageId);
      setPromotedMap((prev) => ({ ...prev, [messageId]: res.case_id }));
    } catch (err: any) {
      alert(err.message || "Failed to promote feedback to test case.");
    } finally {
      setPromotingId(null);
    }
  };

  const items = data?.items || [];
  const total = data?.total || 0;

  const positiveRatio = analytics?.positive_ratio ?? 0;
  const negativeRatio = analytics?.negative_ratio ?? 0;
  const feedbackCoverage = analytics?.feedback_rate ?? 0;
  const isSpikeAlert = negativeRatio > 25.0 && (analytics?.total_feedback ?? 0) >= 5;

  const columns: ColumnDef<FeedbackRow>[] = [
    {
      key: "ip_address",
      header: "Client IP",
      render: (f) => (
        <span className="font-mono text-xs font-medium text-slate-800 dark:text-slate-200">
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
                  ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800"
                  : rating === 3
                  ? "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800"
                  : rating > 0
                  ? "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800"
                  : "bg-slate-100 dark:bg-white/10 text-slate-500 dark:text-slate-400"
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
        <p className="line-clamp-2 text-xs text-slate-700 dark:text-slate-300 max-w-md">
          {f.comment || <span className="italic text-slate-400 dark:text-slate-500">No comment provided</span>}
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
            className="font-mono text-xs text-brand-600 dark:text-brand-400 hover:underline"
            title={`View conversation ${f.conversation_id}`}
          >
            {f.conversation_id.slice(0, 8)}…
          </Link>
        ) : (
          <span className="text-slate-400 dark:text-slate-600">—</span>
        ),
    },
    {
      key: "message_id",
      header: "Turn ID",
      render: (f) => (
        <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500 truncate max-w-[100px] block" title={f.message_id}>
          {f.message_id ? `${f.message_id.slice(0, 8)}…` : "—"}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "Submitted At",
      sortable: true,
      render: (f) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {f.created_at ? new Date(f.created_at).toLocaleString() : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "RLHF Action",
      align: "right",
      render: (f) => {
        if (!f.message_id) return null;
        const caseId = promotedMap[f.message_id];
        const isPromoting = promotingId === f.message_id;

        if (caseId) {
          return (
            <span
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800/60 px-2 py-0.5 rounded-md"
              title={`Promoted as ${caseId}`}
            >
              <CheckCircle2 className="h-3 w-3" />
              Promoted
            </span>
          );
        }

        return (
          <button
            type="button"
            disabled={isPromoting}
            onClick={() => handlePromoteToTest(f.message_id!)}
            title="Convert this turn into a permanent regression golden test case"
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium border border-ink/10 dark:border-white/10 bg-white dark:bg-dark-surface text-slate-700 dark:text-slate-200 hover:border-brand-500/40 hover:bg-brand-50/50 dark:hover:bg-brand-950/50 dark:hover:text-brand-300 transition shadow-sm disabled:opacity-50"
          >
            {isPromoting ? (
              <Loader2 className="h-3 w-3 animate-spin text-brand-600" />
            ) : (
              <FlaskConical className="h-3 w-3 text-brand-600 dark:text-brand-400" />
            )}
            <span>Promote Test</span>
          </button>
        );
      },
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="User Feedback & RLHF"
        subtitle="Audience-scoped satisfaction sentiments, negative spike alerts, and continuous eval promotion"
        icon={ThumbsUp}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Negative Feedback Spike Alert Banner */}
        {isSpikeAlert && (
          <div className="rounded-xl border border-rose-300/80 bg-rose-50/90 dark:bg-rose-950/40 dark:border-rose-900/60 p-4 shadow-sm flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-rose-600 dark:text-rose-400 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-semibold text-rose-900 dark:text-rose-200">
                Negative Feedback Anomaly Detected ({negativeRatio}% &gt; 25% threshold)
              </h2>
              <p className="text-xs text-rose-700 dark:text-rose-300 mt-0.5">
                A rolling spike in critical or low-star user ratings has been flagged and recorded to audit logs. Review low-rated exchanges below and promote failing turns into regression test cases.
              </p>
            </div>
          </div>
        )}

        {/* Hero Stat Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-200/90 dark:border-white/10 bg-white dark:bg-dark-surface p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Total Feedback</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{total.toLocaleString()}</p>
              <p className="mt-0.5 text-[11px] text-slate-400">Coverage: {feedbackCoverage}% of queries</p>
            </div>
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-900/60">
              <MessageSquare className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-white/10 bg-white dark:bg-dark-surface p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Positive Satisfaction</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600 dark:text-emerald-400">{positiveRatio}%</p>
              <p className="mt-0.5 text-[11px] text-slate-400">{analytics?.positive_count ?? 0} ratings (4–5 ★)</p>
            </div>
            <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/60 p-2.5 text-emerald-600 dark:text-emerald-400 border border-emerald-100 dark:border-emerald-900/60">
              <ThumbsUp className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-white/10 bg-white dark:bg-dark-surface p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Needs Improvement</p>
              <p className={`mt-1 text-2xl font-bold tracking-tight ${isSpikeAlert ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400"}`}>
                {negativeRatio}%
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">{analytics?.negative_count ?? 0} ratings (1–2 ★)</p>
            </div>
            <div className={`rounded-lg p-2.5 border ${isSpikeAlert ? "bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-400 border-rose-100 dark:border-rose-900/60" : "bg-amber-50 dark:bg-amber-950/60 text-amber-600 dark:text-amber-400 border-amber-100 dark:border-amber-900/60"}`}>
              <ThumbsDown className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-white/10 bg-white dark:bg-dark-surface p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Closed-Loop RLHF</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-brand-600 dark:text-brand-400">
                {Object.keys(promotedMap).length} Active
              </p>
              <p className="mt-0.5 text-[11px] text-slate-400">Promoted into golden test suite</p>
            </div>
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-900/60">
              <FlaskConical className="h-5 w-5" />
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