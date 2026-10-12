"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatLatencyMs } from "@/lib/format";
import { Activity, Gauge, Timer, AlertTriangle } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

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

function HeroStat({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  accent: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded-xl border border-purple-100 bg-white p-5 shadow-sm transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-4 -top-4 h-20 w-20 rounded-full opacity-10 transition-transform group-hover:scale-110 ${accent}`}
      />
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <div className={`rounded-lg p-2 ${accent} bg-opacity-10`}>
          <Icon className={`h-4 w-4 ${accent.replace("bg-", "text-")}`} />
        </div>
      </div>
      <p className="mt-3 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function shortId(id?: string, head = 10) {
  if (!id) return "—";
  return id.length > head ? `${id.slice(0, head)}…` : id;
}

function statusBadge(status?: string) {
  const s = (status || "ok").toLowerCase();
  if (s === "ok" || s === "success") return "bg-emerald-50 text-emerald-700";
  if (s === "error" || s === "failed") return "bg-red-50 text-red-700";
  return "bg-amber-50 text-amber-700";
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
  const pageSize = 10;

  const { token } = useAuth();

  useEffect(() => {
    if (!token) return;
    api
      .adminGet<Trace[]>(token, "/governance/traces")
      .then(setTraces)
      .catch(() => setTraces([]))
      .finally(() => setLoading(false));
  }, [token]);

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
          const isOk = (t.status || "ok").toLowerCase() === "ok";
          if (statusFilter === "ok" && !isOk) return false;
          if (statusFilter === "error" && isOk) return false;
        }
        if (!search.trim()) return true;
        const haystack = `${t.request_id} ${t.trace_id ?? ""} ${t.ip_address ?? ""}`.toLowerCase();
        return haystack.includes(search.trim().toLowerCase());
      }),
    [traces, search, statusFilter]
  );

  useEffect(() => setPage(1), [search, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div>
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

      <div className="p-8">
        <div className="mb-6 grid grid-cols-1 gap-5 sm:grid-cols-3">
          <HeroStat label="Total Traces" value={traces.length} icon={Activity} accent="bg-purple-500" />
          <HeroStat label="Average Latency" value={formatLatencyMs(avgLatency)} icon={Gauge} accent="bg-indigo-500" />
          <HeroStat label="Errors" value={errorCount} icon={AlertTriangle} accent="bg-rose-500" />
        </div>

        <div className="overflow-hidden rounded-xl border border-purple-100 bg-white shadow-sm">
          <div className="p-4 pb-0">
            <TableToolbar
              search={search}
              onSearchChange={setSearch}
              searchPlaceholder="Search by request ID or IP…"
              filterOptions={[
                { label: "All", value: "all" },
                { label: "OK", value: "ok" },
                { label: "Error", value: "error" },
              ]}
              filterValue={statusFilter}
              onFilterChange={setStatusFilter}
              countLabel={`${filtered.length} traces`}
            />
          </div>

          <div className="max-h-[60vh] overflow-y-auto overflow-x-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 z-10">
                <tr>
                  {["Request ID", "Trace ID", "IP", "Status", "Latency", "Timestamp"].map((x) => (
                    <th
                      key={x}
                      className="border-b border-purple-100 bg-purple-50 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500"
                    >
                      {x}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-10 text-center text-sm text-slate-500">
                      Loading traces...
                    </td>
                  </tr>
                ) : paged.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-10 text-center text-sm text-slate-500">
                      No traces found.
                    </td>
                  </tr>
                ) : (
                  paged.map((trace) => (
                    <tr
                      key={trace.request_id}
                      className="border-b border-purple-50 odd:bg-white even:bg-purple-50/20 transition-colors hover:bg-purple-50/60"
                    >
                      <td className="px-5 py-4 font-mono text-xs text-slate-600" title={trace.request_id}>
                        {shortId(trace.request_id)}
                      </td>
                      <td className="px-5 py-4 font-mono text-xs text-slate-600" title={trace.trace_id}>
                        {shortId(trace.trace_id)}
                      </td>
                      <td className="px-5 py-4 text-sm text-slate-600">{trace.ip_address || "—"}</td>
                      <td className="px-5 py-4">
                        <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${statusBadge(trace.status)}`}>
                          {trace.status || "ok"}
                        </span>
                      </td>
                      <td className="px-5 py-4">
                        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${latencyBadge(trace.total_ms)}`}>
                          <Timer className="h-3 w-3" />
                          {trace.total_ms != null ? formatLatencyMs(trace.total_ms) : "—"}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-sm text-slate-600">
                        {trace.timestamp ? new Date(trace.timestamp).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <Pagination
            page={page}
            pageCount={pageCount}
            onPageChange={setPage}
            totalItems={filtered.length}
            pageSize={pageSize}
          />
        </div>
      </div>
    </div>
  );
}
