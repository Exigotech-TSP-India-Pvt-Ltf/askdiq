"use client";

import React, { useEffect, useId, useState } from "react";
import { CheckCircle2, FlaskConical, RotateCcw, Star, XCircle } from "lucide-react";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { primaryButton, secondaryButton } from "@/components/ui/styles";
import {
  MAX_CORRECTED_ANSWER_CHARS,
  MAX_REVIEW_NOTE_CHARS,
  TRIAGE_LABELS,
  reasonLabel,
  type FeedbackItem,
} from "@/lib/feedback-api";
import { outcomeLabel } from "@/lib/feedback-filters";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { ACTION_LABELS, STATUS_BADGE, canTransition, ratingTone, type TriageAction } from "./triage";
import { renderCitedText } from "@/lib/format-text";

export interface FeedbackDrawerProps {
  item: FeedbackItem | null;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  canTriage: boolean;
  busy?: boolean;
  onAction: (item: FeedbackItem, action: TriageAction) => void;
  /** Dismissed -> new. */
  onRestore?: (item: FeedbackItem) => void;
  onSaveNote: (item: FeedbackItem, note: string) => Promise<void>;
  onSaveCorrectedAnswer: (item: FeedbackItem, answer: string) => Promise<void>;
}

const ACTION_ICONS: Record<TriageAction, React.ElementType> = {
  reviewed: CheckCircle2,
  dismissed: XCircle,
  promoted: FlaskConical,
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">{title}</h3>
      {children}
    </section>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface-muted/40 px-3 py-2">
      <dt className="text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 text-xs font-medium text-fg">{value}</dd>
    </div>
  );
}

