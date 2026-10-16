"use client";

import React, { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  FlaskConical,
  ListTree,
  Repeat,
  Settings as SettingsIcon,
  UserCheck,
  UserPlus,
  Users as UsersIcon,
} from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber, TableBadge } from "@/components/admin/TableFormatters";
import { StatCard } from "@/components/admin/insights/StatCard";
import { TestIpManager } from "@/components/admin/usage/TestIpManager";
import { ChartCard } from "@/components/charts/ChartCard";
import { CalendarHeatmap, type HeatCell } from "@/components/charts/CalendarHeatmap";
import { StackedBar } from "@/components/charts/StackedBar";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, iconButton, secondaryButton } from "@/components/ui/styles";
import { FilterChip, errorMessage, formatDay, useRangeParams } from "@/components/admin/entity/shared";
import { useTableState } from "@/lib/use-table-state";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { useDeepLink } from "@/lib/use-deep-link";
import { entityHref } from "@/lib/entity-links";
import { heatFilterLabel, useCarryHref, visitorDetailHref } from "@/lib/entity-routes";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useCan } from "@/lib/use-can";
import { invalidate, useApiMutation, useApiQuery } from "@/lib/query-cache";
import type { PageEnvelope } from "@/lib/types";
import {
  HOURS_PER_DAY,
  VISITOR_SORTS,
  WEEKDAYS,
  getVisitorStats,
  getVisitors,
  intParam,
  type VisitorRow,
} from "@/lib/entity-stats-api";

const ACTIVE_WINDOW_MS = 30 * 60 * 1000;
const HOUR_LABELS = Array.from({ length: HOURS_PER_DAY }, (_, h) => String(h).padStart(2, "0"));
const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;

function isActive(u: VisitorRow): boolean {
  return (
    u.status?.toLowerCase() === "active" ||
    Boolean(u.last_active && Date.now() - new Date(u.last_active).getTime() < ACTIVE_WINDOW_MS)
  );
}

