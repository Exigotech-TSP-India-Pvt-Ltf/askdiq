"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { UserCheck, Users as UsersIcon, Activity } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
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
          <span className="font-mono font-medium text-slate-800">{u.ip_address}</span>
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold ${
              u.is_test
                ? "bg-amber-100 text-amber-800"
                : "bg-emerald-100 text-emerald-800"
            }`}
          >
            {u.type || (u.is_test ? "Test IP" : "Production")}
          </span>
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
          <span
            className={`inline-flex items-center gap-1.5 text-xs font-medium ${
              isActive ? "text-emerald-700" : "text-slate-400"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isActive ? "bg-emerald-500 animate-pulse" : "bg-slate-300"
              }`}
            />
            {isActive ? "Active" : "Inactive"}
          </span>
        );
      },
    },
    {
      key: "sessions",
      header: "Sessions",
      sortable: true,
      align: "center",
      render: (u) => (
        <span className="font-mono text-xs font-medium text-slate-700">
          {(u.sessions ?? u.conversations ?? 0).toLocaleString()}
        </span>
      ),
    },
    {
      key: "queries",
      header: "Queries",
      sortable: true,
      align: "center",
      render: (u) => (
        <span className="font-mono text-xs font-semibold text-slate-900">
          {(u.queries ?? 0).toLocaleString()}
        </span>
      ),
    },
    {
      key: "feedback_count",
      header: "Feedback",
      align: "center",
      render: (u) => (
        <span className="font-mono text-xs text-slate-500">
          {(u.feedback_count ?? 0).toLocaleString()}
        </span>
      ),
    },
    {
      key: "first_seen",
      header: "First Seen",
      render: (u) => (
        <span className="text-xs text-slate-500 whitespace-nowrap">
          {u.first_seen ? new Date(u.first_seen).toLocaleDateString() : "—"}
        </span>
      ),
    },
    {
      key: "last_active",
      header: "Last Active",
      sortable: true,
      render: (u) => (
        <span className="text-xs text-slate-500 whitespace-nowrap">
          {u.last_active ? new Date(u.last_active).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-slate-50/50">
      {/* Standard Universal Top Bar Line */}
      <DashboardHeader
        title="Users"
        subtitle="Audience-scoped telemetry by client IP address, activity history, and engagement"
        icon={UsersIcon}
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Hero Stat Cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Total Filtered Users</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900">{total.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo border border-indigo-100">
              <UsersIcon className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Active Page Users</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600">{activeCount.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-emerald-50 p-2.5 text-emerald-600 border border-emerald-100">
              <UserCheck className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-sm flex items-center justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Page Queries Driven</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900">{totalQueries.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-purple-50 p-2.5 text-purple-600 border border-purple-100">
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
