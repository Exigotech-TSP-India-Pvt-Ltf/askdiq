"use client";

import React, { useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { CircleDollarSign, Clock, ListTree, MessageSquareText, MessagesSquare, Search, ThumbsUp, User } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { TableCard } from "@/components/admin/TableCard";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { formatNumber } from "@/components/admin/TableFormatters";
import { StatCard } from "@/components/admin/insights/StatCard";
import { ChartCard } from "@/components/charts/ChartCard";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import { Waterfall, type WaterfallStep } from "@/components/charts/Waterfall";
import { EntityLink } from "@/components/ui/EntityLink";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { MascotLoader } from "@/components/ui/MascotLoader";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { secondaryButton } from "@/components/ui/styles";
import {
  SectionCard,
  ToneChip,
  errorMessage,
  formatDateTime,
  formatDay,
  formatMs,
  formatUsd,
  outcomeTone,
  ratingTone,
  useRangeParams,
} from "@/components/admin/entity/shared";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/query-cache";
import { useDeepLinkParam } from "@/lib/use-deep-link";
import { entityHref } from "@/lib/entity-links";
import { conversationsHref, useCarryHref } from "@/lib/entity-routes";
import {
  getVisitor,
  getVisitorTimeline,
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
const MAX_TIMELINE_SESSIONS = 20;
const MIN_SESSION_HOURS = 0.05;
const TITLE_CHARS = 16;

function formatHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} d`;
}

interface TimelineBar {
  sessionId: string;
  title: string;
  startedAt: number;
  endedAt: number;
}

function VisitorDetailContent() {
  const router = useRouter();
  const carry = useCarryHref();
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params?.id ?? "");
  const { token } = useAuth();
  const range = useRangeParams();
  const [inspect, setInspect] = useDeepLinkParam("inspect");

  const [convSearch, setConvSearch] = useState("");
  const [convPage, setConvPage] = useState(1);
  const [convPageSize, setConvPageSize] = useState(10);

  const visitor = useApiQuery(token && id ? ["visitor", id] : null, (signal) => getVisitor(token ?? "", id, signal));
  const timeline = useApiQuery(token && id ? ["visitor", id, "timeline", range.from, range.to] : null, (signal) =>
    getVisitorTimeline(token ?? "", id, { from: range.from, to: range.to }, signal),
  );

  const user = visitor.data;
  const tl = timeline.data ?? null;
  const timelineAbsent = !timeline.isLoading && !timeline.error && tl === null;
  const ip = user?.ip_address ?? tl?.ip_address ?? null;

  const conversations = useMemo(() => user?.conversations_data ?? [], [user]);
  const filteredConversations = useMemo(() => {
    const needle = convSearch.trim().toLowerCase();
    if (!needle) return conversations;
    return conversations.filter((c) => (c.title || "").toLowerCase().includes(needle) || c.id.toLowerCase().includes(needle));
  }, [conversations, convSearch]);
  const pagedConversations = useMemo(() => {
    const start = (convPage - 1) * convPageSize;
    return filteredConversations.slice(start, start + convPageSize);
  }, [filteredConversations, convPage, convPageSize]);

  // Sessions as bars on a time axis: B7.2 timeline when deployed, else the
  // sessions the existing detail endpoint already returns.
  const bars = useMemo<TimelineBar[]>(() => {
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
      .sort((a, b) => a.startedAt - b.startedAt)
      .slice(-MAX_TIMELINE_SESSIONS);
  }, [conversations, tl]);

  const steps = useMemo<(WaterfallStep & { sessionId: string })[]>(() => {
    if (bars.length === 0) return [];
    const t0 = bars[0].startedAt;
    return bars.map((b) => ({
      sessionId: b.sessionId,
      label: b.title.length > TITLE_CHARS ? `${b.title.slice(0, TITLE_CHARS - 1)}…` : b.title,
      start: (b.startedAt - t0) / HOUR_MS,
      value: Math.max(MIN_SESSION_HOURS, ((Number.isFinite(b.endedAt) ? b.endedAt : b.startedAt) - b.startedAt) / HOUR_MS),
    }));
  }, [bars]);

  const costTrend = useMemo(
    () => (tl?.cost_series ?? []).map((d) => ({ label: formatDay(d.day), value: d.cost_usd })),
    [tl],
  );
  const totalCost = tl ? tl.cost_series.reduce((sum, d) => sum + d.cost_usd, 0) : null;

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
      render: (q) => <span className="line-clamp-2 text-xs text-fg">{q.query}</span>,
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
    { key: "latency_ms", header: "Latency", align: "right", render: (q) => formatMs(q.latency_ms) },
    { key: "cost_usd", header: "Cost", align: "right", render: (q) => formatUsd(q.cost_usd) },
    { key: "created_at", header: "When", render: (q) => <RelativeTime value={q.created_at} className="text-xs text-fg-subtle" /> },
  ];

  if (visitor.isLoading) {
    return (
      <MascotLoader variant="page" label="Loading visitor" />
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
          breadcrumb={[{ label: "Visitors", href: "/admin/users" }, { label: id || "Unknown" }]}
        />
        <div className="p-8">
          {notFound ? (
            <EmptyState
              title="Visitor not found"
              description="The requested visitor could not be found."
              action={
                <Link href={carry("/admin/users")} className={secondaryButton}>
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

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title={label}
        icon={User}
        showAudienceTabs={false}
        showDateRange
        breadcrumb={[{ label: "Visitors", href: "/admin/users" }, { label }]}
        actions={
          ip ? (
            <>
              <Link href={carry(entityHref({ kind: "visitor", id: ip }))} className={secondaryButton}>
                <ListTree {...ICON} />
                View sessions
              </Link>
              <Link href={carry(conversationsHref({ ip }))} className={secondaryButton}>
                <MessagesSquare {...ICON} />
                Conversations
              </Link>
            </>
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <StatCard title="Sessions" value={user.sessions ?? 0} icon={MessagesSquare} />
          <StatCard title="Queries" value={user.queries ?? 0} icon={Search} />
          <StatCard title="Feedback" value={user.feedback_count ?? 0} icon={ThumbsUp} />
          <StatCard title="First seen" value={formatDateTime(user.first_seen)} subtitle={`Last active ${formatDateTime(user.last_active)}`} icon={Clock} />
          <KpiTrendCard
            label="Cost"
            value={formatUsd(totalCost)}
            trend={costTrend}
            icon={CircleDollarSign}
            hint={timelineAbsent ? "Per-visitor cost is not available yet" : (range.label ?? undefined)}
          />
        </div>

        <ChartCard
          title="Activity timeline"
          subtitle="Sessions on a time axis; select a bar to open the conversation"
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
              start: new Date(b.startedAt).toLocaleString(),
              duration: formatHours(Math.max(0, b.endedAt - b.startedAt) / HOUR_MS),
            })),
          }}
        >
          <Waterfall
            data={steps}
            height={Math.max(120, steps.length * 28)}
            label="Sessions over time"
            formatValue={formatHours}
            onSelect={(step) => {
              const s = steps.find((x) => x === step);
              if (s) router.push(carry(entityHref({ kind: "conversation", id: s.sessionId })));
            }}
          />
        </ChartCard>

        <TableCard
          title="Sessions"
          description="Conversations started from this visitor."
          toolbar={
            <TableToolbar
              search={convSearch}
              onSearchChange={(v) => {
                setConvSearch(v);
                setConvPage(1);
              }}
              searchPlaceholder="Search sessions…"
            />
          }
          pagination={
            <Pagination
              page={convPage}
              pageCount={Math.ceil(filteredConversations.length / convPageSize)}
              totalItems={filteredConversations.length}
              pageSize={convPageSize}
              onPageChange={setConvPage}
              onPageSizeChange={setConvPageSize}
            />
          }
        >
          <DataTable
            columns={convColumns}
            data={pagedConversations}
            keyField="id"
            onRowClick={(c) => router.push(carry(entityHref({ kind: "conversation", id: c.id })))}
            emptyMessage="No sessions found for this visitor."
          />
        </TableCard>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <SectionCard
            title="Queries"
            description="Most recent first; select one to open the query inspector."
            className="xl:col-span-2"
          >
            {timeline.isLoading ? (
              <SkeletonCard label="Loading queries" />
            ) : timeline.error ? (
              <ErrorState compact message={errorMessage(timeline.error, "Couldn't load queries.")} onRetry={() => void timeline.refetch()} />
            ) : !tl ? (
              <EmptyState compact icon={Search} title="Per-visitor queries are not available yet" />
            ) : (
              <DataTable
                columns={queryColumns}
                data={tl.queries}
                keyField="request_id"
                onRowClick={(q) => setInspect(q.request_id)}
                emptyMessage="No queries in this range."
              />
            )}
          </SectionCard>

          <SectionCard title="Feedback" description="Ratings this visitor gave.">
            {timeline.isLoading ? (
              <SkeletonCard label="Loading feedback" />
            ) : !tl ? (
              <EmptyState compact icon={MessageSquareText} title="Per-visitor feedback is not available yet" />
            ) : tl.feedback.length === 0 ? (
              <EmptyState compact icon={MessageSquareText} title="No feedback in this range" />
            ) : (
              <ul className="space-y-3" aria-label="Visitor feedback">
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
