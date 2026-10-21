"use client";

import React, { useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { CalendarClock, Coins, Hash, MessagesSquare, Timer, ThumbsDown, ThumbsUp, User } from "lucide-react";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { CopyButton } from "@/components/ui/CopyButton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { focusRing } from "@/components/ui/styles";
import { ToneChip, errorMessage, formatUsd } from "@/components/admin/entity/shared";
import { ConversationThread, ThreadSkeleton, ratingKind, turnStatus } from "@/components/admin/entity/ConversationThread";
import { buildTurns, type TurnView } from "@/components/admin/entity/turns";
import { entityHref } from "@/lib/entity-links";
import { useCarryHref, visitorDetailHref } from "@/lib/entity-routes";
import { useApiQuery } from "@/lib/query-cache";
import { conversationQuery } from "@/lib/audience-data";
import { isTypingTarget } from "@/lib/shortcuts";
import { toServerDate } from "@/lib/time";
import type { Audience } from "@/lib/types";

const AUDIENCE_LABEL: Partial<Record<Audience, string>> = { users: "User", testing: "Testing" };

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** `<1 min`, `18 min`, `2h 5m`, `3d 4h`. */
export function formatDuration(ms: number): string {
  if (ms < MINUTE_MS) return "<1 min";
  if (ms < HOUR_MS) return `${Math.floor(ms / MINUTE_MS)} min`;
  if (ms < DAY_MS) return `${Math.floor(ms / HOUR_MS)}h ${Math.floor((ms % HOUR_MS) / MINUTE_MS)}m`;
  return `${Math.floor(ms / DAY_MS)}d ${Math.floor((ms % DAY_MS) / HOUR_MS)}h`;
}

interface Summary {
  outcomes: [string, number][];
  up: number;
  down: number;
  mid: number;
  tokens: number | null;
  cost: number | null;
}

/** Conversation totals from the per-turn detail (null totals when the payload has none). */
function summarize(turns: readonly TurnView[]): Summary {
  const outcomes = new Map<string, number>();
  const s: Summary = { outcomes: [], up: 0, down: 0, mid: 0, tokens: null, cost: null };
  for (const t of turns) {
    const d = t.detail;
    if (!d) continue;
    if (d.outcome) outcomes.set(d.outcome, (outcomes.get(d.outcome) ?? 0) + 1);
    for (const f of d.feedback) s[ratingKind(f.rating)] += 1;
    if (d.tokens) s.tokens = (s.tokens ?? 0) + d.tokens.total;
    if (d.cost_usd != null) s.cost = (s.cost ?? 0) + d.cost_usd;
  }
  s.outcomes = [...outcomes.entries()];
  return s;
}

const CHIP =
  "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-full border border-line bg-surface-card px-2 py-0.5 text-[11px] font-medium tabular-nums text-fg-muted dark:border-white/10";
const ICON = { className: "h-3 w-3 shrink-0", strokeWidth: 1.75, "aria-hidden": true } as const;

function MetaChip({ icon: Icon, title, children }: { icon: typeof User; title: string; children: React.ReactNode }) {
  return (
    <li className={CHIP} title={title}>
      <Icon {...ICON} />
      {children}
    </li>
  );
}

interface ConversationPreviewDrawerProps {
  token: string;
  conversationId: string | null;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  /** Audience of the list the drawer opened from ("User" / "Testing" chip; none for the overview). */
  audience?: Audience;
}

/**
 * One conversation over the list as a compact chat thread. The list page
 * stays mounted underneath, so its page, sort and filters are unchanged when
 * the drawer closes; "Open full view" goes to the detail page. Rows warmed by
 * the batch render straight from cache (no skeleton). ↑/↓ (or k/j) move to the
 * previous / next conversation, Esc closes.
 */
export function ConversationPreviewDrawer({ token, conversationId, onClose, onPrev, onNext, audience }: ConversationPreviewDrawerProps) {
  const q = conversationQuery(token, conversationId ?? "");
  const detail = useApiQuery(conversationId ? q.key : null, q.fetch);
  const turns = useMemo(() => buildTurns(detail.data), [detail.data]);
  const summary = useMemo(() => summarize(turns), [turns]);
  const carry = useCarryHref();
  const open = conversationId !== null;

  const nav = useRef({ onPrev, onNext });
  nav.current = { onPrev, onNext };
  useEffect(() => {
    if (!open) return undefined;
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (isTypingTarget(event.target)) return;
      // Arrow keys scroll the transcript while focus is inside it.
      if (event.target instanceof Element && event.target.closest("[data-preview-transcript]")) return;
      const back = event.key === "ArrowUp" || event.key === "k";
      const forward = event.key === "ArrowDown" || event.key === "j";
      const go = back ? nav.current.onPrev : forward ? nav.current.onNext : undefined;
      if (!go) return;
      event.preventDefault();
      go();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const header = detail.data?.conversation;
  const title = header?.title || turns.find((t) => t.question)?.question || "Conversation";
  const fullViewHref = conversationId ? entityHref({ kind: "conversation", id: conversationId }) : "";
  const started = toServerDate(header?.created_at);
  const last = toServerDate(header?.last_activity_at);
  const durationMs = started && last ? Math.max(0, last.getTime() - started.getTime()) : null;
  const visitorLabel = header?.user_email || header?.created_ip || null;
  const audienceLabel = audience ? AUDIENCE_LABEL[audience] : undefined;
  const rated = summary.up + summary.down + summary.mid > 0;

  const meta = header ? (
    <ul
      aria-label="Conversation summary"
      className="flex flex-wrap items-center gap-1.5 border-b border-line-brand/70 bg-surface-studio/40 px-4 py-2.5 dark:border-brand-500/20 sm:px-6"
    >
      {visitorLabel ? (
        <li className="min-w-0 max-w-full">
          {header.created_ip ? (
            <Link
              href={carry(visitorDetailHref(`client-${header.created_ip}`))}
              title="Open this conversation's visitor"
              className={`${CHIP} border-brand-500/30 text-brand-700 hover:border-brand-500/60 dark:text-brand-300 ${focusRing}`}
            >
              <User {...ICON} />
              <span className="truncate">{visitorLabel}</span>
            </Link>
          ) : (
            <span className={CHIP} title="Visitor">
              <User {...ICON} />
              <span className="truncate">{visitorLabel}</span>
            </span>
          )}
        </li>
      ) : null}
      {audienceLabel ? (
        <li title="Audience">
          <ToneChip tone={audience === "testing" ? "warn" : "neutral"}>{audienceLabel}</ToneChip>
        </li>
      ) : null}
      {started ? (
        <MetaChip icon={CalendarClock} title="Started">
          <RelativeTime value={header.created_at} prefix="Started " />
        </MetaChip>
      ) : null}
      {durationMs !== null ? (
        <MetaChip icon={Timer} title="Duration (first to last activity)">
          {formatDuration(durationMs)}
        </MetaChip>
      ) : null}
      <MetaChip icon={MessagesSquare} title="Turns">
        {turns.length} {turns.length === 1 ? "turn" : "turns"}
      </MetaChip>
      {summary.outcomes.map(([outcome, count]) => {
        const status = turnStatus(outcome);
        return status ? (
          <li key={outcome} title={`${count} ${outcome} ${count === 1 ? "turn" : "turns"}`}>
            <ToneChip tone={status.tone}>
              {status.label}
              {summary.outcomes.length > 1 || count > 1 ? ` ×${count}` : ""}
            </ToneChip>
          </li>
        ) : null;
      })}
      {rated ? (
        <li className={CHIP} title="Ratings">
          {summary.up ? (
            <span className="inline-flex items-center gap-0.5 text-status-ok-fg" aria-label={`${summary.up} thumbs up`}>
              <ThumbsUp {...ICON} />
              {summary.up}
            </span>
          ) : null}
          {summary.down ? (
            <span className="inline-flex items-center gap-0.5 text-status-danger-fg" aria-label={`${summary.down} thumbs down`}>
              <ThumbsDown {...ICON} />
              {summary.down}
            </span>
          ) : null}
          {summary.mid ? <span className="text-status-warn-fg">{summary.mid} neutral</span> : null}
        </li>
      ) : null}
      {summary.tokens !== null ? (
        <MetaChip icon={Hash} title="Total tokens">
          {summary.tokens.toLocaleString()} tokens
        </MetaChip>
      ) : null}
      {summary.cost !== null ? (
        <MetaChip icon={Coins} title="Total cost">
          {formatUsd(summary.cost)}
        </MetaChip>
      ) : null}
    </ul>
  ) : undefined;

  return (
    <DrawerShell
      open={open}
      onClose={onClose}
      title={
        <span className="line-clamp-2 min-w-0 break-words leading-5" title={title}>
          {title}
        </span>
      }
      description={conversationId ? <span className="font-mono">{conversationId}</span> : undefined}
      headerExtra={conversationId ? <CopyButton value={conversationId} label="Copy conversation id" className="mr-0.5" /> : undefined}
      toolbar={meta}
      fullViewHref={fullViewHref || undefined}
      onPrev={onPrev}
      onNext={onNext}
      closeLabel="Close preview"
      widthClassName="max-w-[min(640px,92vw)]"
      bodyClassName="bg-surface-page/40 px-4 py-5 sm:px-6"
    >
      {detail.isLoading ? (
        <ThreadSkeleton />
      ) : detail.error && !detail.data ? (
        <ErrorState compact message={errorMessage(detail.error, "Couldn't load this conversation.")} onRetry={() => void detail.refetch()} />
      ) : turns.length === 0 ? (
        <EmptyState compact title="No turns in this conversation" description="Messages appear here once the visitor asks a question." />
      ) : (
        <ConversationThread turns={turns} conversationId={conversationId ?? ""} fullViewHref={fullViewHref} />
      )}
    </DrawerShell>
  );
}