function fmtNumber(value: number | null, digits: number, suffix = ""): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}${suffix}`;
}

function TextEditor({
  label,
  value,
  max,
  placeholder,
  saveLabel,
  disabled,
  onSave,
}: {
  label: string;
  value: string;
  max: number;
  placeholder: string;
  saveLabel: string;
  disabled: boolean;
  onSave: (text: string) => Promise<void>;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  useEffect(() => setDraft(value), [value]);
  const dirty = draft.trim() !== value.trim();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px] text-fg-muted">
        <label htmlFor={id} className="font-medium">
          {label}
        </label>
        <span className="tabular-nums text-fg-subtle">
          {draft.length}/{max}
        </span>
      </div>
      <textarea
        id={id}
        value={draft}
        disabled={disabled}
        maxLength={max}
        onChange={(e) => setDraft(e.target.value.slice(0, max))}
        rows={4}
        placeholder={placeholder}
        className="w-full resize-y rounded-lg border border-line bg-surface-card p-2.5 text-xs text-fg placeholder:text-fg-subtle outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
      />
      <div className="flex justify-end">
        <button
          type="button"
          disabled={disabled || saving || !dirty}
          onClick={async () => {
            setSaving(true);
            try {
              await onSave(draft.trim());
            } finally {
              setSaving(false);
            }
          }}
          className={secondaryButton}
        >
          {saving ? "Saving…" : saveLabel}
        </button>
      </div>
    </div>
  );
}

/**
 * Feedback detail drawer: full exchange, sources with scores,
 * flags, labels, the user's rating and reasons, reviewer actions and the
 * "Write the correct answer" field (the `chosen` side of a preference pair).
 */
export function FeedbackDrawer({
  item,
  onClose,
  onPrev,
  onNext,
  canTriage,
  busy = false,
  onAction,
  onRestore,
  onSaveNote,
  onSaveCorrectedAnswer,
}: FeedbackDrawerProps) {
  return (
    <DrawerShell
      open={item !== null}
      onClose={onClose}
      onPrev={onPrev}
      onNext={onNext}
      title={
        item ? (
          <>
            <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${ratingTone(item.rating)}`}>
              <Star className="h-3 w-3 fill-current" strokeWidth={1.75} aria-hidden="true" />
              {item.rating}
            </span>
            <span className="truncate">Feedback on {item.user_query ? `"${item.user_query.slice(0, 60)}"` : "an answer"}</span>
          </>
        ) : (
          "Feedback"
        )
      }
      description={
        item ? `${item.created_at ? new Date(item.created_at).toLocaleString() : "Unknown time"} · ${item.actor}` : undefined
      }
      headerExtra={
        item ? (
          <span className={`mr-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_BADGE[item.triage_status]}`}>
            {TRIAGE_LABELS[item.triage_status]}
          </span>
        ) : null
      }
      fullViewHref={item ? `/admin/conversations/${item.conversation_id}` : undefined}
      footer={
        item && canTriage ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            {(["reviewed", "dismissed", "promoted"] as const).map((action) => {
              const Icon = ACTION_ICONS[action];
              const allowed = canTransition(item.triage_status, action);
              return (
                <button
                  key={action}
                  type="button"
                  disabled={!allowed || busy}
                  onClick={() => onAction(item, action)}
                  className={action === "promoted" ? primaryButton : secondaryButton}
                  aria-keyshortcuts={ACTION_LABELS[action].key}
                >
                  <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                  {ACTION_LABELS[action].verb}
                  <kbd className="ml-1 rounded bg-surface-muted px-1 font-mono text-[10px] text-fg-subtle">
                    {ACTION_LABELS[action].key}
                  </kbd>
                </button>
              );
            })}
            {item.triage_status === "dismissed" && onRestore ? (
              <button type="button" disabled={busy} onClick={() => onRestore(item)} className={secondaryButton}>
                <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                Restore to queue
              </button>
            ) : null}
          </div>
        ) : null
      }
    >
      {item ? (
        <>
          <Section title="Question">
            <p className="whitespace-pre-wrap rounded-lg bg-surface-muted/50 p-3 text-sm text-fg">{item.user_query ?? "—"}</p>
          </Section>
          <Section title="Answer">
            <p className="whitespace-pre-wrap rounded-lg border border-line p-3 text-sm text-fg">{item.answer ? renderCitedText(item.answer) : "—"}</p>
          </Section>

          <Section title="Labels and flags">
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <Meta label="Outcome" value={outcomeLabel(item.outcome)} />
              <Meta label="Grounded" value={item.grounded === null ? "—" : item.grounded ? "Yes" : "No"} />
              <Meta label="Confidence" value={fmtNumber(item.confidence, 2)} />
              <Meta label="Latency" value={fmtNumber(item.latency_ms, 0, " ms")} />
              <Meta label="Cost" value={item.cost_usd === null ? "—" : `$${item.cost_usd.toFixed(4)}`} />
              <Meta label="Status" value={TRIAGE_LABELS[item.triage_status]} />
            </dl>
            {item.guardrail_flags.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5" aria-label="Guardrail flags">
                {item.guardrail_flags.map((flag) => (
                  <li key={flag} className="rounded-full bg-status-warn-bg px-2 py-0.5 text-[11px] font-medium text-status-warn-fg">
                    {flag}
                  </li>
                ))}
              </ul>
            ) : null}
          </Section>

          <Section title={`Sources (${item.sources.length})`}>
            {item.sources.length === 0 ? (
              <p className="text-xs text-fg-subtle">No sources were cited.</p>
            ) : (
              <ol className="space-y-1.5">
                {item.sources.map((s, i) => (
                  <li key={s.chunk_id ?? i} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2 text-xs">
                    <span className="min-w-0 truncate text-fg">
                      {s.source_name ?? "Unknown source"}
                      {s.section_heading ? <span className="text-fg-subtle"> · {s.section_heading}</span> : null}
                    </span>
                    <span className="shrink-0 tabular-nums font-semibold text-fg-muted">{s.score === null ? "—" : s.score.toFixed(3)}</span>
                  </li>
                ))}
              </ol>
            )}
          </Section>

          <Section title="User feedback">
            <p className="text-xs text-fg">{item.comment ? item.comment : <span className="italic text-fg-subtle">No comment</span>}</p>
            {item.reason_tags.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5" aria-label="Reasons">
                {item.reason_tags.map((tag) => (
                  <li key={tag} className="rounded-full border border-line bg-surface-muted px-2 py-0.5 text-[11px] text-fg-muted">
                    {reasonLabel(tag)}
                  </li>
                ))}
              </ul>
            ) : null}
            {item.reviewed_by ? (
              <p className="text-[11px] text-fg-subtle">
                Reviewed by {item.reviewed_by}
                {item.reviewed_at ? (
                  <>
                    {" "}
                    <RelativeTime value={item.reviewed_at} />
                  </>
                ) : null}
              </p>
            ) : null}
          </Section>

          <Section title="Correct answer">
            <TextEditor
              key={`answer-${item.id}`}
              label="Write the correct answer"
              value={item.corrected_answer ?? ""}
              max={MAX_CORRECTED_ANSWER_CHARS}
              placeholder={canTriage ? "Becomes the preferred (chosen) answer in the preference export." : "No corrected answer yet."}
              saveLabel="Save answer"
              disabled={!canTriage}
              onSave={(text) => onSaveCorrectedAnswer(item, text)}
            />
          </Section>

          <Section title="Reviewer note">
            <TextEditor
              key={`note-${item.id}`}
              label="Note"
              value={item.review_note ?? ""}
              max={MAX_REVIEW_NOTE_CHARS}
              placeholder={canTriage ? "Why this was reviewed, dismissed or promoted." : "No note."}
              saveLabel="Save note"
              disabled={!canTriage}
              onSave={(text) => onSaveNote(item, text)}
            />
          </Section>
        </>
      ) : null}
    </DrawerShell>
  );
}
