"use client";

import React, { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { Activity, Gauge, Timer, AlertTriangle } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableCard } from "@/components/admin/TableCard";

type Trace = {
  request_id: string;
  trace_id?: string;
  user_id?: string;
  conversation_id?: string;
  ip_address?: string;
  timestamp?: string;
  status?: string;
  total_ms?: number;
};

function shortId(id?: string, head = 10) {
  if (!id) return "—";
  return id.length > head ? `${id.slice(0, head)}…` : id;
}

function statusBadge(status?: string) {
  const s = (status || "ok").toLowerCase();
  if (s === "ok" || s === "success") return "bg-emerald-50 text-emerald-700 border border-emerald-200";
  if (s === "error" || s === "failed") return "bg-red-50 text-red-700 border border-red-200";
  return "bg-amber-50 text-amber-700 border border-amber-200";
}

function latencyBadge(ms?: number) {
  if (ms == null) return "bg-slate-100 text-slate-500";
  if (ms >= 8000) return "bg-red-50 text-red-700";
  if (ms >= 4000) return "bg-amber-50 text-amber-700";
  return "bg-emerald-50 text-emerald-700";
}

export default function TracesView({ hideHeader = false }: { hideHeader?: boolean } = {}) {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const { token } = useAuth();
  const { audience } = useAudience();

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    api
      .adminGet<Trace[]>(token, `/governance/traces${qs}`)
      .then(setTraces)
      .catch(() => setTraces([]))
      .finally(() => setLoading(false));
  }, [token, audience]);

  const avgLatency = useMemo(() => {
    const vals = traces.map((t) => t.total_ms).filter((v): v is number => v != null);
    return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : 0;
  }, [traces]);

  const errorCount = useMemo(
    () => traces.filter((t) => (t.status || "ok").toLowerCase() !== "ok").length,
    [traces]
  );

  const filtered = useMemo(
    () =>
      traces.filter((t) => {
        if (statusFilter !== "all") {
          const s = (t.status || "ok").toLowerCase();
          if (statusFilter === "ok" && s !== "ok" && s !== "success") return false;
          if (statusFilter === "error" && s !== "error" && s !== "failed") return false;
        }
        if (!search.trim()) return true;
        const needle = search.trim().toLowerCase();
        return (
          t.request_id.toLowerCase().includes(needle) ||
          (t.trace_id ?? "").toLowerCase().includes(needle) ||
          (t.ip_address ?? "").toLowerCase().includes(needle)
        );
      }),
    [traces, statusFilter, search]
  );

  const paged = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  const columns: ColumnDef<Trace>[] = [
    {
      key: "request_id",
      header: "Request ID",
      render: (t) => (
        <span className="font-mono text-xs text-slate-900" title={t.request_id}>
          {shortId(t.request_id, 12)}
        </span>
      ),
    },
    {
      key: "trace_id",
      header: "Trace ID",
      render: (t) => (
        <span className="font-mono text-xs text-slate-500" title={t.trace_id}>
          {shortId(t.trace_id, 12)}
        </span>
      ),
    },
    {
      key: "ip_address",
      header: "Client IP",
      render: (t) => (
        <span className="font-mono text-xs text-slate-600">
          {t.ip_address || "—"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      align: "center",
      render: (t) => (
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadge(
            t.status
          )}`}
        >
          {t.status || "ok"}
        </span>
      ),
    },
    {
      key: "total_ms",
      header: "Pipeline Latency",
      sortable: true,
      align: "right",
      render: (t) => (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${latencyBadge(
            t.total_ms
          )}`}
        >
          <Timer className="h-3 w-3" />
          {t.total_ms != null ? formatLatencyMs(t.total_ms) : "—"}
        </span>
      ),
    },
    {
      key: "timestamp",
      header: "Timestamp",
      sortable: true,
      render: (t) => (
        <span className="text-xs text-slate-500 whitespace-nowrap">
          {t.timestamp ? new Date(t.timestamp).toLocaleString() : "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {!hideHeader && (
        <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-8">
          <div>
            <h1 className="text-sm font-semibold text-slate-900">Request Traces</h1>
            <p className="text-xs text-slate-500">
              Complete request journey across the RAG pipeline.
            </p>
          </div>
        </header>
      )}

      <div className="p-8 space-y-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500">Total Pipeline Traces</p>
              <p className="mt-1 text-2xl font-bold text-slate-900">{traces.length.toLocaleString()}</p>
            </div>
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo">
              <Activity className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500">Average Turn Latency</p>
              <p className="mt-1 text-2xl font-bold text-slate-900">{formatLatencyMs(avgLatency)}</p>
            </div>
            <div className="rounded-lg bg-emerald-50 p-2.5 text-emerald-600">
              <Gauge className="h-5 w-5" />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-500">Pipeline Errors</p>
              <p className="mt-1 text-2xl font-bold text-rose-600">{errorCount}</p>
            </div>
            <div className="rounded-lg bg-rose-50 p-2.5 text-rose-600">
              <AlertTriangle className="h-5 w-5" />
            </div>
          </div>
        </div>

        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={(v) => {
                setSearch(v);
                setPage(1);
              }}
              searchPlaceholder="Search request ID, trace ID, or IP…"
              filters={[
                {
                  id: "status",
                  label: "Trace Status",
                  value: statusFilter,
                  options: [
                    { label: "All Traces", value: "all" },
                    { label: "Successful Only", value: "ok" },
                    { label: "Errors Only", value: "error" },
                  ],
                  onChange: (v) => {
                    setStatusFilter(v);
                    setPage(1);
                  },
                },
              ]}
            />
          }
          pagination={
            <Pagination
              page={page}
              pageCount={Math.ceil(filtered.length / pageSize)}
              totalItems={filtered.length}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          }
        >
          <DataTable
            columns={columns}
            data={paged}
            keyField="request_id"
            isLoading={loading}
            emptyMessage="No request traces found matching the selected filters."
          />
        </TableCard>
      </div>
    </div>
  );
}
