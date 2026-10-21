"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent, type MutableRefObject } from "react";
import { m } from "motion/react";
import { Check, CheckCircle2, MessageSquareText } from "lucide-react";
import type { Clarification, ClarificationAnswer, ClarificationQuestion } from "@/lib/types";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";
import { Badge } from "@/components/ui/Badge";

/** What the user picked for one question. */
export interface QuestionSelection {
  optionIds: string[];
  other: boolean;
  otherText: string;
}

type Selections = Record<string, QuestionSelection>;

const EMPTY: QuestionSelection = { optionIds: [], other: false, otherText: "" };
const OTHER_ID = "__other__";

function selectionOf(selections: Selections, q: ClarificationQuestion): QuestionSelection {
  return selections[q.id] ?? EMPTY;
}

export function isAnswered(selection: QuestionSelection): boolean {
  return selection.optionIds.length > 0 || (selection.other && selection.otherText.trim().length > 0);
}

/** The chosen labels of one question, "Other" text last. */
export function answerLabels(q: ClarificationQuestion, selection: QuestionSelection): string[] {
  const labels = q.options.filter((o) => selection.optionIds.includes(o.id)).map((o) => o.label);
  const other = selection.otherText.trim();
  return selection.other && other ? [...labels, other] : labels;
}

/** "Topic: Contact sales · Scope: Australia" (also the follow-up's visible text). */
export function summarizeAnswers(clarification: Clarification, selections: Selections): string {
  return clarification.questions
    .map((q) => ({ q, labels: answerLabels(q, selectionOf(selections, q)) }))
    .filter(({ labels }) => labels.length > 0)
    .map(({ q, labels }) => `${q.header}: ${labels.join(", ")}`)
    .join(" · ");
}

/** Request payload: one entry per answered question. */
export function toAnswers(clarification: Clarification, selections: Selections): ClarificationAnswer[] {
  return clarification.questions
    .map((q) => ({ q, s: selectionOf(selections, q) }))
    .filter(({ s }) => isAnswered(s))
    .map(({ q, s }) => ({
      question_id: q.id,
      option_ids: s.optionIds,
      ...(s.other && s.otherText.trim() ? { other_text: s.otherText.trim() } : {}),
    }));
}

function isTyping(): boolean {
  const el = typeof document === "undefined" ? null : document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || (el as HTMLElement).isContentEditable);
}

export interface ClarificationCardProps {
  clarification: Clarification;
  /** All answers at once, plus their one-line summary (sent as the user's turn). */
  onSubmit?: (answers: ClarificationAnswer[], summary: string) => void;
  /** Esc: the card steps aside and the composer takes focus. */
  onDismiss?: () => void;
  /** Already answered (e.g. the next user turn after a reload): read-only summary. */
  answeredSummary?: string | null;
  /** A reply is in flight, or the conversation moved on. */
  disabled?: boolean;
  /** Move focus into the card when it appears (never while the user is typing). */
  autoFocus?: boolean;
}

/**
 * Clarifying questions as one panel attached to the assistant turn, in the
 * style of a terminal "ask user" prompt: a tab per question with progress,
 * full-width option rows (number key, label, description, radio / checkbox),
 * an "Other" row that becomes a text field, a review step when there are
 * several questions, and a compact read-only summary once answered.
 *
 * Keyboard: ↑/↓ move between rows, 1-9 pick, Space toggles, Enter confirms /
 * goes on, ←/→ switch questions, Esc dismisses.
 */
