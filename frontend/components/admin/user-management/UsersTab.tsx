"use client";

/**
 * Users tab (planchanges4 F1.4.2): TableCard with search, one Filters popover
 * (role, status) and pagination. Filters, sort, page and the open drawer are
 * URL state, so drill-downs from the KPI cards and charts land here.
 */
import React, { useMemo, useState } from "react";
import { UserPlus } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { focusRing, primaryButton } from "@/components/ui/styles";
import { useApiQuery, usePrefetchNextPage } from "@/lib/query-cache";
import type { PageSize } from "@/lib/use-table-state";
import {
  ROLES,
  ROLE_LABEL,
  STATUS_LABEL,
  USER_STATUSES,
  USER_SORT_KEYS,
  umKeys,
  userManagementApi,
  type AuditLogEntry,
  type UserAccount,
  type UserListParams,
  type UserSortKey,
} from "@/lib/user-management-api";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";
import { RoleBadge, StatusBadge, formatDateTime } from "./parts";
import { InviteUserDialog } from "./InviteUserDialog";
import { UserDrawer } from "./UserDrawer";
import { DEFAULT_SORT, type UmUrlState } from "./use-um-url";

const DEFAULT_PAGE_SIZE: PageSize = 25;

interface UsersTabProps {
  url: UmUrlState;
  update: (patch: DeepLinkUpdate) => void;
  meId: string | undefined;
  activeSuperadmins: number;
  audit: readonly AuditLogEntry[];
}

export function UsersTab({ url, update, meId, activeSuperadmins, audit }: UsersTabProps) {
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  const [inviting, setInviting] = useState(false);

  const params: UserListParams = {
    search: url.q || undefined,
    role: url.role ?? undefined,
    status: url.status ?? undefined,
    sort: url.sort,
    order: url.order,
    limit: pageSize,
    offset: (url.page - 1) * pageSize,
  };
  const listKey = umKeys.users(params);
  const query = useApiQuery(listKey, (signal) => userManagementApi.listUsers(params, signal));
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  const nextParams = { ...params, offset: params.offset + pageSize };
  usePrefetchNextPage(url.page < pageCount ? umKeys.users(nextParams) : null, (signal) =>
    userManagementApi.listUsers(nextParams, signal),
  );

  const openIndex = url.user ? items.findIndex((u) => u.id === url.user) : -1;
  const openRow = openIndex >= 0 ? items[openIndex] : undefined;
  const openUser = (u: UserAccount) => update({ user: u.id });

  const columns: ColumnDef<UserAccount>[] = [
    {
      key: "name",
      header: "Name",
      sortable: true,
      render: (u) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            openUser(u);
          }}
          className={`rounded text-left font-medium text-fg hover:text-brand-700 dark:hover:text-brand-300 ${focusRing}`}
        >
          {u.name || "—"}
          {u.id === meId ? <span className="ml-1.5 text-[11px] font-normal text-fg-subtle">(you)</span> : null}
        </button>
      ),
    },
    { key: "email", header: "Email", sortable: true, render: (u) => <span className="text-fg-muted">{u.email}</span> },
    { key: "role", header: "Role", render: (u) => <RoleBadge role={u.role} /> },
    { key: "status", header: "Status", render: (u) => <StatusBadge status={u.status} /> },
    {
      key: "last_login_at",
      header: "Last login",
      sortable: true,
      render: (u) => <span className="whitespace-nowrap text-fg-subtle">{formatDateTime(u.last_login_at)}</span>,
    },
    {
      key: "approved_by_email",
      header: "Approved by",
      render: (u) => <span className="text-fg-subtle">{u.approved_by_email ?? "—"}</span>,
    },
    {
      key: "created_at",
      header: "Created",
      sortable: true,
      render: (u) => <span className="whitespace-nowrap text-fg-subtle">{formatDateTime(u.created_at)}</span>,
    },
  ];

  const onSortChange = (value: string) => {
    const order = value.endsWith("_asc") ? "asc" : "desc";
    const key = value.replace(/_(asc|desc)$/, "");
    if (!(USER_SORT_KEYS as readonly string[]).includes(key)) return;
    update({ sort: key === DEFAULT_SORT ? null : (key as UserSortKey), order: order === "desc" ? null : order, page: null });
  };

  return (
    <>
      <TableCard
        title="Users"
        description="Everyone who has signed in or been invited"
        badge={
          <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted tabular-nums">
            {total.toLocaleString()}
          </span>
        }
        headerActions={
          <button type="button" className={primaryButton} onClick={() => setInviting(true)}>
            <UserPlus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Invite user
          </button>
        }
        toolbar={
          <TableToolbar
            search={url.q}
            onSearchChange={(value) => update({ q: value.trim() || null, page: null })}
            debounceMs={300}
            searchPlaceholder="Search name or email…"
            filters={[
              {
                id: "role",
                label: "Role",
                value: url.role ?? "all",
                options: [{ label: "All roles", value: "all" }, ...ROLES.map((r) => ({ label: ROLE_LABEL[r], value: r }))],
                onChange: (v) => update({ role: v === "all" ? null : v, page: null }),
              },
              {
                id: "status",
                label: "Status",
                value: url.status ?? "all",
                options: [
                  { label: "All statuses", value: "all" },
                  ...USER_STATUSES.map((s) => ({ label: STATUS_LABEL[s], value: s })),
                ],
                onChange: (v) => update({ status: v === "all" ? null : v, page: null }),
              },
            ]}
          />
        }
        pagination={
          <Pagination
            page={url.page}
            pageCount={pageCount}
            totalItems={total}
            pageSize={pageSize}
            onPageChange={(p) => update({ page: p > 1 ? p : null })}
            onPageSizeChange={(size) => {
              setPageSize(size);
              update({ page: null });
            }}
          />
        }
      >
        <DataTable
          columns={columns}
          data={items}
          keyField="id"
          isLoading={query.isLoading || (query.isValidating && !query.data)}
          error={query.error && !query.data ? (query.error as Error).message || "Failed to load users." : null}
          onRetry={() => void query.refetch()}
          currentSort={`${url.sort}_${url.order}`}
          onSortChange={onSortChange}
          onRowClick={openUser}
          emptyMessage="No users match these filters"
          emptySubMessage="Clear the search or filters, or invite someone."
        />
      </TableCard>

      <UserDrawer
        userId={url.user}
        row={openRow}
        listKey={openRow ? listKey : null}
        meId={meId}
        activeSuperadmins={activeSuperadmins}
        audit={audit}
        onClose={() => update({ user: null })}
        onPrev={openIndex > 0 ? () => openUser(items[openIndex - 1]) : undefined}
        onNext={openIndex >= 0 && openIndex < items.length - 1 ? () => openUser(items[openIndex + 1]) : undefined}
      />
      <InviteUserDialog open={inviting} onClose={() => setInviting(false)} />
    </>
  );
}
