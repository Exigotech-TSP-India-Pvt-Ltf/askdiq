"use client";

import React, { useCallback, useMemo } from "react";
import Link from "next/link";
import { Eye, MessageCircle, MessagesSquare, Repeat, Users } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber } from "@/components/admin/TableFormatters";
import { StatCard } from "@/components/admin/insights/StatCard";
import { ChartCard } from "@/components/charts/ChartCard";
import { Histogram, type HistogramBin } from "@/components/charts/Histogram";
import { StackedBar, type BarSegment } from "@/components/charts/StackedBar";
import { EntityLink } from "@/components/ui/EntityLink";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { focusRing, iconButton } from "@/components/ui/styles";
import { FilterChip, errorMessage, formatDay, useRangeParams } from "@/components/admin/entity/shared";
import { ConversationPreviewDrawer } from "@/components/admin/entity/ConversationPreviewDrawer";
import { useTableState } from "@/lib/use-table-state";
import { useDeepLink } from "@/lib/use-deep-link";
import { turnsFilterLabel, useCarryHref, visitorDetailHref } from "@/lib/entity-routes";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/query-cache";
import {
  OUTCOMES,
  OUTCOME_LABELS,
  dayParam,
  getConversationStats,
  getConversations,
  intParam,
  isOutcome,
  turnsOf,
  type ConversationRow,
  type OutcomeDay,
} from "@/lib/entity-stats-api";

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;

type OutcomeRow = { day: string } & Record<string, number | string>;

function toOutcomeRows(days: readonly OutcomeDay[]): OutcomeRow[] {
  return days.map((d) => {
    const row: OutcomeRow = { day: d.day };
    for (const o of OUTCOMES) row[o] = d.counts[o] ?? 0;
    return row;
  });
}

