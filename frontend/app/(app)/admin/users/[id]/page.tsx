"use client";

import React, { useCallback, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ChartPie, ChevronDown, ChevronUp, CircleDollarSign, Clock, ListTree, MessageSquareText, MessagesSquare, Search, ThumbsUp, User } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { TableCard } from "@/components/admin/TableCard";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { formatNumber } from "@/components/admin/TableFormatters";
import { ChartCard } from "@/components/charts/ChartCard";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import { Waterfall, type WaterfallStep } from "@/components/charts/Waterfall";
import { EntityLink } from "@/components/ui/EntityLink";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageShellSkeleton } from "@/components/ui/PageShellSkeleton";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { secondaryButton } from "@/components/ui/styles";
import {
  SectionCard,
  ToneChip,
  errorMessage,
  formatDay,
  formatMs,
  formatUsd,
  outcomeTone,
  ratingTone,
  useRangeParams,
} from "@/components/admin/entity/shared";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/query-cache";
import {
  isOtherRange,
  prefetchConversation,
  presetDayRanges,
  useIdlePrefetch,
  useRowIntent,
  visitorQuery,
  visitorTimelineQuery,
} from "@/lib/audience-data";
import { useDeepLinkParam } from "@/lib/use-deep-link";
import { useClientTable } from "@/lib/use-client-table";
import { activitySparks } from "@/lib/visitor-activity";
import { entityHref } from "@/lib/entity-links";
import { AUDIENCE_PAGE, AUDIENCE_PAGE_LABEL, conversationsHref, useCarryHref } from "@/lib/entity-routes";
import {
  OUTCOMES,
  OUTCOME_LABELS,
  isAbsent,
  type TimelineQuery,
  type VisitorConversation,
} from "@/lib/entity-stats-api";

const QueryInspectorDrawer = dynamic(
  () => import("@/components/admin/usage/QueryInspectorDrawer").then((mod) => mod.QueryInspectorDrawer),
  { ssr: false },
);

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;
const HOUR_MS = 60 * 60 * 1000;
/** Timeline rows shown at first and added per "Show more". */
const TIMELINE_PAGE = 10;
const TIMELINE_ROW_PX = 26;
const TIMELINE_LABEL_PX = 220;
const MIN_SESSION_HOURS = 0.05;

function formatHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;

interface TimelineBar {
  sessionId: string;
  title: string;
  startedAt: number;
  endedAt: number;
}

const OUTCOME_FILTER = [{ label: "All outcomes", value: "all" }, ...OUTCOMES.map((o) => ({ label: OUTCOME_LABELS[o], value: o }))];

