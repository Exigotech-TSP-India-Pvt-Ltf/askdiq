"use client";

import { Fragment, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { parseServerDate } from "@/lib/time";
import type { AuditLogEntry } from "@/lib/types";

const ACTION_FILTERS = ["all", "query", "ingest"] as const;

function formatDetail(detail: Record<string, unknown>): string {
  try {
    return JSON.stringify(detail, null, 2);
  } catch {
    return String(detail);
  }
}

export default function GovernancePage() {
  const { token } = useAuth();
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState<(typeof ACTION_FILTERS)[number]>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setEntries(null);
    api
      .auditLog(token, { action: actionFilter === "all" ? undefined : actionFilter })
      .then(setEntries)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load the audit log.")
      );
  }, [token, actionFilter]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold text-ink">Governance & audit log</h1>
          <p className="text-xs text-ink/50">
            Every query and ingest is recorded with its actor, guardrail decisions, and
            cited sources — the auditable record behind each answer.
          </p>
        </div>

        <div className="flex gap-1 rounded-lg border border-ink/10 p-1">
          {ACTION_FILTERS.map((action) => (
            <button
              key={action}
              onClick={() => setActionFilter(action)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium capitalize transition ${
                actionFilter === action
                  ? "bg-brand-600 text-white"
                  : "text-ink/60 hover:bg-ink/5"
              }`}
            >
              {action}
            </button>
          ))}
        </div>
      </header>

      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {!error && !entries && (
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      )}

      {entries && entries.length === 0 && (
        <p className="text-sm text-ink/50">No audit events recorded yet.</p>
      )}

      {entries && entries.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-ink/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink/[0.03] text-xs uppercase tracking-wide text-ink/40">
              <tr>
                <th className="px-4 py-2.5 font-medium">When</th>
                <th className="px-4 py-2.5 font-medium">Actor</th>
                <th className="px-4 py-2.5 font-medium">Action</th>
                <th className="px-4 py-2.5 font-medium">IP address</th>
                <th className="px-4 py-2.5 font-medium">Request ID</th>
                <th className="px-4 py-2.5 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-ink/5">
              {entries.map((entry) => (
                <Fragment key={entry.id}>
                  <tr>
                    <td className="px-4 py-2.5 text-ink/60">
                      {parseServerDate(entry.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-ink">{entry.actor}</td>
                    <td className="px-4 py-2.5 text-ink/60 capitalize">{entry.action}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink/40">
                      {entry.ip_address ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-ink/40">
                      {entry.request_id}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        onClick={() =>
                          setExpandedId((id) => (id === entry.id ? null : entry.id))
                        }
                        className="text-xs font-medium text-brand-600 hover:text-brand-700"
                      >
                        {expandedId === entry.id ? "Hide" : "Details"}
                      </button>
                    </td>
                  </tr>
                  {expandedId === entry.id && (
                    <tr>
                      <td colSpan={6} className="bg-ink/[0.02] px-4 py-3">
                        <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-ink/70">
                          {formatDetail(entry.detail)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