function VisitorsContent() {
  const router = useRouter();
  const carry = useCarryHref();
  const { token } = useAuth();
  const toast = useToast();
  const canWriteTestIp = useCan("testip:write");
  const { get, set } = useDeepLink();
  const range = useRangeParams();
  const [configOpen, setConfigOpen] = useState(false);

  const weekday = intParam(get("weekday"), WEEKDAYS.length - 1);
  const hour = intParam(get("hour"), HOURS_PER_DAY - 1);
  const heat = useMemo(() => (weekday !== null && hour !== null ? { weekday, hour } : null), [weekday, hour]);

  const { page, pageSize, offset, sort, search, filters, audience, setPage, setPageSize, setSort, setSearchInput, setFilter } =
    useTableState({
      tableKey: "users",
      defaultSort: "last_active_desc",
      allowedSorts: [...VISITOR_SORTS],
      defaultFilters: { status: "all", type: "all" },
    });

  const listKey = useMemo(
    () => [
      "visitors",
      "list",
      audience,
      search,
      filters.status,
      filters.type,
      sort,
      pageSize,
      offset,
      heat?.weekday,
      heat?.hour,
      heat ? range.from : null,
      heat ? range.to : null,
    ],
    [audience, search, filters.status, filters.type, sort, pageSize, offset, heat, range.from, range.to],
  );

  const list = useApiQuery(token ? listKey : null, (signal) =>
    getVisitors(
      token ?? "",
      {
        audience,
        q: search || undefined,
        status: filters.status,
        type: filters.type,
        sort,
        limit: pageSize,
        offset,
        ...(heat ? { weekday: heat.weekday, hour: heat.hour, from: range.from, to: range.to } : {}),
      },
      signal,
    ),
  );

  const stats = useApiQuery(token ? ["visitors", "stats", audience, range.from, range.to] : null, (signal) =>
    getVisitorStats(token ?? "", { audience, from: range.from, to: range.to }, signal),
  );

  const markTestIp = useApiMutation<VisitorRow, unknown, PageEnvelope<VisitorRow>>({
    mutationFn: (row) => api.addTestIp(token ?? "", row.ip_address, "Marked from Visitors"),
    optimistic: {
      key: listKey,
      update: (current, row) =>
        current
          ? {
              ...current,
              items: current.items.map((u) => (u.ip_address === row.ip_address ? { ...u, is_test: true, type: "Test" } : u)),
            }
          : { items: [], total: 0, limit: pageSize, offset },
    },
    onError: (err, row) => {
      toast.show({
        title: `Couldn't mark ${row.ip_address} as a test IP`,
        description: errorMessage(err, "Please try again.") ?? undefined,
        tone: "error",
      });
    },
  });

  const { undo } = markTestIp;
  const undoMark = useCallback(
    async (ip: string) => {
      undo();
      try {
        await api.deleteTestIp(token ?? "", ip);
        toast.show({ title: `${ip} counts as a production visitor again`, tone: "info" });
      } catch (err) {
        toast.show({ title: "Undo failed", description: errorMessage(err, "Please try again.") ?? undefined, tone: "error" });
      } finally {
        invalidate("visitors/");
      }
    },
    [undo, toast, token],
  );

  const { mutate } = markTestIp;
  const onMarkTestIp = useCallback(
    async (row: VisitorRow) => {
      const result = await mutate(row);
      if (result === undefined) return;
      invalidate("visitors/stats");
      toast.show({
        title: `${row.ip_address} marked as a test IP`,
        description: "Its traffic now counts under Testing.",
        tone: "success",
        onUndo: () => void undoMark(row.ip_address),
      });
    },
    [mutate, toast, undoMark],
  );

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const statsData = stats.data ?? null;
  const statsAbsent = !stats.isLoading && !stats.error && statsData === null;
  const statsError = errorMessage(stats.error, "Couldn't load visitor stats.");

  const heatData = useMemo<HeatCell[]>(
    () =>
      (statsData?.heatmap ?? [])
        .filter((c) => c.weekday >= 0 && c.weekday < WEEKDAYS.length && c.hour >= 0 && c.hour < HOURS_PER_DAY)
        .map((c) => ({ row: WEEKDAYS[c.weekday], col: HOUR_LABELS[c.hour], value: c.sessions })),
    [statsData],
  );

  const selectHeatCell = useCallback(
    (cell: HeatCell) => {
      setPage(1);
      set({ weekday: WEEKDAYS.indexOf(cell.row as (typeof WEEKDAYS)[number]), hour: Number(cell.col) });
    },
    [set, setPage],
  );

  const activeCount = items.filter(isActive).length;
  const totalQueries = items.reduce((acc, u) => acc + (u.queries ?? 0), 0);

  const columns: ColumnDef<VisitorRow>[] = [
    {
      key: "ip_address",
      header: "Client IP Address",
      render: (u) => (
        <div className="flex items-center gap-2">
          <Link
            href={carry(visitorDetailHref(u.id))}
            onClick={(e) => e.stopPropagation()}
            className={`rounded font-mono text-xs font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            {u.ip_address}
          </Link>
          <TableBadge label={u.type || (u.is_test ? "Test" : "Production")} variant={u.is_test ? "warning" : "success"} />
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      align: "center",
      render: (u) => <TableBadge label={isActive(u) ? "Active" : "Inactive"} variant={isActive(u) ? "success" : "neutral"} />,
    },
    {
      key: "sessions",
      header: "Sessions",
      sortable: true,
      align: "right",
      render: (u) => formatNumber(u.sessions ?? u.conversations ?? 0),
    },
    { key: "queries", header: "Queries", sortable: true, align: "right", render: (u) => formatNumber(u.queries ?? 0) },
    { key: "feedback_count", header: "Feedback", align: "right", render: (u) => formatNumber(u.feedback_count ?? 0) },
    {
      key: "first_seen",
      header: "First Seen",
      sortable: true,
      render: (u) => <RelativeTime value={u.first_seen} className="text-xs text-fg-subtle" />,
    },
    {
      key: "last_active",
      header: "Last Active",
      sortable: true,
      render: (u) => <RelativeTime value={u.last_active} className="text-xs text-fg-subtle" />,
    },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      render: (u) => (
        <div className="flex items-center justify-end gap-1">
          <Link
            href={carry(entityHref({ kind: "visitor", id: u.ip_address }))}
            onClick={(e) => e.stopPropagation()}
            aria-label={`View sessions for ${u.ip_address}`}
            title="View sessions"
            className={iconButton}
          >
            <ListTree {...ICON} />
          </Link>
          {canWriteTestIp && !u.is_test ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                void onMarkTestIp(u);
              }}
              disabled={markTestIp.isPending}
              aria-label={`Mark ${u.ip_address} as test IP`}
              title="Mark as test IP"
              className={iconButton}
            >
              <FlaskConical {...ICON} />
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Visitors"
        subtitle="Audience-scoped visitors by client IP: activity, sessions and engagement"
        icon={UsersIcon}
        showDateRange
        actions={
          canWriteTestIp ? (
            <button type="button" onClick={() => setConfigOpen(true)} className={secondaryButton}>
              <SettingsIcon {...ICON} />
              Manage test IPs
            </button>
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="visitor-kpis">
          {statsData ? (
            <>
              <StatCard title="Visitors" value={statsData.kpis.visitors} subtitle={range.label ?? undefined} icon={UsersIcon} />
              <StatCard title="New visitors" value={statsData.kpis.new_visitors} icon={UserPlus} />
              <StatCard title="Returning visitors" value={statsData.kpis.returning_visitors} icon={Repeat} />
              <StatCard title="Active now" value={statsData.kpis.active_now} subtitle="Last 30 minutes" icon={UserCheck} />
            </>
          ) : (
            <>
              <StatCard title="Total filtered visitors" value={total} icon={UsersIcon} />
              <StatCard title="Active on this page" value={activeCount} icon={UserCheck} />
              <StatCard title="Queries on this page" value={totalQueries} icon={Activity} />
            </>
          )}
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <ChartCard
            title="New vs returning"
            subtitle="Visitors per day"
            window={range.label ?? undefined}
            loading={stats.isLoading}
            error={statsError}
            onRetry={() => void stats.refetch()}
            empty={statsAbsent || (statsData?.new_vs_returning.length ?? 0) === 0}
            emptyMessage={statsAbsent ? "Visitor stats are not available yet" : undefined}
            table={{
              columns: [
                { key: "day", label: "Day" },
                { key: "new", label: "New", numeric: true },
                { key: "returning", label: "Returning", numeric: true },
              ],
              rows: (statsData?.new_vs_returning ?? []).map((d) => ({ day: formatDay(d.day), new: d.new, returning: d.returning })),
            }}
          >
            <StackedBar
              data={statsData?.new_vs_returning ?? []}
              xKey="day"
              series={[
                { key: "new", label: "New" },
                { key: "returning", label: "Returning" },
              ]}
              formatLabel={formatDay}
              label="New vs returning visitors per day"
            />
          </ChartCard>

          <ChartCard
            title="When visitors are active"
            subtitle="Sessions by weekday and hour (UTC); select a cell to filter the table"
            window={range.label ?? undefined}
            loading={stats.isLoading}
            error={statsError}
            onRetry={() => void stats.refetch()}
            empty={statsAbsent || heatData.length === 0}
            emptyMessage={statsAbsent ? "Visitor stats are not available yet" : undefined}
            table={{
              columns: [
                { key: "slot", label: "Slot" },
                { key: "sessions", label: "Sessions", numeric: true },
              ],
              rows: heatData.map((c) => ({ slot: `${c.row} ${c.col}:00`, sessions: c.value })),
            }}
          >
            <CalendarHeatmap
              data={heatData}
              rows={WEEKDAYS}
              cols={HOUR_LABELS}
              colLabelEvery={3}
              onSelect={selectHeatCell}
              label="Sessions by weekday and hour"
            />
          </ChartCard>
        </div>

        <TableCard
          toolbar={
            <div className="space-y-2">
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
                    label: "Visitor Type",
                    value: filters.type,
                    options: [
                      { label: "All Visitors", value: "all" },
                      { label: "Production", value: "production" },
                      { label: "Test", value: "test" },
                    ],
                    onChange: (v) => setFilter("type", v),
                  },
                ]}
              />
              {heat ? (
                <div className="flex items-center gap-2 text-[11px] text-fg-subtle">
                  Active in
                  <FilterChip
                    label={heatFilterLabel(heat.weekday, heat.hour)}
                    onClear={() => {
                      setPage(1);
                      set({ weekday: null, hour: null });
                    }}
                  />
                </div>
              ) : null}
            </div>
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
            isLoading={list.isLoading}
            error={errorMessage(list.error, "Failed to load visitors.")}
            onRetry={() => void list.refetch()}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(u) => router.push(carry(visitorDetailHref(u.id)))}
            emptyMessage="No visitors found matching current audience and filters."
          />
        </TableCard>
      </div>

      {configOpen && token ? (
        <TestIpManager
          token={token}
          isOpen={configOpen}
          onClose={() => setConfigOpen(false)}
          onConfigChanged={() => invalidate("visitors/")}
        />
      ) : null}
    </div>
  );
}

export default function VisitorsPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading visitors…</div>}>
      <VisitorsContent />
    </React.Suspense>
  );
}