export function ClarificationCard({
  clarification,
  onSubmit,
  onDismiss,
  answeredSummary = null,
  disabled = false,
  autoFocus = false,
}: ClarificationCardProps) {
  const uid = useId();
  const variants = useMotionVariants(fadeUp);
  const questions = clarification.questions;
  const multi = questions.length > 1;
  const reviewStep = questions.length; // only reachable when `multi`
  const allowOther = clarification.allow_free_text !== false;

  const [step, setStep] = useState(0);
  const [selections, setSelections] = useState<Selections>({});
  const [active, setActive] = useState<Record<string, number>>({});
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const rowRefs = useRef<Record<string, (HTMLDivElement | null)[]>>({});
  const otherInputRef = useRef<HTMLInputElement | null>(null);
  const sendRef = useRef<HTMLButtonElement | null>(null);
  const moved = useRef(false);

  const summary = answeredSummary ?? submitted;
  const inReview = multi && step === reviewStep;
  const question = inReview ? null : questions[step];

  const rowCount = (q: ClarificationQuestion) => q.options.length + (allowOther ? 1 : 0);
  const activeIndex = (q: ClarificationQuestion) => Math.min(active[q.id] ?? 0, rowCount(q) - 1);

  function focusRow(q: ClarificationQuestion, index: number) {
    setActive((prev) => ({ ...prev, [q.id]: index }));
    rowRefs.current[q.id]?.[index]?.focus();
  }

  // Focus follows the step once the user has started moving through the card.
  useEffect(() => {
    if (!moved.current || summary || dismissed) return;
    if (inReview) sendRef.current?.focus();
    else if (question) rowRefs.current[question.id]?.[activeIndex(question)]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  // Entry focus: into the first row, unless the user is already typing.
  useEffect(() => {
    if (!autoFocus || disabled || summary || isTyping()) return;
    const first = questions[0];
    rowRefs.current[first.id]?.[0]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFocus]);

  if (summary) {
    return (
      <div
        className="inline-flex max-w-full items-start gap-2 rounded-2xl border border-line bg-surface-card px-3.5 py-2 text-xs text-fg-muted shadow-sm"
        aria-label="Your answers"
        role="note"
      >
        <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0 text-status-ok" strokeWidth={2} aria-hidden="true" />
        <span className="min-w-0 break-words">{summary}</span>
      </div>
    );
  }

  if (dismissed) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-subtle">
        <MessageSquareText className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        <span>Questions skipped. Type your own answer below.</span>
        <button
          type="button"
          onClick={() => setDismissed(false)}
          className={`rounded-md px-1.5 py-0.5 font-medium text-accent-fg hover:underline ${focusRing}`}
        >
          Show questions
        </button>
      </div>
    );
  }

  const update = (q: ClarificationQuestion, change: (s: QuestionSelection) => QuestionSelection) =>
    setSelections((prev) => ({ ...prev, [q.id]: change(selectionOf(prev, q)) }));

  function choose(q: ClarificationQuestion, index: number) {
    if (disabled) return;
    const isOther = index === q.options.length;
    const optionId = isOther ? OTHER_ID : q.options[index].id;
    update(q, (s) => {
      if (q.multi_select) {
        if (isOther) return { ...s, other: !s.other };
        const has = s.optionIds.includes(optionId);
        return { ...s, optionIds: has ? s.optionIds.filter((id) => id !== optionId) : [...s.optionIds, optionId] };
      }
      return isOther ? { ...s, optionIds: [], other: true } : { ...s, optionIds: [optionId], other: false };
    });
    setActive((prev) => ({ ...prev, [q.id]: index }));
    if (isOther) setTimeout(() => otherInputRef.current?.focus(), 0);
  }

  function goTo(next: number) {
    moved.current = true;
    setStep(Math.max(0, Math.min(next, multi ? reviewStep : 0)));
  }

  function submit() {
    if (disabled) return;
    const answers = toAnswers(clarification, selections);
    if (answers.length === 0) return;
    const text = summarizeAnswers(clarification, selections);
    setSubmitted(text);
    onSubmit?.(answers, text);
  }

  function advance() {
    if (!question || !isAnswered(selectionOf(selections, question))) return;
    if (!multi) submit();
    else goTo(step + 1);
  }

  function dismiss() {
    setDismissed(true);
    onDismiss?.();
  }

  function onRowsKey(e: KeyboardEvent<HTMLDivElement>, q: ClarificationQuestion) {
    const typing = e.target instanceof HTMLInputElement;
    const count = rowCount(q);
    const current = activeIndex(q);
    if (typing) {
      if (e.key === "Enter") {
        e.preventDefault();
        advance();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        focusRow(q, q.options.length);
      }
      return;
    }
    const digit = Number(e.key);
    if (Number.isInteger(digit) && digit >= 1 && digit <= count) {
      e.preventDefault();
      choose(q, digit - 1);
      if (digit - 1 < q.options.length) focusRow(q, digit - 1);
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusRow(q, (current + 1) % count);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusRow(q, (current - 1 + count) % count);
        break;
      case " ":
        e.preventDefault();
        choose(q, current);
        break;
      case "Enter": {
        e.preventDefault();
        const s = selectionOf(selections, q);
        const isOther = current === q.options.length;
        const picked = isOther ? s.other : s.optionIds.includes(q.options[current].id);
        if (!picked && !q.multi_select) {
          choose(q, current);
          if (isOther) return;
          // A single-select pick confirms at once.
          const nextSel: Selections = { ...selections, [q.id]: { optionIds: [q.options[current].id], other: false, otherText: s.otherText } };
          if (!multi) {
            const answers = toAnswers(clarification, nextSel);
            const text = summarizeAnswers(clarification, nextSel);
            setSubmitted(text);
            onSubmit?.(answers, text);
          } else goTo(step + 1);
          return;
        }
        advance();
        break;
      }
      case "ArrowRight":
        if (multi) {
          e.preventDefault();
          goTo(step + 1);
        }
        break;
      case "ArrowLeft":
        if (multi) {
          e.preventDefault();
          goTo(step - 1);
        }
        break;
      default:
    }
  }

  function onCardKey(e: KeyboardEvent<HTMLElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      dismiss();
    } else if (inReview && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      goTo(e.key === "ArrowLeft" ? step - 1 : step);
    }
  }

  const live = inReview ? "Review step: check your answers before sending" : multi ? `Question ${step + 1} of ${questions.length}` : "";
  const currentAnswered = question ? isAnswered(selectionOf(selections, question)) : true;
  const answeredCount = questions.filter((q) => isAnswered(selectionOf(selections, q))).length;

  return (
    <m.section
      variants={variants}
      initial="hidden"
      animate="visible"
      aria-label="Clarifying questions"
      onKeyDown={onCardKey}
      className="w-full overflow-hidden rounded-2xl border border-line bg-surface-card text-fg shadow-sm dark:border-white/10"
      data-tour="chat-clarification"
    >
      {clarification.preamble ? (
        <p className="px-4 pt-3.5 text-sm leading-relaxed text-fg-muted">{clarification.preamble}</p>
      ) : null}

      {multi ? (
        <div className="flex items-center gap-2 border-b border-line px-3 pb-2 pt-3 dark:border-white/10">
          <div role="tablist" aria-label="Questions" className="flex min-w-0 flex-1 flex-wrap gap-1">
            {questions.map((q, i) => {
              const done = isAnswered(selectionOf(selections, q));
              return (
                <button
                  key={q.id}
                  type="button"
                  role="tab"
                  id={`${uid}-tab-${q.id}`}
                  aria-selected={step === i}
                  aria-controls={`${uid}-panel`}
                  tabIndex={step === i ? 0 : -1}
                  onClick={() => goTo(i)}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors motion-reduce:transition-none ${focusRing} ${
                    step === i
                      ? "bg-brand-600 text-white dark:bg-brand-500"
                      : "text-fg-muted hover:bg-surface-muted hover:text-fg"
                  }`}
                >
                  {done ? (
                    <Check className="h-3 w-3" strokeWidth={2.5} aria-label="answered" />
                  ) : (
                    <span className="tabular-nums opacity-70" aria-hidden="true">
                      {i + 1}
                    </span>
                  )}
                  {q.header}
                </button>
              );
            })}
            <button
              type="button"
              role="tab"
              aria-selected={inReview}
              aria-controls={`${uid}-panel`}
              tabIndex={inReview ? 0 : -1}
              disabled={answeredCount === 0}
              onClick={() => goTo(reviewStep)}
              className={`inline-flex items-center rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors disabled:opacity-40 motion-reduce:transition-none ${focusRing} ${
                inReview ? "bg-brand-600 text-white dark:bg-brand-500" : "text-fg-muted hover:bg-surface-muted hover:text-fg"
              }`}
            >
              Review
            </button>
          </div>
          <span className="shrink-0 text-[11px] tabular-nums text-fg-subtle" aria-hidden="true">
            {inReview ? "Review" : `${step + 1} of ${questions.length}`}
          </span>
        </div>
      ) : null}

      <p className="sr-only" aria-live="polite">
        {live}
      </p>

      <div id={`${uid}-panel`} role={multi ? "tabpanel" : undefined} className="px-3 pb-3 pt-3">
        {question ? (
          <QuestionRows
            uid={uid}
            question={question}
            selection={selectionOf(selections, question)}
            activeIndex={activeIndex(question)}
            allowOther={allowOther}
            disabled={disabled}
            rowRefs={(el, i) => {
              rowRefs.current[question.id] = rowRefs.current[question.id] ?? [];
              rowRefs.current[question.id][i] = el;
            }}
            otherInputRef={otherInputRef}
            onChoose={(i) => choose(question, i)}
            onFocusRow={(i) => setActive((prev) => ({ ...prev, [question.id]: i }))}
            onOtherText={(text) => update(question, (s) => ({ ...s, otherText: text.slice(0, 300) }))}
            onKeyDown={(e) => onRowsKey(e, question)}
          />
        ) : (
          <div>
            <p className="px-1 text-sm font-medium">Review your answers</p>
            <dl className="mt-2 divide-y divide-line rounded-xl border border-line dark:divide-white/10 dark:border-white/10">
              {questions.map((q, i) => {
                const labels = answerLabels(q, selectionOf(selections, q));
                return (
                  <div key={q.id} className="flex items-start gap-3 px-3 py-2 text-xs">
                    <dt className="w-24 shrink-0 font-semibold text-fg-muted">{q.header}</dt>
                    <dd className="min-w-0 flex-1">
                      {labels.length ? (
                        <span className="text-fg">{labels.join(", ")}</span>
                      ) : (
                        <span className="text-fg-subtle">Not answered</span>
                      )}
                    </dd>
                    <button
                      type="button"
                      onClick={() => goTo(i)}
                      className={`shrink-0 rounded px-1 font-medium text-accent-fg hover:underline ${focusRing}`}
                    >
                      Change
                    </button>
                  </div>
                );
              })}
            </dl>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2.5 dark:border-white/10">
        <p className="mr-auto hidden text-[11px] text-fg-subtle sm:block" aria-hidden="true">
          ↑↓ move · 1-{question ? rowCount(question) : 0} pick{question?.multi_select ? " · Space toggle" : ""} · Enter{" "}
          {multi && !inReview ? "next" : "send"} · Esc skip
        </p>
        {multi && step > 0 ? (
          <button type="button" onClick={() => goTo(step - 1)} className={`${secondaryButton} py-1.5`}>
            Back
          </button>
        ) : null}
        {inReview || !multi ? (
          <button
            ref={sendRef}
            type="button"
            onClick={inReview ? submit : advance}
            disabled={disabled || (inReview ? answeredCount === 0 : !currentAnswered)}
            className={`${primaryButton} py-1.5`}
          >
            {inReview ? "Send answers" : "Send"}
          </button>
        ) : (
          <button
            type="button"
            onClick={advance}
            disabled={disabled || !currentAnswered}
            className={`${primaryButton} py-1.5`}
          >
            {step === questions.length - 1 ? "Review" : "Next"}
          </button>
        )}
      </div>
    </m.section>
  );
}

function QuestionRows({
  uid,
  question,
  selection,
  activeIndex,
  allowOther,
  disabled,
  rowRefs,
  otherInputRef,
  onChoose,
  onFocusRow,
  onOtherText,
  onKeyDown,
}: {
  uid: string;
  question: ClarificationQuestion;
  selection: QuestionSelection;
  activeIndex: number;
  allowOther: boolean;
  disabled: boolean;
  rowRefs: (el: HTMLDivElement | null, index: number) => void;
  otherInputRef: MutableRefObject<HTMLInputElement | null>;
  onChoose: (index: number) => void;
  onFocusRow: (index: number) => void;
  onOtherText: (text: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void;
}) {
  const multi = question.multi_select === true;
  const labelId = `${uid}-${question.id}-label`;
  // A tag only (never pre-selected); at most one, even if the payload had more.
  const recommendedId = question.options.find((o) => o.recommended === true)?.id;
  const rows = [
    ...question.options.map((o) => ({
      key: o.id,
      label: o.label,
      description: o.description ?? "",
      sources: o.sources ?? [],
      checked: selection.optionIds.includes(o.id),
      recommended: o.id === recommendedId,
    })),
    ...(allowOther
      ? [
          {
            key: OTHER_ID,
            label: "Other",
            description: "Type your own answer",
            sources: [] as string[],
            checked: selection.other,
            recommended: false,
          },
        ]
      : []),
  ];

  return (
    <div>
      <p id={labelId} className="px-1 text-sm font-medium leading-snug text-fg">
        {question.question}
      </p>
      <div
        role={multi ? "group" : "radiogroup"}
        aria-labelledby={labelId}
        onKeyDown={onKeyDown}
        className="mt-2 space-y-1"
      >
        {rows.map((row, i) => {
          const descId = `${uid}-${question.id}-${row.key}-desc`;
          const isOther = row.key === OTHER_ID;
          return (
            <div key={row.key}>
              <div
                ref={(el) => rowRefs(el, i)}
                role={multi ? "checkbox" : "radio"}
                aria-checked={row.checked}
                aria-describedby={row.description || row.sources.length ? descId : undefined}
                aria-disabled={disabled || undefined}
                tabIndex={i === activeIndex ? 0 : -1}
                onClick={() => onChoose(i)}
                onFocus={() => onFocusRow(i)}
                className={`relative flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors duration-150 motion-reduce:transition-none ${focusRing} ${
                  row.checked
                    ? "border-brand-300 bg-brand-50/70 before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-brand-600 dark:border-brand-500/40 dark:bg-brand-500/10 dark:before:bg-brand-400"
                    : "border-transparent hover:bg-surface-muted dark:hover:bg-white/[0.04]"
                } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
              >
                <kbd className="mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md border border-line bg-surface-muted px-1 font-mono text-[10px] font-semibold text-fg-muted dark:border-white/10">
                  {i + 1}
                </kbd>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-semibold text-fg">
                    <span>{row.label}</span>{" "}
                    {row.recommended ? <Badge tone="brand">Recommended</Badge> : null}
                  </span>
                  {row.description || row.sources.length ? (
                    <span id={descId} className="mt-0.5 block truncate text-xs text-fg-muted" title={row.description}>
                      {row.description}
                      {row.sources.length ? (
                        <span className="text-fg-subtle">
                          {row.description ? " · " : ""}from: {row.sources.join(", ")}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                </span>
                <span
                  aria-hidden="true"
                  className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center border transition-colors motion-reduce:transition-none ${
                    multi ? "rounded-[5px]" : "rounded-full"
                  } ${row.checked ? "border-brand-600 bg-brand-600 text-white dark:border-brand-400 dark:bg-brand-500" : "border-line-strong bg-surface-card"}`}
                >
                  {row.checked ? (
                    multi ? (
                      <Check className="h-3 w-3" strokeWidth={3} />
                    ) : (
                      <span className="h-1.5 w-1.5 rounded-full bg-surface-card" />
                    )
                  ) : null}
                </span>
              </div>
              {isOther && row.checked ? (
                <input
                  ref={otherInputRef}
                  type="text"
                  value={selection.otherText}
                  onChange={(e) => onOtherText(e.target.value)}
                  aria-label={`Your own answer: ${question.question}`}
                  placeholder="Type your answer, then press Enter"
                  maxLength={300}
                  disabled={disabled}
                  className="mt-1.5 w-full rounded-lg border border-line bg-surface-card px-3 py-2 text-sm text-fg outline-none placeholder:text-fg-subtle focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 dark:border-white/10"
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
