"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CircleDollarSign, Clock, FlaskConical, Hash, MessagesSquare, Search, User } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { StatCard } from "@/components/admin/insights/StatCard";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { NO_IP_HINT } from "@/lib/audience-routes";
import { ChartCard } from "@/components/charts/ChartCard";
import { StackedBar, type BarSegment } from "@/components/charts/StackedBar";
import { Waterfall, type WaterfallStep } from "@/components/charts/Waterfall";
import { EntityLink } from "@/components/ui/EntityLink";
import { entityHref } from "@/lib/entity-links";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageShellSkeleton } from "@/components/ui/PageShellSkeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, secondaryButton } from "@/components/ui/styles";
import { HeaderIconButton } from "@/components/ui/HeaderIconButton";
import {
  ToneChip,
  errorMessage,
  formatMs,
  formatUsd,
  outcomeTone,
  ratingTone,
} from "@/components/admin/entity/shared";
import { useAuth } from "@/lib/auth-context";
import { useCan } from "@/lib/use-can";
import { useApiMutation, useApiQuery } from "@/lib/query-cache";
import { usePrefetchQueryDetails, usePreloadQueryInspector } from "@/lib/use-usage-data";
import { conversationQuery } from "@/lib/audience-data";
import { useDeepLink } from "@/lib/use-deep-link";
import { usePrefersReducedMotion } from "@/lib/motion";
import { AUDIENCE_PAGE_LABEL, conversationsHref, useCarryHref, visitorDetailHref } from "@/lib/entity-routes";
import { useClientTable } from "@/lib/use-client-table";
import { buildTurns, type TurnView } from "@/components/admin/entity/turns";
import {
  OUTCOMES,
  OUTCOME_LABELS,
  STAGES,
  STAGE_LABELS,
  intParam,
  isAbsent,
  promoteTurnToTest,
  type ConversationDetail,
} from "@/lib/entity-stats-api";

const QueryInspectorDrawer = dynamic(
  () => import("@/components/admin/usage/QueryInspectorDrawer").then((mod) => mod.QueryInspectorDrawer),
  { ssr: false },
);

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;

const OUTCOME_FILTER = [{ label: "All outcomes", value: "all" }, ...OUTCOMES.map((o) => ({ label: OUTCOME_LABELS[o], value: o }))];
const turnText = (t: TurnView) => `${t.question ?? ""} ${t.answer ?? ""}`;

type TokenRow = { turn: string; turnIndex: number; prompt: number; cached_prompt: number; completion: number; reasoning: number };