function ConversationsContent() {
  const { token } = useAuth();
  const carry = useCarryHref();
  const { get, set } = useDeepLink();
  const range = useRangeParams();

  const ip = get("ip") || null;
  const minTurns = intParam(get("min_turns"));
  const maxTurns = intParam(get("max_turns"));
  const outcomeRaw = get("outcome");
  const outcome = isOutcome(outcomeRaw) ? outcomeRaw : null;
  const day = dayParam(get("day"));
  const preview = get("preview");

  const { page, pageSize, offset, sort, search, audience, setPage, setPageSize, setSort, setSearchInput } = useTableState({
    tableKey: "convs",
    defaultSort: "last_activity_desc",
    allowedSorts: ["last_activity_desc", "last_activity_asc", "created_at_desc", "created_at_asc"],
  });

  const list = useApiQuery(
    token
      ? ["conversations", "list", audience, search, ip, minTurns, maxTurns, outcome, day, sort, pageSize, offset]
      : null,
    (signal) =>
      getConversations(
        token ?? "",
        {
          audience,
          q: search || undefined,
          ip: ip ?? undefined,
          min_turns: minTurns,
          max_turns: maxTurns,
          outcome,
          day,
          sort,
          limit: pageSize,
          offset,
        },
        signal,
      ),
  );

  const stats = useApiQuery(
    token ? ["conversations", "stats", audience, ip, search, range.from, range.to] : null,
    (signal) =>
      getConversationStats(
        token ?? "",
        { audience, ip: ip ?? undefined, q: search || undefined, from: range.from, to: range.to },
        signal,
      ),
  );

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const statsData = stats.data ?? null;
  const statsAbsent = !stats.isLoading && !stats.error && statsData === null;
  const statsError = errorMessage(stats.error, "Couldn't load conversation stats.");

  const bins = useMemo(() => statsData?.turns_histogram ?? [], [statsData]);
  const openMin = bins.find((b) => b.max_turns === null)?.min_turns ?? null;
  const histogram = useMemo<HistogramBin[]>(
    () => bins.map((b) => ({ x0: b.min_turns, x1: b.max_turns ?? b.min_turns, count: b.conversations })),
    [bins],
  );
  const formatEdge = useCallback((v: number) => (v === openMin ? `${v}+` : String(v)), [openMin]);
  const outcomeRows = useMemo(() => toOutcomeRows(statsData?.outcome_by_day ?? []), [statsData]);

  const filter = useCallback(
    (update: Record<string, string | number | null>) => {
      setPage(1);
      set({ ...update, preview: null }, { history: "push" });
    },
    [set, setPage],
  );

  const selectBin = useCallback(
    (bin: HistogramBin) => {
      const source = bins.find((b) => b.min_turns === bin.x0);
      if (source) filter({ min_turns: source.min_turns, max_turns: source.max_turns });
    },
    [bins, filter],
  );

  const selectOutcome = useCallback(
    (seg: BarSegment<OutcomeRow>) => filter({ outcome: seg.series, day: String(seg.row.day) }),
    [filter],
  );

  const previewIndex = preview ? items.findIndex((c) => c.id === preview) : -1;
  const openPreview = useCallback((id: string | null) => set({ preview: id }, { history: id ? "push" : "replace" }), [set]);

  const uniqueIps = Array.from(new Set(items.map((c) => c.created_ip).filter((v): v is string => Boolean(v))));
  if (ip && !uniqueIps.includes(ip)) uniqueIps.unshift(ip);
  const totalMessages = items.reduce((sum, c) => sum + (c.message_count ?? 0), 0);

  const chips: { label: string; clear: Record<string, null> }[] = [];
  if (ip) chips.push({ label: `Visitor ${ip}`, clear: { ip: null } });
  if (minTurns !== null)
    chips.push({ label: turnsFilterLabel({ min_turns: minTurns, max_turns: maxTurns }), clear: { min_turns: null, max_turns: null } });
  if (outcome)
    chips.push({ label: `${OUTCOME_LABELS[outcome]}${day ? ` on ${formatDay(day)}` : ""}`, clear: { outcome: null, day: null } });

  const columns: ColumnDef<ConversationRow>[] = [
    {
      key: "title",
      header: "Conversation",
      render: (c) => (
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="font-semibold text-fg">{c.title || "Untitled Session"}</span>
          <EntityLink entity={{ kind: "conversation", id: c.id }} />
        </div>
      ),
    },
    {
      key: "created_ip",
      header: "Origin IP",
      render: (c) =>
        c.created_ip && c.created_ip !== "Unknown" ? (
          <Link
            href={carry(visitorDetailHref(`client-${c.created_ip}`))}
            onClick={(e) => e.stopPropagation()}
            className={`rounded font-mono text-xs text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            {c.created_ip}
          </Link>
        ) : (
          <span className="font-mono text-xs text-fg-subtle">—</span>
        ),
    },
    { key: "turns", header: "Turns", align: "right", render: (c) => formatNumber(turnsOf(c)) },
    { key: "message_count", header: "Messages", align: "right", render: (c) => formatNumber(c.message_count ?? 0) },
    {
      key: "created_at",
      header: "Created",
      sortable: true,
      render: (c) => <RelativeTime value={c.created_at} className="text-xs text-fg-subtle" />,
    },
    {
      key: "last_activity",
      header: "Last Active",
      sortable: true,
      render: (c) => <RelativeTime value={c.last_activity_at} className="text-xs text-fg-subtle" />,
    },
    {
      key: "preview",
      header: "",
      align: "right",
      render: (c) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            openPreview(c.id);
          }}
          aria-label={`Preview ${c.title || c.id}`}
          title="Preview"
          className={iconButton}
        >
          <Eye {...ICON} />
        </button>
      ),
    },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Conversations"
        subtitle="Audience-scoped conversations, turn counts and outcomes"
        icon={MessagesSquare}
        showDateRange
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard title="Conversations" value={statsData?.totals.conversations ?? total} subtitle={statsData ? (range.label ?? undefined) : "Matching filters"} icon={MessagesSquare} />
          <StatCard
            title="Avg turns per conversation"
            value={statsData?.totals.avg_turns != null ? statsData.totals.avg_turns.toFixed(1) : "—"}
            icon={Repeat}
          />
          {statsData ? (
            <StatCard title="Turns" value={statsData.totals.turns} icon={MessageCircle} />
          ) : (
            <StatCard title="Messages on this page" value={totalMessages} icon={Users} />
          )}
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <ChartCard
            title="Conversation length"
            subtitle="Conversations by number of turns; select a bar to filter"
            window={range.label ?? undefined}
            loading={stats.isLoading}
            error={statsError}
            onRetry={() => void stats.refetch()}
            empty={statsAbsent || histogram.length === 0}
            emptyMessage={statsAbsent ? "Conversation stats are not available yet" : undefined}
            table={{
              columns: [
                { key: "turns", label: "Turns" },
                { key: "conversations", label: "Conversations", numeric: true },
              ],
              rows: bins.map((b) => ({ turns: turnsFilterLabel(b), conversations: b.conversations })),
            }}
          >
            <Histogram data={histogram} onSelect={selectBin} formatEdge={formatEdge} label="Conversations by number of turns" />
          </ChartCard>

          <ChartCard
            title="Outcomes by day"
            subtitle="Share of turns per outcome; select a segment to filter"
            window={range.label ?? undefined}
            loading={stats.isLoading}
            error={statsError}
            onRetry={() => void stats.refetch()}
            empty={statsAbsent || outcomeRows.length === 0}
            emptyMessage={statsAbsent ? "Conversation stats are not available yet" : undefined}
            table={{
              columns: [{ key: "day", label: "Day" }, ...OUTCOMES.map((o) => ({ key: o, label: OUTCOME_LABELS[o], numeric: true }))],
              rows: outcomeRows.map((r) => ({ ...r, day: formatDay(String(r.day)) })),
            }}
          >
            <StackedBar
              data={outcomeRows}
              xKey="day"
              series={OUTCOMES.map((o) => ({ key: o, label: OUTCOME_LABELS[o] }))}
              percent
              formatLabel={formatDay}
              onSelect={selectOutcome}
              label="Outcome share per day"
            />
          </ChartCard>
        </div>

        <TableCard
          toolbar={
            <div className="space-y-2">
              <TableToolbar
                search={search}
                onSearchChange={setSearchInput}
                searchPlaceholder="Search title, ID, or origin IP…"
                filters={[
                  {
                    id: "ip",
                    label: "Origin IP",
                    value: ip ?? "",
                    options: [{ label: "All Origin IPs", value: "" }, ...uniqueIps.map((v) => ({ label: v, value: v }))],
                    onChange: (v) => filter({ ip: v || null }),
                  },
                ]}
              />
              {chips.length > 0 ? (
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-fg-subtle" aria-label="Active filters">
                  {chips.map((c) => (
                    <FilterChip key={c.label} label={c.label} onClear={() => filter(c.clear)} />
                  ))}
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
            keyField="id"
            isLoading={list.isLoading}
            error={errorMessage(list.error, "Failed to load conversations.")}
            onRetry={() => void list.refetch()}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(c) => openPreview(c.id)}
            emptyMessage="No conversations found matching current audience and filters."
          />
        </TableCard>
      </div>

      {token ? (
        <ConversationPreviewDrawer
          token={token}
          conversationId={preview}
          onClose={() => openPreview(null)}
          onPrev={previewIndex > 0 ? () => set({ preview: items[previewIndex - 1].id }) : undefined}
          onNext={previewIndex >= 0 && previewIndex < items.length - 1 ? () => set({ preview: items[previewIndex + 1].id }) : undefined}
        />
      ) : null}
    </div>
  );
}

export default function ConversationsPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading conversations…</div>}>
      <ConversationsContent />
    </React.Suspense>
  );
}
