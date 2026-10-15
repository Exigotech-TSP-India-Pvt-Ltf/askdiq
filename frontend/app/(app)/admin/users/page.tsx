"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { UserCheck, Users as UsersIcon, Activity } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber, TableBadge } from "@/components/admin/TableFormatters";
import { useTableState } from "@/lib/use-table-state";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { PageEnvelope } from "@/lib/types";

interface UserRow {
  id: string;
  ip_address: string;
  is_test?: boolean;
  type?: string;
  first_seen?: string | null;
  last_active?: string | null;
  status?: string;
  sessions?: number;
  conversations?: number;
  queries?: number;
  feedback_count?: number;
}

function UsersContent() {
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
    tableKey: "users",
    defaultSort: "last_active_desc",
    allowedSorts: [
      "last_active_desc",
      "last_active_asc",
      "queries_desc",
      "queries_asc",
      "sessions_desc",
      "sessions_asc",
    ],
    defaultFilters: { status: "all", type: "all" },
  });

  const [data, setData] = useState<PageEnvelope<UserRow> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getUsersList(
        token,
        {
          audience,
          q: search || undefined,
          status: filters.status !== "all" ? filters.status : undefined,
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
        setError(err.message || "Failed to load users list.");
      }
    } finally {
      setLoading(false);
    }
  }, [token, audience, search, filters.status, filters.type, sort, pageSize, offset, getSignal]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const items = data?.items || [];
  const total = data?.total || 0;

  const activeCount = items.filter(
    (u) =>
      u.status?.toLowerCase() === "active" ||
      (u.last_active &&
        Date.now() - new Date(u.last_active).getTime() < 30 * 60 * 1000)
  ).length;

  const totalQueries = items.reduce((acc, u) => acc + (u.queries ?? 0), 0);

  const columns: ColumnDef<UserRow>[] = [
    {
      key: "ip_address",
      header: "Client IP Address",
      render: (u) => (
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium text-slate-800 dark:text-slate-200">
            {u.ip_address}
          </span>
          <TableBadge
            label={u.type || (u.is_test ? "Test" : "Production")}
            variant={u.is_test ? "warning" : "success"}
          />
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      align: "center",
      render: (u) => {
        const isActive =
          u.status?.toLowerCase() === "active" ||
          (u.last_active &&
            Date.now() - new Date(u.last_active).getTime() < 30 * 60 * 1000);
        return (
          <TableBadge
            label={isActive ? "Active" : "Inactive"}
            variant={isActive ? "success" : "neutral"}
          />
        );
      },
    },
    {
      key: "sessions",
      header: "Sessions",
      sortable: true,
      align: "right",
      render: (u) => formatNumber(u.sessions ?? u.conversations ?? 0),
    },
    {
      key: "queries",
      header: "Queries",
      sortable: true,
      align: "right",
      render: (u) => formatNumber(u.queries ?? 0),
    },
    {
      key: "feedback_count",
      header: "Feedback",
      align: "right",
      render: (u) => formatNumber(u.feedback_count ?? 0),
    },
    {
      key: "first_seen",
      header: "First Seen",
      render: (u) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {u.first_seen ? new Date(u.first_seen).toLocaleDateString() : "—"}
        </span>
      ),
    },
    {
      key: "last_active",
      header: "Last Active",
      sortable: true,
      render: (u) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {u.last_active ? new Date(u.last_active).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-ink dark:text-slate-100">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Users"
        subtitle="Audience-scoped telemetry by client IP address, activity history, and engagement"
        icon={UsersIcon}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Hero Stat Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Total Filtered Users</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{total.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-800">
              <UsersIcon className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Active Page Users</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600 dark:text-emerald-400">{activeCount.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/60 p-2.5 text-emerald-600 dark:text-emerald-400 border border-emerald-100 dark:border-emerald-800">
              <UserCheck className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Page Queries Driven</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{totalQueries.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-teal-50 dark:bg-teal-950/60 p-2.5 text-brand-600 dark:text-brand-400 border border-teal-100 dark:border-teal-800">
              <Activity className="h-5 w-5" />
            </div>
          </div>
        </div>

        {/* Integrated TableCard Section */}
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={setSearchInput}
              searchPlaceholder="Search client IP address…"
              filters={[
                {
                  id: "status",
                  label: "Status",
                  value: filters.status,
                  options: [
                    { label: "All Statuses", value: "all" },
                    { label: "Active Only", value: "active" },
                    { label: "Inactive Only", value: "inactive" },
                  ],
                  onChange: (v) => setFilter("status", v),
                },
                {
                  id: "type",
                  label: "User Type",
                  value: filters.type,
                  options: [
                    { label: "All Users", value: "all" },
                    { label: "Production Users", value: "production" },
                    { label: "Test Users", value: "test" },
                  ],
                  onChange: (v) => setFilter("type", v),
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
            keyField="ip_address"
            isLoading={loading}
            error={error}
            onRetry={fetchUsers}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(u) => router.push(`/admin/users/${encodeURIComponent(u.ip_address)}`)}
            emptyMessage="No users found matching current audience and filters."
          />
        </TableCard>
      </div>
    </div>
  );
}

export default function UsersPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-slate-400">Loading users...</div>}>
      <UsersContent />
    </React.Suspense>
  );
}
