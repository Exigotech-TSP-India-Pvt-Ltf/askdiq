"use client";

/**
 * Audit Trail tab (planchanges4 F1.4.3): the auth events (sign-ins, denials,
 * approvals, role changes, suspensions, revocations) from
 * `GET /admin/auth-audit`, one keyset page at a time: the event filter and
 * the actor / target search run on the server, the next page is prefetched,
 * and the previous page stays on screen while a new one loads. Only
 * `audit:auth:read` callers receive these rows from the API.
 */
import React, { useState } from "react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { useApiQuery, usePrefetchNextPage } from "@/lib/query-cache";
import { useCursorStack } from "@/lib/use-cursor-stack";
import type { PageSize } from "@/lib/use-table-state";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";
import {
  AUDIT_PAGE_DEFAULT,
  AUTH_EVENTS,
  AUTH_EVENT_LABEL,
  isAuthEvent,
  umKeys,
  userManagementApi,
  type AuditLogEntry,
  type AuthAuditParams,
  type AuthEvent,
} from "@/lib/user-management-api";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { auditSummary } from "./UserDrawer";

const DEFAULT_PAGE_SIZE: PageSize = AUDIT_PAGE_DEFAULT;
const SEARCH_DEBOUNCE_MS = 250;

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
  action: AuthEvent | null;
  update: (patch: DeepLinkUpdate) => void;
}

function pageParams(pageSize: number, action: AuthEvent | null, search: string, cursor: string | null): AuthAuditParams {
  const q = search.trim();
  return { limit: pageSize, ...(action ? { action } : {}), ...(q ? { search: q } : {}), ...(cursor ? { cursor } : {}) };
}

export function AuthAuditTab({ action, update }: AuthAuditTabProps) {
  const [search, setSearch] = useState("");
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  const pager = useCursorStack(`${action ?? ""}|${search.trim()}|${pageSize}`);

  const params = pageParams(pageSize, action, search, pager.cursor);
  const query = useApiQuery(umKeys.auditPage(params), (signal) => userManagementApi.authAuditPage(params, signal), {
    keepPreviousData: true,
  });
  const rows = query.data?.items ?? [];
  const nextCursor = query.isPlaceholderData ? null : query.data?.next_cursor ?? null;
  const nextParams = pageParams(pageSize, action, search, nextCursor);
  usePrefetchNextPage(nextCursor ? umKeys.auditPage(nextParams) : null, (signal) =>
    userManagementApi.authAuditPage(nextParams, signal),
  );
  const error = query.error && !query.data ? (query.error as Error).message || "Failed to load." : null;

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
    <div data-tour="user-management-audit-table">
    <TableCard
      title="Auth audit trail"
      description="Who approved whom, role changes, suspensions and denied sign-ins. Newest first."
      toolbar={
        <TableToolbar
          search={search}
          onSearchChange={setSearch}
          debounceMs={SEARCH_DEBOUNCE_MS}
          searchPlaceholder="Search actor or target…"
          filters={[
            {
              id: "action",
              label: "Event",
              value: action ?? "all",
              options: [{ label: "All events", value: "all" }, ...AUTH_EVENTS.map((a) => ({ label: AUTH_EVENT_LABEL[a], value: a }))],
              onChange: (v) => update({ action: v === "all" ? null : v }),
            },
          ]}
        />
      }
      cursor={{
        hasPrev: pager.hasPrev,
        hasNext: Boolean(nextCursor),
        onPrev: pager.prev,
        onNext: () => pager.next(nextCursor),
        onFirst: pager.reset,
        page: pager.page,
        totalItems: query.data?.total,
        pageSize,
        onPageSizeChange: (size) => setPageSize(size as PageSize),
        pageItemCount: rows.length,
        itemLabel: "events",
        isLoading: query.isValidating,
      }}
    >
      <DataTable
        columns={columns}
        data={rows}
        keyField="id"
        isLoading={query.isLoading}
        error={error}
        onRetry={() => void query.refetch()}
        emptyMessage="No auth events match"
        emptySubMessage="Sign-ins, approvals and role changes appear here."
      />
    </TableCard>
    </div>
  );
}
