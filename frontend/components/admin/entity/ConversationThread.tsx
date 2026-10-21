"use client";

import React, { useId, useState } from "react";
import Link from "next/link";
import { Activity, ChevronDown, ChevronUp, Clock, Coins, FileText, Hash, ThumbsDown, ThumbsUp } from "lucide-react";
import { EntityLink } from "@/components/ui/EntityLink";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { Skeleton, SkeletonGroup } from "@/components/ui/Skeleton";
import { focusRing } from "@/components/ui/styles";
import { ToneChip, formatMs, formatUsd } from "@/components/admin/entity/shared";
import type { TurnView } from "@/components/admin/entity/turns";
import { entityHref } from "@/lib/entity-links";
import type { TurnFeedback, TurnSource } from "@/lib/entity-stats-api";
import { stripCitationMarkers } from "@/lib/citation-markers";
import { renderFormattedText } from "@/lib/format-text";
import { useCan } from "@/lib/use-can";

/** Answers estimated past this many rendered lines start collapsed ("Show more"). */
export const LONG_ANSWER_LINES = 12;
/** Characters per rendered line in the drawer's assistant bubble (for the estimate). */
const CHARS_PER_LINE = 80;

type Tone = "ok" | "warn" | "danger" | "neutral";

/** Turn status shown in the footer (canonical outcome -> reviewer wording). */
const STATUS: Record<string, { label: string; tone: Tone }> = {
  answered: { label: "Answered", tone: "ok" },
  clarify: { label: "Clarification", tone: "neutral" },
  ungrounded: { label: "Fallback", tone: "warn" },
  llm_failed: { label: "Fallback", tone: "danger" },
  conversational: { label: "Conversational", tone: "neutral" },
  blocked: { label: "Blocked", tone: "danger" },
};

export function turnStatus(outcome: string | null | undefined): { label: string; tone: Tone } | null {
  if (!outcome) return null;
  return STATUS[outcome] ?? { label: outcome.replace(/_/g, " "), tone: "neutral" };
}

/** Thumbs up (4-5), thumbs down (1-2) or the raw score (3, legacy 1-5 scale). */
export function ratingKind(rating: number): "up" | "down" | "mid" {
  if (rating >= 4) return "up";
  if (rating <= 2) return "down";
  return "mid";
}

/** Rough rendered-line count: each source line wraps every `CHARS_PER_LINE` characters. */
export function estimateLines(text: string): number {
  return text.split("\n").reduce((n, line) => n + Math.max(1, Math.ceil(line.length / CHARS_PER_LINE)), 0);
}

const META = "inline-flex items-center gap-1 whitespace-nowrap tabular-nums";
const ICON = { className: "h-3 w-3", strokeWidth: 1.75, "aria-hidden": true } as const;
const PILL =
  "inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-brand-500/25 bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-700 transition-colors hover:border-brand-500/60 hover:bg-brand-100 dark:bg-brand-950/50 dark:text-brand-300 dark:hover:bg-brand-900/50";

