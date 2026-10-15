"use client";

import React from "react";
import { AnimatePresence, m } from "motion/react";
import { Filter, X } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import type { FilterChip } from "@/lib/feedback-filters";
import { focusRing } from "@/components/ui/styles";

export interface FilterChipsProps {
  chips: readonly FilterChip[];
  onRemove: (chip: FilterChip) => void;
  onClearAll: () => void;
  /** Shown when some chips filter the loaded page on the client only. */
  note?: string;
}

/** Active queue filters as removable chips (state lives in the URL). */
export function FilterChips({ chips, onRemove, onClearAll, note }: FilterChipsProps) {
  const variants = useMotionVariants(pop);
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" role="region" aria-label="Active filters">
      <Filter className="h-4 w-4 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
      <ul className="flex flex-wrap items-center gap-1.5">
        <AnimatePresence initial={false}>
          {chips.map((chip) => (
            <m.li
              key={chip.key}
              layout
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              className="inline-flex items-center gap-1 rounded-full border border-brand-300 bg-brand-50 py-0.5 pl-2.5 pr-1 text-[11px] font-medium text-brand-800 dark:border-brand-700/60 dark:bg-brand-950/60 dark:text-brand-200"
              data-testid="filter-chip"
            >
              {chip.label}
              <button
                type="button"
                onClick={() => onRemove(chip)}
                aria-label={`Remove filter ${chip.label}`}
                className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-brand-700 transition-colors hover:bg-brand-100 dark:text-brand-300 dark:hover:bg-brand-900/60 ${focusRing}`}
              >
                <X className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
              </button>
            </m.li>
          ))}
        </AnimatePresence>
      </ul>
      <button
        type="button"
        onClick={onClearAll}
        className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium text-fg-muted transition-colors hover:text-fg ${focusRing}`}
      >
        Clear all
      </button>
      {note ? <span className="text-[11px] text-fg-subtle">{note}</span> : null}
    </div>
  );
}