function VisitorDetailContent() {
  const router = useRouter();
  const carry = useCarryHref();
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params?.id ?? "");
  const { token } = useAuth();
  const range = useRangeParams();
  const [inspect, setInspect] = useDeepLinkParam("inspect");
  const [timelineCount, setTimelineCount] = useState(TIMELINE_PAGE);
  const [outcome, setOutcome] = useState("all");

  const visitorQ = visitorQuery(token ?? "", id);
  const visitor = useApiQuery(token && id ? visitorQ.key : null, visitorQ.fetch);
  const timelineQ = visitorTimelineQuery(token ?? "", id, range);
  // A range switch keeps the previous timeline on screen until the new one arrives.
  const timeline = useApiQuery(token && id ? timelineQ.key : null, timelineQ.fetch, { keepPreviousData: true });
  // While idle, warm the timeline for the other range presets.
  useIdlePrefetch(
    token && id && timeline.data !== undefined && !timeline.isPlaceholderData
      ? presetDayRanges()
          .filter((r) => isOtherRange(r, range))
          .map((r) => visitorTimelineQuery(token, id, r))
      : null,
  );

  const user = visitor.data;
  const tl = timeline.data ?? null;
  const timelineAbsent = !timeline.isLoading && !timeline.error && tl === null;
  const ip = user?.ip_address ?? tl?.ip_address ?? null;
  const sparks = useMemo(() => activitySparks(tl), [tl]);

  const conversations = useMemo(() => user?.conversations_data ?? [], [user]);
  const sessionSearch = useCallback((c: VisitorConversation) => `${c.title ?? ""} ${c.id}`, []);
  const sessionsTable = useClientTable(conversations, { searchText: sessionSearch });
  // Hovering a session row warms its conversation page.
  const sessionIntent = useRowIntent((index) => {
    const row = sessionsTable.paged[index];
    if (token && row) prefetchConversation(token, row.id);
  });

  const queries = useMemo(() => tl?.queries ?? [], [tl]);
  const querySearch = useCallback((q: TimelineQuery) => `${q.query} ${q.request_id}`, []);
  const outcomeFilter = useCallback((q: TimelineQuery) => outcome === "all" || q.outcome === outcome, [outcome]);
  const queriesTable = useClientTable(queries, { searchText: querySearch, filter: outcomeFilter });

  // Sessions as bars on a time axis: B7.2 timeline when deployed, else the
  // sessions the existing detail endpoint already returns. Chronological.
  const allBars = useMemo<TimelineBar[]>(() => {
    const source = tl
      ? tl.sessions.map((s) => ({ sessionId: s.session_id, title: s.title ?? "", start: s.started_at, end: s.ended_at }))
      : conversations.map((c) => ({
          sessionId: c.id,
          title: c.title ?? "",
          start: c.created_at ?? "",
          end: c.last_activity_at ?? c.created_at ?? "",
        }));
    return source
      .map((s) => ({
        sessionId: s.sessionId,
        title: s.title || "Untitled",
        startedAt: new Date(s.start).getTime(),
        endedAt: new Date(s.end).getTime(),
      }))
      .filter((s) => Number.isFinite(s.startedAt))
      .sort((a, b) => a.startedAt - b.startedAt);
  }, [conversations, tl]);
  // The most recent `timelineCount` sessions (the rest behind "Show more").
  const bars = useMemo(() => allBars.slice(-timelineCount), [allBars, timelineCount]);

  const steps = useMemo<(WaterfallStep & { sessionId: string })[]>(() => {
    if (bars.length === 0) return [];
    const t0 = bars[0].startedAt;
    return bars.map((b) => ({
      sessionId: b.sessionId,
      label: b.title,
      start: (b.startedAt - t0) / HOUR_MS,
      value: Math.max(MIN_SESSION_HOURS, ((Number.isFinite(b.endedAt) ? b.endedAt : b.startedAt) - b.startedAt) / HOUR_MS),
    }));
  }, [bars]);

  const convColumns: ColumnDef<VisitorConversation>[] = [
    {
      key: "title",
      header: "Session",
      render: (c) => (
        <div className="flex min-w-0 flex-col">
          <EntityLink entity={{ kind: "conversation", id: c.id }} mono={false} copyable={false}>
            {c.title || "Untitled Conversation"}
          </EntityLink>
          <span className="font-mono text-[10px] text-fg-subtle">{c.id}</span>
        </div>
      ),
    },
    { key: "created_at", header: "Started", render: (c) => <RelativeTime value={c.created_at} className="text-xs text-fg-muted" /> },
    {
      key: "last_activity_at",
      header: "Last Activity",
      render: (c) => <RelativeTime value={c.last_activity_at} className="text-xs text-fg-muted" />,
    },
    { key: "message_count", header: "Messages", align: "right", render: (c) => formatNumber(c.message_count ?? 0) },
  ];

  const queryColumns: ColumnDef<TimelineQuery>[] = [
    {
      key: "query",
      header: "Query",
      render: (q) => (
        <span className="line-clamp-2 max-w-[44ch] text-xs text-fg" title={q.query}>
          {q.query}
        </span>
      ),
    },
    {
      key: "outcome",
      header: "Outcome",
      render: (q) => <ToneChip tone={outcomeTone(q.outcome)}>{q.outcome ?? "unknown"}</ToneChip>,
    },
    {
      key: "turn",
      header: "Turn",
      render: (q) => (
        <EntityLink entity={{ kind: "turn", id: String(q.turn_index), conversationId: q.session_id }} copyable={false}>
          #{q.turn_index + 1}
        </EntityLink>
      ),
    },
    { key: "latency_ms", header: "Latency", align: "right", render: (q) => <span className="whitespace-nowrap">{formatMs(q.latency_ms)}</span> },
    { key: "cost_usd", header: "Cost", align: "right", render: (q) => <span className="whitespace-nowrap">{formatUsd(q.cost_usd)}</span> },
    { key: "created_at", header: "When", render: (q) => <RelativeTime value={q.created_at} className="text-xs text-fg-subtle" /> },
  ];

  if (visitor.isLoading) {
    return (
      <PageShellSkeleton label="Loading visitor" />
    );
  }

  if (!user) {
    const notFound = isAbsent(visitor.error);
    return (
      <div className="flex min-h-screen flex-col bg-surface-page text-fg">
        <DashboardHeader
          title="Visitor"
          icon={User}
          showAudienceTabs={false}
          breadcrumb={[{ label: AUDIENCE_PAGE_LABEL, href: carry(AUDIENCE_PAGE) }, { label: id || "Unknown" }]}
        />
        <div className="p-8">
          {notFound ? (
            <EmptyState
              title="Visitor not found"
              description="The requested visitor could not be found."
              action={
                <Link href={carry(AUDIENCE_PAGE)} className={secondaryButton}>
                  Back to Visitors
                </Link>
              }
            />
          ) : (
            <ErrorState message={errorMessage(visitor.error, "Couldn't load this visitor.")} onRetry={() => void visitor.refetch()} />
          )}
        </div>
      </div>
    );
  }

  const label = ip || `Visitor ${user.id.slice(0, 8)}`;
  const windowLabel = range.label ?? undefined;
  const allTime = (n: number | null | undefined) => `${(n ?? 0).toLocaleString()} all time`;
  const totalCost = sparks ? sparks.cost.reduce((sum, d) => sum + d.value, 0) : null;
  const hiddenSessions = allBars.length - bars.length;
  const truncatedQueries = tl?.truncated.queries ?? false;

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title={label}
        icon={User}
        showAudienceTabs={false}
        showDateRange
        breadcrumb={[{ label: AUDIENCE_PAGE_LABEL, href: carry(AUDIENCE_PAGE) }, { label }]}
        actions={
          ip ? (
            <>
              <Link href={carry(entityHref({ kind: "visitor", id: ip }))} className={secondaryButton} title="Sessions in Usage & Cost">
                <ListTree {...ICON} />
                <span className="hidden lg:inline">Usage sessions</span>
              </Link>
              <Link href={carry(conversationsHref({ ip }))} className={secondaryButton} title="This visitor's conversations">
                <MessagesSquare {...ICON} />
                <span className="hidden lg:inline">Conversations</span>
              </Link>
              <Link href={entityHref({ kind: "analytics", visitor: id })} className={secondaryButton}>
                <ChartPie {...ICON} />
                Analytics
              </Link>
            </>
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-4 sm:p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5" data-testid="visitor-detail-kpis">
          <KpiTrendCard
            label="Sessions"
            value={formatNumber(sparks ? sparks.totals.sessions : (user.sessions ?? 0))}
            windowLabel={sparks ? windowLabel : "All time"}
            trend={sparks?.sessions ?? []}
            icon={MessagesSquare}
            hint={sparks ? `Sessions per day · ${allTime(user.sessions)}` : "Per-day sessions are not available yet"}
          />
          <KpiTrendCard
            label="Queries"
            value={formatNumber(sparks ? sparks.totals.queries : (user.queries ?? 0))}
            windowLabel={sparks ? windowLabel : "All time"}
            trend={sparks?.queries ?? []}
            icon={Search}
            hint={sparks ? `Queries per day · ${allTime(user.queries)}` : "Per-day queries are not available yet"}
          />
          <KpiTrendCard
            label="Feedback"
            value={formatNumber(sparks ? sparks.totals.feedback : (user.feedback_count ?? 0))}
            windowLabel={sparks ? windowLabel : "All time"}
            trend={sparks?.feedback ?? []}
            icon={ThumbsUp}
            hint={sparks ? `Ratings per day · ${allTime(user.feedback_count)}` : "Per-day feedback is not available yet"}
          />
          <KpiTrendCard
            label="Last active"
            value={<RelativeTime value={user.last_active} />}
            windowLabel={sparks ? `Active ${sparks.totals.activeDays} of ${plural(sparks.totals.days, "day")}` : undefined}
            trend={sparks?.active ?? []}
            tone="info"
            icon={Clock}
            hint={<RelativeTime value={user.first_seen} prefix="First seen " />}
          />
          <KpiTrendCard
            label="Cost"
            value={formatUsd(totalCost)}
            windowLabel={windowLabel}
            trend={sparks?.cost ?? []}
            formatTrendValue={formatUsd}
            icon={CircleDollarSign}
            goodDirection="down"
            hint={timelineAbsent ? "Per-visitor cost is not available yet" : "LLM cost (USD) per day"}
          />
        </div>

        <ChartCard
          title="Activity timeline"
          subtitle={`Most recent sessions on a time axis; hover a bar for the full title, select it to open the conversation`}
          window={windowLabel}
          loading={timeline.isLoading && conversations.length === 0}
          empty={steps.length === 0}
          emptyMessage="No sessions for this visitor"
          bodyClassName="w-full"
          table={{
            columns: [
              { key: "title", label: "Session" },
              { key: "start", label: "Started" },
              { key: "duration", label: "Duration", numeric: true },
            ],
            rows: bars.map((b) => ({
              title: <EntityLink entity={{ kind: "conversation", id: b.sessionId }} mono={false} copyable={false}>{b.title}</EntityLink>,
              start: <RelativeTime value={new Date(b.startedAt).toISOString()} display="absolute" />,
              duration: formatHours(Math.max(0, b.endedAt - b.startedAt) / HOUR_MS),
            })),
          }}
        >
          <Waterfall
            data={steps}
            height={steps.length * TIMELINE_ROW_PX + 28}
            labelWidth={TIMELINE_LABEL_PX}
            label="Sessions over time"
            xAxisLabel="Hours since the first session shown"
            yAxisLabel="Session"
            valueLabel="Duration"
            formatValue={formatHours}
            onSelect={(step) => {
              const s = steps.find((x) => x === step);
              if (s) router.push(carry(entityHref({ kind: "conversation", id: s.sessionId })));
            }}
          />
          {allBars.length > TIMELINE_PAGE ? (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-[11px] text-fg-subtle">
              <span>
                Showing the latest {bars.length} of {plural(allBars.length, "session")}
                {tl?.truncated.sessions ? " (first 200 in range)" : ""}
              </span>
              <div className="flex items-center gap-2">
                {hiddenSessions > 0 ? (
                  <button type="button" className={secondaryButton} onClick={() => setTimelineCount((n) => n + TIMELINE_PAGE)}>
                    <ChevronDown {...ICON} />
                    Show {Math.min(TIMELINE_PAGE, hiddenSessions)} more
                  </button>
                ) : null}
                {bars.length > TIMELINE_PAGE ? (
                  <button type="button" className={secondaryButton} onClick={() => setTimelineCount(TIMELINE_PAGE)}>
                    <ChevronUp {...ICON} />
                    Show fewer
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </ChartCard>

        <TableCard
          toolbar={
            <TableToolbar
              title="Sessions"
              countLabel={`${plural(sessionsTable.total, "session")} from this visitor`}
              search={sessionsTable.search}
              onSearchChange={sessionsTable.setSearch}
              searchPlaceholder="Search title or session ID…"
            />
          }
          pagination={
            <Pagination
              page={sessionsTable.page}
              pageCount={sessionsTable.pageCount}
              totalItems={sessionsTable.total}
              pageSize={sessionsTable.pageSize}
              onPageChange={sessionsTable.setPage}
              onPageSizeChange={sessionsTable.setPageSize}
            />
          }
        >
          <div {...sessionIntent}>
            <DataTable
              columns={convColumns}
              data={sessionsTable.paged}
              keyField="id"
              onRowClick={(c) => router.push(carry(entityHref({ kind: "conversation", id: c.id })))}
              emptyMessage={conversations.length === 0 ? "No sessions found for this visitor." : "No sessions match this search."}
            />
          </div>
        </TableCard>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <TableCard
            className="xl:col-span-2"
            toolbar={
              <TableToolbar
                title="Queries"
                countLabel={
                  tl
                    ? `${queriesTable.total.toLocaleString()} quer${queriesTable.total === 1 ? "y" : "ies"}${truncatedQueries ? " (latest 100 in range)" : ""} · select one to inspect`
                    : undefined
                }
                search={queriesTable.search}
                onSearchChange={queriesTable.setSearch}
                searchPlaceholder="Search query text…"
                filters={[
                  {
                    id: "outcome",
                    label: "Outcome",
                    value: outcome,
                    options: OUTCOME_FILTER,
                    onChange: (v) => {
                      setOutcome(v || "all");
                      queriesTable.setPage(1);
                    },
                  },
                ]}
              />
            }
            pagination={
              tl ? (
                <Pagination
                  page={queriesTable.page}
                  pageCount={queriesTable.pageCount}
                  totalItems={queriesTable.total}
                  pageSize={queriesTable.pageSize}
                  onPageChange={queriesTable.setPage}
                  onPageSizeChange={queriesTable.setPageSize}
                />
              ) : undefined
            }
          >
            {timeline.isLoading ? (
              <div className="p-5">
                <SkeletonCard label="Loading queries" />
              </div>
            ) : timeline.error ? (
              <div className="p-5">
                <ErrorState compact message={errorMessage(timeline.error, "Couldn't load queries.")} onRetry={() => void timeline.refetch()} />
              </div>
            ) : !tl ? (
              <div className="p-5">
                <EmptyState compact icon={Search} title="Per-visitor queries are not available yet" />
              </div>
            ) : (
              <DataTable
                columns={queryColumns}
                data={queriesTable.paged}
                keyField="request_id"
                onRowClick={(q) => setInspect(q.request_id)}
                emptyMessage={queries.length === 0 ? "No queries in this range." : "No queries match the search or outcome filter."}
              />
            )}
          </TableCard>

          <SectionCard
            title="Feedback"
            description={tl ? `${plural(tl.feedback.length, "rating")} this visitor gave in the range` : "Ratings this visitor gave."}
          >
            {timeline.isLoading ? (
              <SkeletonCard label="Loading feedback" />
            ) : !tl ? (
              <EmptyState compact icon={MessageSquareText} title="Per-visitor feedback is not available yet" />
            ) : tl.feedback.length === 0 ? (
              <EmptyState compact icon={MessageSquareText} title="No feedback in this range" />
            ) : (
              <ul className="max-h-[560px] space-y-3 overflow-y-auto pr-1" aria-label="Visitor feedback" tabIndex={0}>
                {tl.feedback.map((f) => (
                  <li key={f.id} className="rounded-lg border border-line p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <ToneChip tone={ratingTone(f.rating)}>{f.rating} / 5</ToneChip>
                      {f.reason_tags.map((t) => (
                        <ToneChip key={t} tone="neutral">
                          {t.replace(/_/g, " ")}
                        </ToneChip>
                      ))}
                      <RelativeTime value={f.created_at} className="ml-auto text-[11px] text-fg-subtle" />
                    </div>
                    {f.comment ? <p className="mt-2 text-xs text-fg-muted">{f.comment}</p> : null}
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px]">
                      <EntityLink entity={{ kind: "feedback", id: f.message_id }} mono={false} copyable={false}>
                        Open in Feedback
                      </EntityLink>
                      {f.turn_index !== null ? (
                        <EntityLink
                          entity={{ kind: "turn", id: String(f.turn_index), conversationId: f.session_id }}
                          mono={false}
                          copyable={false}
                        >
                          View turn #{f.turn_index + 1}
                        </EntityLink>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>
      </div>

      {inspect && token ? <QueryInspectorDrawer requestId={inspect} token={token} onClose={() => setInspect(null)} /> : null}
    </div>
  );
}

export default function VisitorDetailPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading visitor…</div>}>
      <VisitorDetailContent />
    </React.Suspense>
  );
}
