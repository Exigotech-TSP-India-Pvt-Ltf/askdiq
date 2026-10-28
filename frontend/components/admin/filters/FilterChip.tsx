"use client";

import React, { useId, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, ChevronDown, X } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import { useMenu } from "@/lib/use-menu";
import { focusRing, popoverPanel } from "@/components/ui/styles";
import { chipText, resetValue, type FilterDescriptor } from "./types";

const PANEL_WIDTH_PX = 224;
const EDGE_PX = 16;

const chipShell =
  "inline-flex max-w-full items-center rounded-full border border-brand-300 bg-brand-50 text-[11px] font-medium text-brand-800 dark:border-brand-700/60 dark:bg-brand-950/60 dark:text-brand-200";

interface FilterChipProps {
  filter: FilterDescriptor;
  onChange: (key: string, value: string) => void;
}

/**
 * One active filter (OR-13): the chip body opens its options as an APG
 * listbox in a popover (current value `aria-selected` with a check), so the
 * value can be changed in place; the × is a separate "Remove filter" button.
 * Filters without options (search text, a picked person) are a label + ×.
 */
export function FilterChip({ filter, onChange }: FilterChipProps) {
  const text = chipText(filter);
  const menu = useMenu<HTMLButtonElement>();
  const { open, close, toggle, triggerRef, panelRef } = menu;
  const [alignRight, setAlignRight] = useState(false);
  const variants = useMotionVariants(pop);
  const listId = useId();
  const options = filter.options ?? [];
  const editable = filter.kind === "single" && options.length > 0;

  function choose(value: string) {
    close();
    if (value !== filter.value) onChange(filter.key, value);
  }

  function onTrigger() {
    // Open towards the side with room, decided once at open (no layout shift).
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setAlignRight(rect.left + PANEL_WIDTH_PX > window.innerWidth - EDGE_PX);
    toggle();
  }

  const remove = (
    <button
      type="button"
      onClick={() => onChange(filter.key, resetValue(filter))}
      aria-label={`Remove filter ${text}`}
      title={`Remove filter ${text}`}
      className={`mr-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-brand-700 transition-colors hover:bg-brand-100 dark:text-brand-300 dark:hover:bg-brand-900/60 ${focusRing}`}
    >
      <X className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
    </button>
  );

  if (!editable) {
    return (
      <span data-testid="filter-chip" className={`${chipShell} py-0.5 pl-2.5`}>
        <span title={text} className="min-w-0 truncate">
          {text}
        </span>
        {remove}
      </span>
    );
  }

  return (
    <span data-testid="filter-chip" className={`relative ${chipShell}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={onTrigger}
        onKeyDown={menu.triggerProps.onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${text}, change filter`}
        title={text}
        className={`inline-flex min-w-0 items-center gap-1 rounded-full py-0.5 pl-2.5 pr-1 transition-colors hover:bg-brand-100 dark:hover:bg-brand-900/60 ${focusRing}`}
      >
        <span className="min-w-0 truncate">{text}</span>
        <ChevronDown className="h-3 w-3 shrink-0 opacity-70" strokeWidth={2} aria-hidden="true" />
      </button>
      {remove}
      <AnimatePresence>
        {open ? (
          <m.div
            ref={panelRef}
            id={listId}
            role="listbox"
            aria-label={filter.label}
            {...menu.panelProps}
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            style={{ width: PANEL_WIDTH_PX }}
            className={`absolute top-full z-50 mt-1.5 max-w-[calc(100vw-2rem)] ${alignRight ? "right-0 origin-top-right" : "left-0 origin-top-left"} ${popoverPanel} outline-hidden`}
          >
            {options.map((option) => {
              const selected = option.value === filter.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  tabIndex={-1}
                  onClick={() => choose(option.value)}
                  className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors ${focusRing} ${
                    selected
                      ? "bg-brand-50 font-semibold text-brand-700 dark:bg-brand-950/60 dark:text-brand-300"
                      : "text-fg-muted hover:bg-surface-muted hover:text-fg"
                  }`}
                >
                  <span className="min-w-0 truncate">{option.label}</span>
                  {selected ? <Check className="h-3.5 w-3.5 shrink-0" strokeWidth={2} aria-hidden="true" /> : null}
                </button>
              );
            })}
          </m.div>
        ) : null}
      </AnimatePresence>
    </span>
  );
}
