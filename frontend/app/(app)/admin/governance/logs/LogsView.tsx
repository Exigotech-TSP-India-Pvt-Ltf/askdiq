"use client";

import { Fragment, useMemo, useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

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
  CRITICAL: "bg-red-100 text-red-800",
  ALERT: "bg-orange-50 text-orange-700",
  ERROR: "bg-red-50 text-red-700",
  WARN: "bg-amber-50 text-amber-700",
  WARNING: "bg-amber-50 text-amber-700",
  INFO: "bg-sky-50 text-sky-700",
  DEBUG: "bg-slate-100 text-slate-600",
};

// Always offered as filter choices, regardless of what's currently in the
// data, so Critical/Alert/Error/Warning can be selected even when there
// happen to be zero matching entries right now.
const KNOWN_LEVELS = ["CRITICAL", "ALERT", "ERROR", "WARNING", "INFO"];

function levelClass(level?: string) {
  return LEVEL_STYLES[(level || "INFO").toUpperCase()] ?? "bg-slate-100 text-slate-600";
}

function shortId(id?: string, head = 8) {
  if (!id) return "—";
  return id.length > head ? `${id.slice(0, head)}…` : id;
}

/** Governance log messages are often JSON blobs (e.g. query events); parse
 * them into a short, scannable summary instead of dumping raw JSON inline. */
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
    // Not JSON — fall through to plain text.
  }
  return { text: message, badges: [] };
}

export default function LogsView({ hideHeader = false }: { hideHeader?: boolean } = {}) {
  const [logs, setLogs] = useState<Log[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [levelFilter, setLevelFilter] = useState<string>("all");
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 15;

  const { token } = useAuth();

  useEffect(() => {
    if (!token) return;
    const load = (showSpinner: boolean) => {
      if (showSpinner) setLoading(true);
      api
        .adminGet<{ total: number; items: Log[] }>(token, "/governance/logs?limit=2000")
        .then((data) => {
          setLogs(data.items);
          setTotal(data.total);
          setLastUpdated(new Date());
        })
        .catch(() => setLogs([]))
        .finally(() => setLoading(false));
    };
    load(true);
    const timer = setInterval(() => load(false), 10000);
    return () => clearInterval(timer);
  }, [token]);

  const levels = useMemo(() => {
    const present = new Set(logs.map((l) => (l.level || "INFO").toUpperCase()));
    // Always offer the full known severity list first (in fixed, most- to
    // least-severe order), then any extra/unexpected level found in the
    // data that isn't one of them.
    const extras = Array.from(present).filter((lvl) => !KNOWN_LEVELS.includes(lvl));
    return ["all", ...KNOWN_LEVELS, ...extras];
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

  useEffect(() => {
    setPage(1);
    setExpandedIndex(null);
  }, [search, levelFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div>
      {!hideHeader && (
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-8">
        <div>
          <h1 className="text-sm font-semibold text-slate-900">Governance Logs</h1>
          <p className="text-xs text-slate-500">
            Searchable application and security activity.
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
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="p-4 pb-0">
          <TableToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search message, service or user…"
            filterOptions={levels.map((lvl) => ({
              label: lvl === "all" ? "All" : lvl,
              value: lvl,
            }))}
            filterValue={levelFilter}
            onFilterChange={setLevelFilter}
            countLabel={`${total} entries`}
          />
        </div>

        <div className="max-h-[60vh] overflow-y-auto overflow-x-auto">
          <table className="w-full text-left">
            <thead className="sticky top-0 z-10">
              <tr>
                {["Timestamp", "Level", "Service", "Summary", "IP", "Request ID", ""].map((x) => (
                  <th
                    key={x}
                    className="border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs font-semibold uppercase text-slate-500"
                  >
                    {x}
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {loading && (
                <tr>
                  <td colSpan={8} className="px-5 py-8 text-center text-sm text-slate-400">
                    Loading logs…
                  </td>
                </tr>
              )}

              {!loading && paged.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-5 py-8 text-center text-sm text-slate-400">
                    No log entries match this filter.
                  </td>
                </tr>
              )}

              {!loading &&
                paged.map((log, index) => {
                  const { text, badges } = summarize(log.message);
                  const isExpanded = expandedIndex === index;
                  return (
                    <Fragment key={index}>
                      <tr
                        onClick={() => setExpandedIndex(isExpanded ? null : index)}
                        className={`cursor-pointer border-b border-slate-100 align-top transition-colors odd:bg-white even:bg-slate-50/40 hover:bg-purple-50/50 ${
                          isExpanded ? "bg-purple-50/60" : ""
                        }`}
                      >
                        <td className="whitespace-nowrap px-5 py-4 text-xs text-slate-500">
                          {log.created_at ? new Date(log.created_at).toLocaleString() : "—"}
                        </td>
                        <td className="px-5 py-4">
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${levelClass(
                              log.level
                            )}`}
                          >
                            {(log.level || "INFO").toUpperCase()}
                          </span>
                        </td>
                        <td className="px-5 py-4 text-sm capitalize text-slate-600">
                          {log.service || "API"}
                        </td>
                        <td className="max-w-sm px-5 py-4">
                          <p className="truncate text-sm text-slate-800" title={text}>
                            {text}
                          </p>
                          {badges.length > 0 && (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              {badges.map((b) => (
                                <span
                                  key={b}
                                  className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600"
                                >
                                  {b}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-500">
                          {log.ip_address || "—"}
                        </td>
                        <td
                          className="px-5 py-4 font-mono text-xs text-slate-500"
                          title={log.request_id}
                        >
                          {shortId(log.request_id)}
                        </td>
                        <td className="px-5 py-4 text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedIndex(isExpanded ? null : index);
                            }}
                            className="text-xs font-medium text-purple-600 hover:text-purple-700"
                          >
                            {isExpanded ? "Hide" : "Details"}
                          </button>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="border-b border-slate-100 bg-slate-50/60">
                          <td colSpan={8} className="px-5 py-4">
                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-white p-3 text-[11px] text-slate-600 ring-1 ring-slate-200">
                              {(() => {
                                try {
                                  return JSON.stringify(JSON.parse(log.message ?? ""), null, 2);
                                } catch {
                                  return log.message || "—";
                                }
                              })()}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
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
