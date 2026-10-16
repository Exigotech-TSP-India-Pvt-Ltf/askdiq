"use client";

/**
 * Audit Trail tab (planchanges4 F1.4.3): the auth events from
 * `/governance/audit-log` (sign-ins, denials, approvals, role changes,
 * suspensions, revocations), filtered by event and searchable by actor or
 * target. Only `audit:auth:read` callers receive these rows from the API.
 */
import React, { useMemo, useState } from "react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import type { PageSize } from "@/lib/use-table-state";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";
import {
  AUTH_EVENTS,
  AUTH_EVENT_LABEL,
  isAuthEvent,
  type AuditLogEntry,
  type AuthEvent,
} from "@/lib/user-management-api";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { auditSummary } from "./UserDrawer";

const DEFAULT_PAGE_SIZE: PageSize = 25;

const DANGER: ReadonlySet<string> = new Set<AuthEvent>(["login_denied", "user_suspended", "access_rejected", "session_revoked"]);
const OK: ReadonlySet<string> = new Set<AuthEvent>(["access_approved", "user_reactivated", "user_invited", "login_success"]);

function EventBadge({ action }: { action: string }) {
  const tone = DANGER.has(action)
    ? "bg-status-danger-bg text-status-danger-fg"
    : OK.has(action)
      ? "bg-status-ok-bg text-status-ok-fg"
      : "bg-status-info-bg text-status-info-fg";
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`}>
      {isAuthEvent(action) ? AUTH_EVENT_LABEL[action] : action}
    </span>
  );
}

interface AuthAuditTabProps {
  entries: readonly AuditLogEntry[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  action: AuthEvent | null;
  update: (patch: DeepLinkUpdate) => void;
}

export function AuthAuditTab({ entries, loading, error, onRetry, action, update }: AuthAuditTabProps) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return entries.filter((e) => {
      if (action && e.action !== action) return false;
      if (!q) return true;
      const target = typeof e.detail.target_email === "string" ? e.detail.target_email : "";
      return e.actor.toLowerCase().includes(q) || target.toLowerCase().includes(q);
    });
  }, [entries, action, search]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(page, pageCount);
  const rows = filtered.slice((current - 1) * pageSize, current * pageSize);

  const columns: ColumnDef<AuditLogEntry>[] = [
    {
      key: "created_at",
      header: "When",
      render: (e) => <RelativeTime value={e.created_at} className="text-fg-subtle" />,
    },
    { key: "action", header: "Event", render: (e) => <EventBadge action={e.action} /> },
    { key: "actor", header: "Actor", render: (e) => <span className="text-fg">{e.actor}</span> },
    { key: "detail", header: "Detail", render: (e) => <span className="text-fg-muted">{auditSummary(e) || "—"}</span> },
    { key: "ip_address", header: "IP", render: (e) => <span className="font-mono text-fg-subtle">{e.ip_address ?? "—"}</span> },
    {
      key: "request_id",
      header: "Request",
      render: (e) => <span className="font-mono text-[11px] text-fg-subtle">{e.request_id.slice(0, 12)}</span>,
    },
  ];

  return (
    <TableCard
      title="Auth audit trail"
      description="Who approved whom, role changes, suspensions and denied sign-ins. Newest first."
      toolbar={
        <TableToolbar
          search={search}
          onSearchChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          searchPlaceholder="Search actor or target…"
          filters={[
            {
              id: "action",
              label: "Event",
              value: action ?? "all",
              options: [{ label: "All events", value: "all" }, ...AUTH_EVENTS.map((a) => ({ label: AUTH_EVENT_LABEL[a], value: a }))],
              onChange: (v) => {
                update({ action: v === "all" ? null : v });
                setPage(1);
              },
            },
          ]}
        />
      }
      pagination={
        <Pagination
          page={current}
          pageCount={pageCount}
          totalItems={filtered.length}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
        />
      }
    >
      <DataTable
        columns={columns}
        data={rows}
        keyField="id"
        isLoading={loading}
        error={error}
        onRetry={onRetry}
        emptyMessage="No auth events match"
        emptySubMessage="Sign-ins, approvals and role changes appear here."
      />
    </TableCard>
  );
}
