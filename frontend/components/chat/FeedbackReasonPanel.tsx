"use client";

import React, { useEffect, useId, useRef } from "react";
import { m } from "motion/react";
import { Check } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import { MAX_CORRECTED_ANSWER_CHARS, REASON_LABELS, REASON_TAGS, type ReasonTag } from "@/lib/feedback-api";
import { focusRing, primaryButton } from "@/components/ui/styles";

export interface FeedbackReasonPanelProps {
  reasons: readonly ReasonTag[];
  onToggleReason: (tag: ReasonTag) => void;
  correctedAnswer: string;
  onCorrectedAnswerChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  saving?: boolean;
}

const CHIP = `inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors duration-150 ${focusRing}`;
const CHIP_ON = "border-brand-500 bg-brand-50 text-brand-700 dark:border-brand-400/60 dark:bg-brand-950/60 dark:text-brand-200";
const CHIP_OFF = "border-line bg-surface-card text-fg-muted hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60";

/**
 * Thumbs-down follow-up: allow-listed reason chips that
 * pop in, plus an optional "what should it have said?" box with a counter.
 */
export function FeedbackReasonPanel({
  reasons,
  onToggleReason,
  correctedAnswer,
  onCorrectedAnswerChange,
  onSubmit,
  onCancel,
  saving = false,
}: FeedbackReasonPanelProps) {
  const variants = useMotionVariants(pop);
  const headingId = useId();
  const answerId = useId();
  const firstChipRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    firstChipRef.current?.focus();
  }, []);

  return (
    <m.div
      role="dialog"
      aria-labelledby={headingId}
      variants={variants}
      initial="hidden"
      animate="visible"
      exit="exit"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onCancel();
        }
      }}
      className="mt-2 w-full space-y-3 rounded-xl border border-line bg-surface-card p-3 shadow-md"
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <p id={headingId} className="text-xs font-medium text-fg">
          What went wrong?
        </p>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Reasons">
          {REASON_TAGS.map((tag, i) => {
            const on = reasons.includes(tag);
            return (
              <m.button
                key={tag}
                ref={i === 0 ? firstChipRef : undefined}
                type="button"
                variants={variants}
                aria-pressed={on}
                onClick={() => onToggleReason(tag)}
                className={`${CHIP} ${on ? CHIP_ON : CHIP_OFF}`}
              >
                {on ? <Check className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> : null}
                {REASON_LABELS[tag]}
              </m.button>
            );
          })}
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[11px] text-fg-muted">
            <label htmlFor={answerId}>What should it have said? (optional)</label>
            <span className="tabular-nums text-fg-subtle" aria-live="polite">
              {correctedAnswer.length}/{MAX_CORRECTED_ANSWER_CHARS}
            </span>
          </div>
          <textarea
            id={answerId}
            value={correctedAnswer}
            onChange={(e) => onCorrectedAnswerChange(e.target.value.slice(0, MAX_CORRECTED_ANSWER_CHARS))}
            maxLength={MAX_CORRECTED_ANSWER_CHARS}
            rows={3}
            placeholder="The answer you expected. Do not include personal information."
            className="w-full resize-y rounded-lg border border-line bg-surface-muted/50 p-2 text-xs text-fg placeholder:text-fg-subtle outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
          />
        </div>
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className={`rounded-lg px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg ${focusRing}`}
          >
            Skip
          </button>
          <button type="submit" disabled={saving} className={primaryButton}>
            {saving ? "Sending…" : "Send feedback"}
          </button>
        </div>
      </form>
    </m.div>
  );
}
