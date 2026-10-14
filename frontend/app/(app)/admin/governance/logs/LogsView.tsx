"use client";

import React, { useMemo, useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableCard } from "@/components/admin/TableCard";
import { ChevronDown, ChevronRight, Activity, Terminal } from "lucide-react";

type Log = {
  created_at?: string;
  level?: string;
  service?: string;
  message?: string;
  user_id?: string;
  ip_address?: string;
  request_id?: string;
  trace_id?: string;
};

const LEVEL_STYLES: Record<string, string> = {
  CRITICAL: "bg-red-100 text-red-800 border-red-200",
  ALERT: "bg-orange-50 text-orange-700 border-orange-200",
  ERROR: "bg-red-50 text-red-700 border-red-200",
  WARN: "bg-amber-50 text-amber-700 border-amber-200",
  WARNING: "bg-amber-50 text-amber-700 border-amber-200",
  INFO: "bg-sky-50 text-sky-700 border-sky-200",
  DEBUG: "bg-slate-100 text-slate-600 border-slate-200",
};

const KNOWN_LEVELS = ["CRITICAL", "ALERT", "ERROR", "WARNING", "INFO"];

function levelClass(level?: string) {
  return LEVEL_STYLES[(level || "INFO").toUpperCase()] ?? "bg-slate-100 text-slate-600";
}

function shortId(id?: string, head = 8) {
  if (!id) return "—";
  return id.length > head ? `${id.slice(0, head)}…` : id;
}

function summarize(message?: string): { text: string; badges: string[] } {
  if (!message) return { text: "—", badges: [] };
  try {
    const parsed = JSON.parse(message);
    if (parsed && typeof parsed === "object") {
      const badges: string[] = [];
      let text = "";
      if (typeof parsed.query === "string") text = `Query: "${parsed.query}"`;
      else if (typeof parsed.action === "string") text = parsed.action;
      else text = Object.keys(parsed)[0] ? `${Object.keys(parsed)[0]} event` : "Event";

      if (typeof parsed.grounded === "boolean") {
        badges.push(parsed.grounded ? "Grounded" : "Not grounded");
      }
      if (Array.isArray(parsed.source_ids)) {
        badges.push(`${parsed.source_ids.length} source${parsed.source_ids.length === 1 ? "" : "s"}`);
      }
      if (Array.isArray(parsed.guardrail_flags) && parsed.guardrail_flags.length > 0) {
        badges.push(`${parsed.guardrail_flags.length} flag${parsed.guardrail_flags.length === 1 ? "" : "s"}`);
      }
      return { text, badges };
    }
  } catch {
    // Plain text
  }
  return { text: message, badges: [] };
}

export default function LogsView({ hideHeader = false }: { hideHeader?: boolean } = {}) {
  const [logs, setLogs] = useState<Log[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<string>("all");
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const { token } = useAuth();
  const { audience } = useAudience();

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    const qs = audience && audience !== "overview" ? `&audience=${audience}` : "";
    api
      .adminGet<{ total: number; items: Log[] }>(token, `/governance/logs?limit=2000${qs}`)
      .then((data) => {
        setLogs(data.items);
        setLastUpdated(new Date());
      })
      .catch(() => setLogs([]))
      .finally(() => setLoading(false));
  }, [token, audience]);

  const levels = useMemo(() => {
    const fromData = Array.from(new Set(logs.map((l) => (l.level || "INFO").toUpperCase())));
    return ["all", ...Array.from(new Set([...KNOWN_LEVELS, ...fromData]))];
  }, [logs]);

  const filtered = useMemo(
    () =>
      logs.filter((log) => {
        if (levelFilter !== "all" && (log.level || "INFO").toUpperCase() !== levelFilter) {
          return false;
        }
        if (!search.trim()) return true;
        const haystack = `${log.message ?? ""} ${log.service ?? ""} ${log.ip_address ?? ""}`.toLowerCase();
        return haystack.includes(search.trim().toLowerCase());
      }),
    [logs, levelFilter, search]
  );

  const paged = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  const columns: ColumnDef<Log>[] = [
    {
      key: "created_at",
      header: "Timestamp",
      render: (log) => (
        <span className="whitespace-nowrap text-xs text-slate-500 font-mono">
          {log.created_at ? new Date(log.created_at).toLocaleTimeString() : "—"}
        </span>
      ),
    },
    {
      key: "level",
      header: "Level",
      align: "center",
      render: (log) => (
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold border ${levelClass(
            log.level
          )}`}
        >
          {log.level || "INFO"}
        </span>
      ),
    },
    {
      key: "service",
      header: "Service",
      render: (log) => (
        <span className="text-xs font-mono font-medium text-slate-700">
          {log.service || "app"}
        </span>
      ),
    },
    {
      key: "message",
      header: "Event Summary & Traces",
      render: (log) => {
        const { text, badges } = summarize(log.message);
        return (
          <div className="flex flex-col gap-1 max-w-md">
            <span className="text-xs text-slate-800 line-clamp-1">{text}</span>
            {badges.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {badges.map((b, i) => (
                  <span
                    key={i}
                    className="rounded bg-indigo-50 border border-indigo-100 px-1.5 py-0.2 text-[10px] font-medium text-indigo-700"
                  >
                    {b}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      },
    },
    {
      key: "ip_address",
      header: "Client IP",
      render: (log) => (
        <span className="font-mono text-xs text-slate-600">
          {log.ip_address || "—"}
        </span>
      ),
    },
    {
      key: "request_id",
      header: "Request ID",
      render: (log) => (
        <span className="font-mono text-[10px] text-slate-400" title={log.request_id}>
          {shortId(log.request_id)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      {!hideHeader && (
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-8">
          <div>
            <h1 className="text-sm font-semibold text-slate-900">Governance Logs</h1>
            <p className="text-xs text-slate-500">
              Audit trails, system operations, and security events.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            {lastUpdated ? `Live · updated ${lastUpdated.toLocaleTimeString()}` : "Connecting…"}
          </div>
        </header>
      )}

      <div className="p-8">
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={(v) => {
                setSearch(v);
                setPage(1);
              }}
              searchPlaceholder="Search message, service, or client IP…"
              filters={[
                {
                  id: "level",
                  label: "Log Level",
                  value: levelFilter,
                  options: levels.map((lvl) => ({
                    label: lvl === "all" ? "All Levels" : lvl,
                    value: lvl,
                  })),
                  onChange: (v) => {
                    setLevelFilter(v);
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
            emptyMessage="No log entries match the selected filters."
          />
        </TableCard>
      </div>
    </div>
  );
}
