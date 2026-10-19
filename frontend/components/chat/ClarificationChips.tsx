"use client";

import { useId } from "react";
import { PencilLine } from "lucide-react";
import type { Clarification, ClarificationOption } from "@/lib/types";
import { focusRing } from "@/components/ui/styles";

export interface ClarificationChipsProps {
  clarification: Clarification;
  /** Sends the picked option as the next turn. */
  onSelect?: (option: ClarificationOption) => void;
  /** "Something else…": lets the user type their own answer (focuses the composer). */
  onSomethingElse?: () => void;
  /** Option already picked (highlighted). */
  chosenId?: string | null;
  /** True once the conversation moved on, or while a reply is being sent. */
  disabled?: boolean;
}

const chip = `inline-flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1.5 text-left text-xs font-medium transition-colors disabled:cursor-not-allowed ${focusRing}`;
const idle =
  "border-brand-200 bg-brand-50/60 text-brand-700 hover:border-brand-400 hover:bg-brand-50 disabled:opacity-60 disabled:hover:border-brand-200 disabled:hover:bg-brand-50/60 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200 dark:hover:border-brand-400/60 dark:hover:bg-brand-500/20";
const picked = "border-brand-600 bg-brand-600 text-white dark:border-brand-400 dark:bg-brand-500 dark:text-white";
const other =
  "border-dashed border-line bg-transparent text-fg-muted hover:border-brand-300 hover:text-fg disabled:opacity-60 disabled:hover:border-line";

/**
 * The options of a clarifying question (outcome "clarify") as chips under the
 * assistant's question. Each chip is a real button (Tab / Enter / Space), the
 * group is labelled by the question, and every chip is disabled once one has
 * been picked so the same question cannot be answered twice.
 */
export function ClarificationChips({
  clarification,
  onSelect,
  onSomethingElse,
  chosenId = null,
  disabled = false,
}: ClarificationChipsProps) {
  const hintId = useId();
  const locked = disabled || chosenId !== null;
  const hint =
    chosenId !== null
      ? "Option chosen."
      : disabled
        ? null
        : "Pick the closest option, or choose Something else to type your own.";
  return (
    <div className="space-y-1.5">
      <div
        role="group"
        aria-label={clarification.question}
        aria-describedby={hint ? hintId : undefined}
        className="flex flex-wrap gap-1.5"
      >
        {clarification.options.map((option) => {
          const isPicked = option.id === chosenId;
          return (
            <button
              key={option.id}
              type="button"
              disabled={locked}
              aria-pressed={isPicked}
              title={option.source_name ?? undefined}
              onClick={() => onSelect?.(option)}
              className={`${chip} ${isPicked ? picked : idle}`}
            >
              <span className="truncate">{option.label}</span>
            </button>
          );
        })}
        {clarification.allow_free_text !== false ? (
          <button type="button" disabled={locked} onClick={() => onSomethingElse?.()} className={`${chip} ${other}`}>
            <PencilLine className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            Something else…
          </button>
        ) : null}
      </div>
      {hint ? (
        <p id={hintId} className="text-[11px] text-fg-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
