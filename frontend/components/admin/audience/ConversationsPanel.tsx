"use client";

import React, { useCallback, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { Eye, MessageCircle, MessagesSquare, Repeat, Users } from "lucide-react";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
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
import { NO_IP_HINT } from "@/lib/audience-routes";
import { turnsFilterLabel, useCarryHref, visitorDetailHref } from "@/lib/entity-routes";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery, usePrefetchRows } from "@/lib/query-cache";
import {
  OUTCOMES,
  OUTCOME_LABELS,
  dayParam,
  intParam,
  isOutcome,
  turnsOf,
  type ConversationRow,
  type OutcomeDay,
} from "@/lib/entity-stats-api";
import {
  AUDIENCES,
  CONVERSATION_DEFAULT_SORT,
  CONVERSATION_SORT_COLUMNS,
  conversationListQuery,
  conversationStatsQuery,
  isOtherRange,
  nextSort,
  prefetchConversation,
  conversationKey,
  conversationsBatch,
  presetDayRanges,
  useIdlePrefetch,
  useRowIntent,
  type ConversationListState,
  type Query,
} from "@/lib/audience-data";

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;
/** Search-as-you-type delay (searches are cached in memory on the server). */
const SEARCH_DEBOUNCE_MS = 150;
const SORTS = ["last_activity_desc", "last_activity_asc", "created_at_desc", "created_at_asc"];

type OutcomeRow = { day: string } & Record<string, number | string>;

function toOutcomeRows(days: readonly OutcomeDay[]): OutcomeRow[] {
  return days.map((d) => {
    const row: OutcomeRow = { day: d.day };
    for (const o of OUTCOMES) row[o] = d.counts[o] ?? 0;
    return row;
  });
}