function ConversationDetailContent() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params?.id ?? "");
  const { token } = useAuth();
  const carry = useCarryHref();
  const toast = useToast();
  const canPromote = useCan("feedback:triage");
  const reducedMotion = usePrefersReducedMotion();
  const { get, set } = useDeepLink();
  const focusTurn = intParam(get("turn"));
  const inspect = get("inspect");

  const detailQ = conversationQuery(token ?? "", id, focusTurn);
  const detailKey = detailQ.key;
  const detail = useApiQuery(token && id ? detailKey : null, detailQ.fetch, {
    revalidateOnFocus: false,
  });
  const turns = useMemo(() => buildTurns(detail.data), [detail.data]);
  const hasDetail = (detail.data?.turns?.length ?? 0) > 0;
  const conversation = detail.data?.conversation;

  const [outcome, setOutcome] = useState("all");
  const outcomeFilter = useCallback((t: TurnView) => outcome === "all" || t.detail?.outcome === outcome, [outcome]);
  const turnsTable = useClientTable(turns, { searchText: turnText, filter: outcomeFilter });
  const { filtered: filteredTurns, pageSize: turnsPageSize, setPage: setTurnsPage } = turnsTable;
  // While idle: the query inspector of every turn on screen (one request) and
  // the inspector's code, so opening a turn's inspector is instant.
  const turnRequestIds = useMemo(
    () => turnsTable.paged.map((t) => t.requestId).filter((r): r is string => Boolean(r)),
    [turnsTable.paged],
  );
  usePrefetchQueryDetails(token, turnRequestIds, detail.updatedAt);
  usePreloadQueryInspector();

  // `?turn=` opens the page that holds the turn (then the effect below scrolls to it).
  useEffect(() => {
    if (focusTurn === null) return;
    const idx = filteredTurns.findIndex((t) => t.turnIndex === focusTurn);
    if (idx >= 0) setTurnsPage(Math.floor(idx / turnsPageSize) + 1);
  }, [focusTurn, filteredTurns, turnsPageSize, setTurnsPage]);

  const selected = useMemo(
    () => turns.find((t) => t.turnIndex === focusTurn) ?? turns.find((t) => t.detail !== null) ?? null,
    [focusTurn, turns],
  );

  // `?turn=` scrolls to and highlights the turn once it is rendered.
  useEffect(() => {
    if (focusTurn === null || turns.length === 0) return;
    const el = document.getElementById(`turn-${focusTurn}`);
    if (!el) return;
    el.scrollIntoView?.({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    el.focus({ preventScroll: true });
  }, [focusTurn, turns.length, turnsTable.page, reducedMotion]);

  const selectTurn = useCallback((turnIndex: number) => set({ turn: turnIndex }), [set]);
  const openInspector = useCallback((requestId: string | null) => {
    if (requestId) set({ inspect: requestId }, { history: "push" });
  }, [set]);

  const promote = useApiMutation<TurnView, Awaited<ReturnType<typeof promoteTurnToTest>>, ConversationDetail>({
    mutationFn: (turn) => promoteTurnToTest(token ?? "", turn.assistantMessageId ?? ""),
    optimistic: {
      key: detailKey,
      update: (current, turn) =>
        current
          ? {
              ...current,
              turns: current.turns?.map((t) =>
                t.turn_index === turn.turnIndex ? { ...t, promoted_case_id: t.promoted_case_id ?? "pending" } : t,
              ),
            }
          : (current as unknown as ConversationDetail),
    },
    onSuccess: (result, turn) =>
      toast.show({ title: `Turn ${turn.turnIndex + 1} promoted to a test case`, description: result.case_id, tone: "success" }),
    onError: (err) =>
      toast.show({ title: "Couldn't promote this turn", description: errorMessage(err, "Please try again.") ?? undefined, tone: "error" }),
  });

  const stageSteps = useMemo<WaterfallStep[]>(() => {
    const stages = selected?.detail?.stages_ms;
    if (!stages) return [];
    return STAGES.filter((s) => typeof stages[s] === "number").map((s) => ({ label: STAGE_LABELS[s], value: stages[s] ?? 0 }));
  }, [selected]);

  const tokenRows = useMemo<TokenRow[]>(
    () =>
      turns
        .filter((t) => t.detail?.tokens)
        .map((t) => {
          const tk = t.detail?.tokens;
          const cached = tk?.cached_prompt ?? 0;
          const reasoning = tk?.reasoning ?? 0;
          return {
            turn: `#${t.turnIndex + 1}`,
            turnIndex: t.turnIndex,
            prompt: Math.max(0, (tk?.prompt ?? 0) - cached),
            cached_prompt: cached,
            completion: Math.max(0, (tk?.completion ?? 0) - reasoning),
            reasoning,
          };
        }),
    [turns],
  );

  const totalCost = hasDetail ? turns.reduce((sum, t) => sum + (t.detail?.cost_usd ?? 0), 0) : null;
  const totalTokens = hasDetail ? turns.reduce((sum, t) => sum + (t.detail?.tokens?.total ?? 0), 0) : null;

  if (detail.isLoading) {
    return (
      <PageShellSkeleton label="Loading conversation" />
    );
  }

  if (!detail.data || !conversation) {
    return (
      <div className="flex min-h-screen flex-col bg-surface-page text-fg">
        <DashboardHeader
          title="Conversation"
          icon={MessagesSquare}
          showAudienceTabs={false}
          breadcrumb={[{ label: AUDIENCE_PAGE_LABEL, href: carry(conversationsHref()) }, { label: id || "Unknown" }]}
        />
        <div className="p-8">
          {isAbsent(detail.error) ? (
            <EmptyState
              title="Conversation not found"
              description="It may have expired or been deleted."
              action={
                <Link href={carry(conversationsHref())} className={secondaryButton}>
                  Back to Conversations
                </Link>
              }
            />
          ) : (
            <ErrorState message={errorMessage(detail.error, "Failed to load conversation.")} onRetry={() => void detail.refetch()} />
          )}
        </div>
      </div>
    );
  }

  const title = conversation.title || "Untitled Conversation";

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title={title}
        icon={MessagesSquare}
        showAudienceTabs={false}
        breadcrumb={[{ label: AUDIENCE_PAGE_LABEL, href: carry(conversationsHref()) }, { label: title }]}
        actions={
          conversation.created_ip ? (
            <HeaderIconButton
              href={carry(visitorDetailHref(`client-${conversation.created_ip}`))}
              icon={User}
              label="View visitor"
              tooltip="Open this conversation's visitor"
            />
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" data-tour="conversations-detail-stats">
          <StatCard
            title="Visitor"
            value={
              conversation.created_ip ? (
                <Link
                  href={carry(visitorDetailHref(`client-${conversation.created_ip}`))}
                  className={`rounded font-mono text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
                >
                  {conversation.created_ip}
                </Link>
              ) : (
                <span className="text-fg-subtle" title={NO_IP_HINT}>
                  Not recorded
                </span>
              )
            }
            subtitle={conversation.created_ip ? "Client IP · select to open the visitor" : "No client IP was captured"}
            icon={User}
            badge={
              <Link
                href={entityHref({ kind: "analytics", tab: "conversations", q: conversation.id })}
                className={`rounded text-[11px] font-semibold text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
              >
                In Analytics
              </Link>
            }
          />
          <StatCard
            title="Started"
            value={<RelativeTime value={conversation.created_at} />}
            subtitle={<RelativeTime value={conversation.last_activity_at} prefix="Last activity " />}
            icon={Clock}
          />
          <StatCard title="Turns" value={turns.length} subtitle={`${detail.data.message_count} messages`} icon={Hash} />
          <StatCard
            title="Cost"
            value={formatUsd(totalCost)}
            subtitle={totalTokens !== null ? `${totalTokens.toLocaleString()} tokens` : "Per-turn cost not available yet"}
            icon={CircleDollarSign}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="conversations-detail-charts">
          <ChartCard
            title={selected ? `Stage latency · turn ${selected.turnIndex + 1}` : "Stage latency"}
            subtitle="Select a stage to open the query inspector"
            empty={stageSteps.length === 0}
            emptyMessage={hasDetail ? "No stage timings for this turn" : "Per-turn stage timings are not available yet"}
            table={{
              columns: [
                { key: "stage", label: "Stage" },
                { key: "ms", label: "Duration", numeric: true },
              ],
              rows: stageSteps.map((s) => ({ stage: s.label, ms: formatMs(s.value) })),
            }}
          >
            <Waterfall
              data={stageSteps}
              formatValue={formatMs}
              onSelect={() => openInspector(selected?.requestId ?? null)}
              xAxisLabel="Milliseconds from request start"
              yAxisLabel="Stage"
              valueLabel="Duration"
              label="Stage latency for the selected turn"
            />
          </ChartCard>

          <ChartCard
            title="Tokens per turn"
            subtitle="Select a turn to jump to it"
            empty={tokenRows.length === 0}
            emptyMessage={hasDetail ? "No token counts" : "Per-turn tokens are not available yet"}
            table={{
              columns: [
                { key: "turn", label: "Turn" },
                { key: "prompt", label: "Prompt", numeric: true },
                { key: "cached_prompt", label: "Cached", numeric: true },
                { key: "completion", label: "Completion", numeric: true },
                { key: "reasoning", label: "Reasoning", numeric: true },
              ],
              rows: tokenRows,
            }}
          >
            <StackedBar
              data={tokenRows}
              xKey="turn"
              series={[
                { key: "prompt", label: "Prompt" },
                { key: "cached_prompt", label: "Cached prompt" },
                { key: "completion", label: "Completion" },
                { key: "reasoning", label: "Reasoning" },
              ]}
              onSelect={(seg: BarSegment<TokenRow>) => selectTurn(seg.row.turnIndex)}
              xAxisLabel="Turn"
              yAxisLabel="Tokens"
              label="Tokens per turn"
            />
          </ChartCard>
        </div>

        <div data-tour="conversations-detail-turns">
        <TableCard
          toolbar={
            <TableToolbar
              title="Turns"
              countLabel={`${turnsTable.total.toLocaleString()} of ${turns.length.toLocaleString()} turn${turns.length === 1 ? "" : "s"}`}
              search={turnsTable.search}
              onSearchChange={turnsTable.setSearch}
              searchPlaceholder="Search questions and answers…"
              filters={[
                {
                  id: "outcome",
                  label: "Outcome",
                  value: outcome,
                  options: OUTCOME_FILTER,
                  onChange: (v) => {
                    setOutcome(v || "all");
                    turnsTable.setPage(1);
                  },
                },
              ]}
            />
          }
          pagination={
            turns.length > 0 ? (
              <Pagination
                page={turnsTable.page}
                pageCount={turnsTable.pageCount}
                totalItems={turnsTable.total}
                pageSize={turnsTable.pageSize}
                onPageChange={turnsTable.setPage}
                onPageSizeChange={turnsTable.setPageSize}
              />
            ) : undefined
          }
        >
        <section aria-label="Turns" className="space-y-4 p-4 sm:p-5">
          {turns.length === 0 ? (
            <EmptyState title="No messages found" />
          ) : turnsTable.paged.length === 0 ? (
            <EmptyState compact title="No turns match the search or outcome filter" />
          ) : (
            turnsTable.paged.map((t) => (
              <TurnCard
                key={t.turnIndex}
                turn={t}
                conversationId={conversation.id}
                highlighted={t.turnIndex === focusTurn}
                canPromote={canPromote}
                promoting={promote.isPending}
                onSelect={() => selectTurn(t.turnIndex)}
                onInspect={() => openInspector(t.requestId)}
                onPromote={() => void promote.mutate(t)}
              />
            ))
          )}
        </section>
        </TableCard>
        </div>
      </div>

      {inspect && token ? (
        <QueryInspectorDrawer requestId={inspect} token={token} onClose={() => set({ inspect: null })} />
      ) : null}
    </div>
  );
}

interface TurnCardProps {
  turn: TurnView;
  conversationId: string;
  highlighted: boolean;
  canPromote: boolean;
  promoting: boolean;
  onSelect: () => void;
  onInspect: () => void;
  onPromote: () => void;
}

function TurnCard({ turn, conversationId, highlighted, canPromote, promoting, onSelect, onInspect, onPromote }: TurnCardProps) {
  const d = turn.detail;
  const promoted = Boolean(d?.promoted_case_id);
  const labels = d?.labels;
  return (
    <article
      id={`turn-${turn.turnIndex}`}
      tabIndex={-1}
      aria-label={`Turn ${turn.turnIndex + 1}`}
      data-highlighted={highlighted || undefined}
      className={`rounded-xl border bg-surface-card p-5 transition duration-150 motion-reduce:transition-none focus:outline-none ${
        highlighted
          ? "border-brand-500 ring-2 ring-brand-500/40 shadow-md"
          : "border-line hover:border-brand-300 hover:shadow-md dark:hover:border-brand-700/60"
      }`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onSelect}
          className={`rounded text-xs font-semibold text-fg hover:text-accent-fg ${focusRing}`}
          aria-pressed={highlighted}
        >
          Turn {turn.turnIndex + 1}
        </button>
        {d?.outcome ? <ToneChip tone={outcomeTone(d.outcome)}>{d.outcome}</ToneChip> : null}
        {turn.grounded === false ? <ToneChip tone="warn">not grounded</ToneChip> : null}
        {turn.guardrailFlags.map((f) => (
          <ToneChip key={f} tone="danger">
            {f}
          </ToneChip>
        ))}
        <RelativeTime value={turn.createdAt} className="text-[11px] text-fg-subtle" />
        {d ? (
          <span className="text-[11px] text-fg-subtle">
            · {formatMs(d.total_ms)} · {formatUsd(d.cost_usd)}
          </span>
        ) : null}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {turn.requestId ? (
            <>
              <button type="button" onClick={onInspect} className={secondaryButton} aria-label={`Inspect turn ${turn.turnIndex + 1}`}>
                <Search {...ICON} />
                Inspect
              </button>
              <EntityLink entity={{ kind: "trace", id: turn.requestId }} mono={false} copyable={false}>
                Trace
              </EntityLink>
            </>
          ) : null}
          {canPromote && turn.assistantMessageId ? (
            <button
              type="button"
              onClick={onPromote}
              disabled={promoting || promoted}
              className={secondaryButton}
              aria-label={`Promote turn ${turn.turnIndex + 1} to test`}
            >
              <FlaskConical {...ICON} />
              {promoted ? "Promoted" : "Promote to test"}
            </button>
          ) : null}
        </div>
      </header>

      <div className="mt-4 space-y-3">
        <div className="rounded-lg bg-brand-50 px-4 py-3 dark:bg-brand-950/40">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">User</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-fg">{turn.question ?? "—"}</p>
        </div>
        <div className="rounded-lg border border-line bg-surface-muted px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">Assistant</p>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-fg">{turn.answer ?? "—"}</p>
        </div>
      </div>

      {d && (d.feedback.length > 0 || labels || d.sources.length > 0) ? (
        <footer data-tour="conversations-detail-turn-footer" className="mt-4 grid grid-cols-1 gap-3 border-t border-line pt-3 text-[11px] md:grid-cols-3">
          <div>
            <p className="mb-1 font-semibold text-fg-muted">Feedback</p>
            {d.feedback.length === 0 ? (
              <p className="text-fg-subtle">None</p>
            ) : (
              <ul className="space-y-1">
                {d.feedback.map((f) => (
                  <li key={f.id} className="flex flex-wrap items-center gap-1.5">
                    <ToneChip tone={ratingTone(f.rating)}>{f.rating} / 5</ToneChip>
                    {f.reason_tags.map((r) => (
                      <ToneChip key={r} tone="neutral">
                        {r.replace(/_/g, " ")}
                      </ToneChip>
                    ))}
                    {f.comment ? <span className="text-fg-muted">“{f.comment}”</span> : null}
                  </li>
                ))}
                {turn.assistantMessageId ? (
                  <li>
                    <EntityLink entity={{ kind: "feedback", id: turn.assistantMessageId }} mono={false} copyable={false}>
                      Open in Feedback
                    </EntityLink>
                  </li>
                ) : null}
              </ul>
            )}
          </div>
          <div>
            <p className="mb-1 font-semibold text-fg-muted">Insight labels</p>
            {labels && (labels.intent || labels.quality || labels.issue) ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {labels.intent ? (
                  <EntityLink entity={{ kind: "analytics", intent: labels.intent }} mono={false} copyable={false}>
                    {labels.intent}
                  </EntityLink>
                ) : null}
                {labels.quality ? (
                  <EntityLink entity={{ kind: "analytics", quality: labels.quality }} mono={false} copyable={false}>
                    {labels.quality}
                  </EntityLink>
                ) : null}
                {labels.issue ? <ToneChip tone="warn">{labels.issue}</ToneChip> : null}
              </div>
            ) : (
              <p className="text-fg-subtle">Not classified yet</p>
            )}
          </div>
          <div>
            <p className="mb-1 font-semibold text-fg-muted">Sources</p>
            {d.sources.length === 0 ? (
              <p className="text-fg-subtle">No cited sources</p>
            ) : (
              <ul className="space-y-1">
                {d.sources.map((s, i) => (
                  <li key={`${s.chunk_id ?? s.document_id ?? i}`} className="min-w-0">
                    {s.document_id && s.chunk_id ? (
                      <EntityLink entity={{ kind: "chunk", id: s.chunk_id, documentId: s.document_id }} mono={false} copyable={false}>
                        {s.source_name ?? "Document"}
                        {s.chunk_index !== null ? ` · chunk ${s.chunk_index + 1}` : ""}
                      </EntityLink>
                    ) : s.document_id ? (
                      <EntityLink entity={{ kind: "document", id: s.document_id }} mono={false} copyable={false}>
                        {s.source_name ?? "Document"}
                      </EntityLink>
                    ) : (
                      <span className="text-fg-muted">{s.source_name ?? "Unknown source"}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </footer>
      ) : null}
      <span className="sr-only">Conversation {conversationId}</span>
    </article>
  );
}

export default function ConversationDetailPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading conversation…</div>}>
      <ConversationDetailContent />
    </React.Suspense>
  );
}