/** The full answer, collapsed to about `LONG_ANSWER_LINES` lines with "Show more" when longer (never cut with an ellipsis). */
function AnswerBody({ text: raw }: { text: string }) {
  const id = useId();
  // Stored answers carry `[n]` markers but no citation list to turn into pills: drop them (OR-7).
  const text = stripCitationMarkers(raw);
  const long = estimateLines(text) > LONG_ANSWER_LINES;
  const [expanded, setExpanded] = useState(false);
  const collapsed = long && !expanded;
  return (
    <>
      <div
        id={id}
        data-collapsed={collapsed || undefined}
        className={`relative space-y-2 break-words text-[13px] leading-[1.375rem] text-fg ${collapsed ? "max-h-[16.5rem] overflow-hidden" : ""}`}
      >
        {renderFormattedText(text)}
        {collapsed ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-surface-card to-transparent"
          />
        ) : null}
      </div>
      {long ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls={id}
          className={`mt-1.5 inline-flex items-center gap-1 rounded-md text-[11px] font-semibold text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
        >
          {expanded ? <ChevronUp {...ICON} /> : <ChevronDown {...ICON} />}
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </>
  );
}

/** One pill per cited document (first chunk of each), linking to the chunk or the document. */
function SourcePills({ sources }: { sources: readonly TurnSource[] }) {
  const seen = new Set<string>();
  const pills = sources.filter((s) => {
    const key = s.document_id ?? s.source_name ?? "";
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (pills.length === 0) return null;
  return (
    <ul className="flex flex-wrap items-center gap-1" aria-label="Sources">
      {pills.map((s) => {
        const name = s.source_name ?? "Document";
        const label = (
          <>
            <FileText {...ICON} />
            <span className="truncate">{name}</span>
          </>
        );
        const title = s.section_heading ? `${name} · ${s.section_heading}` : name;
        return (
          <li key={s.document_id ?? name} className="min-w-0">
            {s.document_id ? (
              <Link
                href={
                  s.chunk_id
                    ? entityHref({ kind: "chunk", id: s.chunk_id, documentId: s.document_id })
                    : entityHref({ kind: "document", id: s.document_id })
                }
                title={title}
                className={`${PILL} ${focusRing}`}
              >
                {label}
              </Link>
            ) : (
              <span title={title} className={PILL}>
                {label}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function RatingMark({ rating }: { rating: number }) {
  const kind = ratingKind(rating);
  if (kind === "up") return <ThumbsUp className="h-3 w-3 text-status-ok-fg" strokeWidth={2} aria-label="Thumbs up" />;
  if (kind === "down") return <ThumbsDown className="h-3 w-3 text-status-danger-fg" strokeWidth={2} aria-label="Thumbs down" />;
  return <span className="font-semibold text-status-warn-fg">{rating} / 5</span>;
}

function FeedbackLine({ feedback }: { feedback: TurnFeedback }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5" data-testid="turn-feedback">
      <RatingMark rating={feedback.rating} />
      {feedback.reason_tags.map((r) => (
        <ToneChip key={r} tone="neutral">
          {r.replace(/_/g, " ")}
        </ToneChip>
      ))}
      {feedback.comment ? <span className="min-w-0 break-words text-fg-muted">“{feedback.comment}”</span> : null}
    </li>
  );
}

function TurnFooter({ turn, conversationId }: { turn: TurnView; conversationId: string }) {
  const canTrace = useCan("dashboard:read");
  const d = turn.detail;
  const status = turnStatus(d?.outcome);
  const tokens = d?.tokens?.total;
  return (
    <div className="mt-1.5 space-y-1.5 px-1 text-[11px] text-fg-subtle" data-testid="turn-footer">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <EntityLink entity={{ kind: "turn", id: String(turn.turnIndex), conversationId }} mono={false} copyable={false}>
          Turn {turn.turnIndex + 1}
        </EntityLink>
        {status ? (
          <span title={d?.outcome ?? undefined}>
            <ToneChip tone={status.tone}>{status.label}</ToneChip>
          </span>
        ) : null}
        {turn.grounded === false && d?.outcome === "answered" ? <ToneChip tone="warn">not grounded</ToneChip> : null}
        {turn.guardrailFlags.map((f) => (
          <ToneChip key={f} tone="danger">
            {f}
          </ToneChip>
        ))}
        {d?.total_ms != null ? (
          <span className={META} title="Latency">
            <Clock {...ICON} />
            {formatMs(d.total_ms)}
          </span>
        ) : null}
        {tokens != null ? (
          <span className={META} title="Tokens">
            <Hash {...ICON} />
            {tokens.toLocaleString()} tokens
          </span>
        ) : null}
        {d?.cost_usd != null ? (
          <span className={META} title="Cost">
            <Coins {...ICON} />
            {formatUsd(d.cost_usd)}
          </span>
        ) : null}
        <RelativeTime value={turn.createdAt} />
        {canTrace && turn.requestId ? (
          <Link
            href={entityHref({ kind: "trace", id: turn.requestId })}
            className={`ml-auto inline-flex items-center gap-1 rounded-md font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            <Activity {...ICON} />
            View trace
          </Link>
        ) : null}
      </div>
      {d?.sources.length ? <SourcePills sources={d.sources} /> : null}
      {d?.feedback.length ? (
        <ul className="space-y-1" aria-label="Feedback">
          {d.feedback.map((f) => (
            <FeedbackLine key={f.id} feedback={f} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function TurnItem({ turn, conversationId, fullViewHref }: { turn: TurnView; conversationId: string; fullViewHref: string }) {
  return (
    <li className="space-y-2" aria-label={`Turn ${turn.turnIndex + 1}`}>
      {turn.question !== null ? (
        <div className="flex justify-end pl-8 sm:pl-12">
          <p className="whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-brand-600 px-3.5 py-2 text-[13px] leading-5 text-white shadow-sm">
            {turn.question}
          </p>
        </div>
      ) : null}
      <div className="pr-4 sm:pr-8">
        <div className="rounded-2xl rounded-tl-sm border border-line bg-surface-card px-3.5 py-2.5 shadow-sm dark:border-white/10">
          {turn.answer !== null ? (
            <AnswerBody text={turn.answer} />
          ) : (
            <p className="text-[13px] italic text-fg-subtle">No answer recorded</p>
          )}
          {turn.truncated ? (
            <p className="mt-2 border-t border-line pt-1.5 text-[11px] text-fg-subtle">
              Long message shortened in the preview.{" "}
              <Link href={fullViewHref} className={`rounded font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}>
                Open full view
              </Link>{" "}
              for the full text.
            </p>
          ) : null}
        </div>
        <TurnFooter turn={turn} conversationId={conversationId} />
      </div>
    </li>
  );
}

/** The conversation as a compact chat thread: user bubbles right, assistant left, a metadata footer per turn. */
export function ConversationThread({
  turns,
  conversationId,
  fullViewHref,
}: {
  turns: readonly TurnView[];
  conversationId: string;
  fullViewHref: string;
}) {
  return (
    <ol className="space-y-5" aria-label="Transcript" data-preview-transcript>
      {turns.map((t) => (
        <TurnItem key={t.turnIndex} turn={t} conversationId={conversationId} fullViewHref={fullViewHref} />
      ))}
    </ol>
  );
}

/** Chat-shaped placeholder, shown only while nothing is cached for the conversation. */
export function ThreadSkeleton() {
  return (
    <SkeletonGroup label="Loading conversation" className="space-y-5">
      {[0, 1].map((i) => (
        <div key={i} className="space-y-2">
          <div className="flex justify-end">
            <Skeleton className="h-9 w-3/5 rounded-2xl" />
          </div>
          <Skeleton className="h-24 w-11/12 rounded-2xl" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </SkeletonGroup>
  );
}
