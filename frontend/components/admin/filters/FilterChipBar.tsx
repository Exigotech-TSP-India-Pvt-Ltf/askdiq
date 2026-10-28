"use client";

import React, { useId } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, ListFilter } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import { usePopover } from "@/lib/use-popover";
import { focusRing, popoverPanel } from "@/components/ui/styles";
import { FilterChip } from "./FilterChip";
import { activeCount, hasChip, isActive, type FilterDescriptor } from "./types";

export interface FilterChipBarProps {
  filters: readonly FilterDescriptor[];
  onChange: (key: string, value: string) => void;
  /** "Clear all": restores the page's defaults (shown only while a filter is active). */
  onClearAll?: () => void;
  /** Show the "Filters" button (off where the host already has one, e.g. TableToolbar). */
  showFiltersButton?: boolean;
  /** Footer note in the Filters popover (e.g. client-side filtering). */
  note?: React.ReactNode;
  /** Shown before the chips (e.g. a compact search box). */
  leading?: React.ReactNode;
  /** Region name (one "Active filters" region per page; tables use their own). */
  ariaLabel?: string;
  className?: string;
  "data-tour"?: string;
}

const filtersButton = `relative inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium shadow-xs transition-colors ${focusRing}`;

/**
 * Active filters as editable chips plus a "Filters" button with the active
 * count (OR-13), one component for Feedback, Analytics and TableToolbar.
 * Chips change their value in place (listbox); the Filters popover edits
 * every field the descriptors list; "Clear all" shows only when something is
 * active. Selections go straight to `onChange`, so the caller keeps its URL
 * contract (no new keys).
 */
export function FilterChipBar({
  filters,
  onChange,
  onClearAll,
  showFiltersButton = true,
  note,
  leading,
  ariaLabel = "Active filters",
  className = "",
  "data-tour": dataTour,
}: FilterChipBarProps) {
  const count = activeCount(filters);
  const chips = filters.filter(hasChip);
  const editable = filters.filter((f) => f.inPopover !== false && (f.options?.length ?? 0) > 0);
  const { open, toggle, triggerRef, panelRef } = usePopover();
  const variants = useMotionVariants(pop);
  const panelId = useId();

  return (
    <div
      data-tour={dataTour}
      role="region"
      aria-label={ariaLabel}
      className={`flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1.5 ${className}`}
    >
      {leading}
      {chips.length > 0 ? (
        <ul className="flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1.5">
          {chips.map((filter) => (
            <li key={filter.key} className="min-w-0 max-w-full">
              <FilterChip filter={filter} onChange={onChange} />
            </li>
          ))}
        </ul>
      ) : null}
      {onClearAll && count > 0 ? (
        <button
          type="button"
          onClick={onClearAll}
          className={`rounded-md px-1.5 py-1 text-[11px] font-medium text-fg-muted transition-colors hover:text-fg ${focusRing}`}
        >
          Clear all
        </button>
      ) : null}
      {showFiltersButton && editable.length > 0 ? (
        <div className="relative">
          <button
            ref={triggerRef}
            type="button"
            onClick={toggle}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            aria-label={count > 0 ? `Filters, ${count} active` : "Filters"}
            className={`${filtersButton} ${
              count > 0
                ? "border-brand-500 bg-brand-50/70 font-semibold text-brand-700 dark:bg-brand-950/60 dark:text-brand-300"
                : "border-line bg-surface-card text-fg-muted hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60"
            }`}
          >
            <ListFilter className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            <span>Filters</span>
            {count > 0 ? (
              <span data-testid="filters-count" className="rounded-full bg-brand-600 px-1.5 text-[10px] font-semibold leading-4 text-white dark:bg-brand-500">
                {count}
              </span>
            ) : null}
          </button>
          <AnimatePresence>
            {open ? (
              <m.div
                ref={panelRef}
                id={panelId}
                role="dialog"
                aria-label="Filters"
                variants={variants}
                initial="hidden"
                animate="visible"
                exit="exit"
                className={`absolute right-0 top-full z-50 mt-2 w-72 max-w-[calc(100vw-2rem)] origin-top-right ${popoverPanel} p-3`}
              >
                <div className="max-h-80 space-y-3 overflow-y-auto pr-1">
                  {editable.map((filter) => (
                    <fieldset key={filter.key} className="space-y-1">
                      <legend className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">{filter.label}</legend>
                      <div className="flex flex-wrap gap-1">
                        {filter.options?.map((option) => {
                          const selected = option.value === filter.value || (!filter.value && option.value === "");
                          return (
                            <button
                              key={option.value}
                              type="button"
                              aria-pressed={selected}
                              onClick={() => onChange(filter.key, option.value)}
                              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] transition-colors ${focusRing} ${
                                selected
                                  ? "border-brand-500 bg-brand-50 font-semibold text-brand-700 dark:bg-brand-950/60 dark:text-brand-300"
                                  : "border-line text-fg-muted hover:border-brand-300 hover:text-fg"
                              }`}
                            >
                              {selected ? <Check className="h-3 w-3" strokeWidth={2} aria-hidden="true" /> : null}
                              {option.label}
                            </button>
                          );
                        })}
                      </div>
                    </fieldset>
                  ))}
                </div>
                {note || (onClearAll && filters.some(isActive)) ? (
                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2">
                    <span className="text-[11px] text-fg-subtle">{note}</span>
                    {onClearAll && count > 0 ? (
                      <button type="button" onClick={onClearAll} className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}>
                        Reset all
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </m.div>
            ) : null}
          </AnimatePresence>
        </div>
      ) : null}
    </div>
  );
}