/** Conversations tab of the Visitors & Conversations page (was /admin/conversations). */
export function ConversationsPanel() {
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
    defaultSort: CONVERSATION_DEFAULT_SORT,
    allowedSorts: SORTS,
    searchDebounceMs: SEARCH_DEBOUNCE_MS,
  });

  const listState = useMemo<ConversationListState>(
    () => ({
      audience,
      search,
      ip,
      minTurns,
      maxTurns,
      outcome,
      day,
      range: { from: range.from, to: range.to },
      sort,
      pageSize,
      offset,
    }),
    [audience, search, ip, minTurns, maxTurns, outcome, day, range.from, range.to, sort, pageSize, offset],
  );
  const listQuery = conversationListQuery(token ?? "", listState);
  // Switching page, sort, filter, audience or range swaps the rows in place
  // (the previous ones stay until the new ones arrive): never a blank table.
  const list = useApiQuery(token ? listQuery.key : null, listQuery.fetch, { keepPreviousData: true });

  const statsQuery = conversationStatsQuery(token ?? "", { audience, ip, search, range });
  const stats = useApiQuery(token ? statsQuery.key : null, statsQuery.fetch, { keepPreviousData: true });

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  // While idle, warm what the next click most likely reads: the neighbour
  // pages, each sort toggle, the other audiences and the range presets.
  const idleQueries = useMemo<Query<unknown>[] | null>(() => {
    if (!token || !list.data || list.isPlaceholderData) return null;
    const out: Query<unknown>[] = [];
    if (offset + pageSize < total) out.push(conversationListQuery(token, { ...listState, offset: offset + pageSize }));
    if (offset > 0) out.push(conversationListQuery(token, { ...listState, offset: Math.max(0, offset - pageSize) }));
    for (const column of CONVERSATION_SORT_COLUMNS) {
      out.push(conversationListQuery(token, { ...listState, sort: nextSort(sort, column), offset: 0 }));
    }
    // Other audiences / presets of the unfiltered view are precomputed on the
    // server; filtered variants would each be a fresh aggregation.
    const unfiltered = !search && !ip && minTurns === null && maxTurns === null && !outcome;
    if (unfiltered) {
      const statsState = { audience, ip, search, range: listState.range };
      for (const other of AUDIENCES.filter((a) => a !== audience)) {
        out.push(conversationStatsQuery(token, { ...statsState, audience: other }));
        out.push(conversationListQuery(token, { ...listState, audience: other, offset: 0 }));
      }
      for (const preset of presetDayRanges().filter((r) => isOtherRange(r, listState.range))) {
        out.push(conversationStatsQuery(token, { ...statsState, range: preset }));
        out.push(conversationListQuery(token, { ...listState, range: preset, offset: 0 }));
      }
    }
    return out;
  }, [token, list.data, list.isPlaceholderData, listState, offset, pageSize, total, sort, audience, ip, search, minTurns, maxTurns, outcome]);
  useIdlePrefetch(idleQueries);

  // Hovering a row warms its preview / detail; an open preview warms its neighbours.
  const rowIntent = useRowIntent((index) => {
    const row = items[index];
    if (token && row) prefetchConversation(token, row.id);
  });
  // The previews of every row on screen, in one request once idle: a click
  // opens from cache (refreshed after a live update refetches the list).
  const rowIds = useMemo(
    () => (list.data && !list.isPlaceholderData ? list.data.items.map((c) => c.id) : null),
    [list.data, list.isPlaceholderData],
  );
  usePrefetchRows(token ? rowIds : null, conversationKey, { batch: conversationsBatch(token ?? "") }, { version: list.updatedAt });
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

  // A new date range starts the list from the first page.
  const rangeKey = `${range.from ?? ""}|${range.to ?? ""}`;
  const seenRange = useRef(rangeKey);
  useEffect(() => {
    if (seenRange.current === rangeKey) return;
    seenRange.current = rangeKey;
    setPage(1);
  }, [rangeKey, setPage]);

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
  const prevId = previewIndex > 0 ? items[previewIndex - 1].id : null;
  const nextId = previewIndex >= 0 && previewIndex < items.length - 1 ? items[previewIndex + 1].id : null;
  useEffect(() => {
    if (!token) return;
    if (nextId) prefetchConversation(token, nextId);
    if (prevId) prefetchConversation(token, prevId);
  }, [token, prevId, nextId]);
  const openPreview = useCallback((id: string | null) => set({ preview: id }, { history: id ? "push" : "replace" }), [set]);

  const uniqueIps = Array.from(new Set(items.map((c) => c.created_ip).filter((v): v is string => Boolean(v))));
  if (ip && !uniqueIps.includes(ip)) uniqueIps.unshift(ip);
  const turnSpark = useMemo(
    () =>
      (statsData?.outcome_by_day ?? []).map((d) => ({
        label: formatDay(d.day),
        value: Object.values(d.counts).reduce<number>((sum, n) => sum + (n ?? 0), 0),
      })),
    [statsData],
  );
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
            className={`rounded-sm font-mono text-xs text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            {c.created_ip}
          </Link>
        ) : (
          <span
            className="cursor-help text-xs italic text-fg-subtle underline decoration-dotted underline-offset-2"
            title={NO_IP_HINT}
          >
            Not recorded
            <span className="sr-only"> ({NO_IP_HINT})</span>
          </span>
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
    <div className="space-y-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3" data-tour="conversations-stats">
          <StatCard
            title="Conversations"
            value={statsData?.totals.conversations ?? total}
            subtitle={statsData ? (range.label ? `Started in ${range.label.toLowerCase()}` : "In this range") : "Matching filters"}
            icon={MessagesSquare}
          />
          <StatCard
            title="Avg turns per conversation"
            value={statsData?.totals.avg_turns != null ? statsData.totals.avg_turns.toFixed(1) : "—"}
            subtitle="One turn = a question and its answer"
            icon={Repeat}
          />
          {statsData ? (
            <StatCard title="Turns" value={statsData.totals.turns} subtitle="Turns per day" spark={turnSpark} icon={MessageCircle} />
          ) : (
            <StatCard title="Messages on this page" value={totalMessages} icon={Users} />
          )}
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="conversations-charts">
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
            <Histogram
              data={histogram}
              onSelect={selectBin}
              formatEdge={formatEdge}
              xAxisLabel="Turns per conversation"
              yAxisLabel="Conversations"
              valueLabel="Conversations"
              label="Conversations by number of turns"
            />
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
              xAxisLabel="Date (UTC)"
              yAxisLabel="Share of turns (%)"
              label="Outcome share per day"
            />
          </ChartCard>
        </div>

        <div data-tour="conversations-table" {...rowIntent}>
        <TableCard
          toolbar={
            <div className="space-y-2" data-tour="conversations-filters">
              <TableToolbar
                title="Conversations"
                countLabel={list.isLoading ? undefined : `${total.toLocaleString()} conversation${total === 1 ? "" : "s"}`}
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
            isLoading={list.isLoading || list.isPlaceholderData}
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
          onPrev={prevId ? () => set({ preview: prevId }) : undefined}
          onNext={nextId ? () => set({ preview: nextId }) : undefined}
          audience={audience}
        />
      ) : null}
    </div>
  );
}
