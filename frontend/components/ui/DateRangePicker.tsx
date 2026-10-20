"use client";

import React, { useEffect, useId, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { CalendarRange, Check, ChevronDown } from "lucide-react";
import { pop, useMotionVariants } from "@/lib/motion";
import { usePopover } from "@/lib/use-popover";
import { PRESET_LABEL, RANGE_PRESETS, toIsoDay, useRange } from "@/lib/range-context";
import { focusRing, menuItem, popoverPanel, primaryButton } from "@/components/ui/styles";

function parseInputDay(value: string): Date | null {
  if (!value) return null;
  const [y, mo, d] = value.split("-").map(Number);
  const date = new Date(y, mo - 1, d);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Global date-range control for `DashboardHeader`: presets
 * 24h / 7d / 30d / 90d plus a custom range, all synced to the URL by
 * `RangeProvider`. Renders nothing outside a RangeProvider.
 */
export function DateRangePicker({ className = "" }: { className?: string }) {
  const ctx = useRange();
  const { open, toggle, close, triggerRef, panelRef } = usePopover();
  const variants = useMotionVariants(pop);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const fromId = useId();
  const toId = useId();
  const panelId = useId();

  const range = ctx?.range;
  useEffect(() => {
    if (!open || !range) return;
    setFrom(toIsoDay(range.from));
    setTo(toIsoDay(range.to));
  }, [open, range]);

  if (!ctx || !range) return null;
  const { setPreset, setCustom } = ctx;
  const today = toIsoDay(new Date());

  function applyCustom(event: React.FormEvent) {
    event.preventDefault();
    const start = parseInputDay(from);
    const end = parseInputDay(to);
    if (!start || !end) return;
    setCustom(start, end);
    close();
  }

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={`Date range: ${range.label}`}
        className={`inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface-card px-2.5 text-xs font-medium text-fg-muted shadow-sm transition-colors hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 ${focusRing}`}
      >
        <CalendarRange className="h-4 w-4 text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
        <span className="max-w-[11rem] truncate tabular-nums">{range.label}</span>
        <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      </button>

      <AnimatePresence>
        {open ? (
          <m.div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="Choose date range"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`absolute right-0 z-50 mt-2 w-64 origin-top-right ${popoverPanel}`}
          >
            <div role="group" aria-label="Presets" className="space-y-0.5">
              {RANGE_PRESETS.map((preset) => {
                const active = range.kind === preset;
                return (
                  <button
                    key={preset}
                    type="button"
                    aria-pressed={active}
                    onClick={() => {
                      setPreset(preset);
                      close();
                    }}
                    className={`${menuItem} justify-between ${active ? "bg-brand-50 font-semibold text-brand-700 dark:bg-brand-950/60 dark:text-brand-300" : ""}`}
                  >
                    {PRESET_LABEL[preset]}
                    {active ? <Check className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" /> : null}
                  </button>
                );
              })}
            </div>
            <form onSubmit={applyCustom} noValidate className="mt-1.5 space-y-2 border-t border-line px-1.5 pb-1 pt-2.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">Custom range</p>
              <div className="grid grid-cols-2 gap-2">
                <label htmlFor={fromId} className="space-y-1 text-[11px] text-fg-muted">
                  <span className="block">From</span>
                  <input
                    id={fromId}
                    type="date"
                    value={from}
                    max={to || today}
                    onChange={(e) => setFrom(e.target.value)}
                    className={`w-full rounded-md border border-line bg-surface-card px-2 py-1 text-xs text-fg ${focusRing}`}
                  />
                </label>
                <label htmlFor={toId} className="space-y-1 text-[11px] text-fg-muted">
                  <span className="block">To</span>
                  <input
                    id={toId}
                    type="date"
                    value={to}
                    min={from || undefined}
                    max={today}
                    onChange={(e) => setTo(e.target.value)}
                    className={`w-full rounded-md border border-line bg-surface-card px-2 py-1 text-xs text-fg ${focusRing}`}
                  />
                </label>
              </div>
              <button type="submit" disabled={!from || !to} className={`${primaryButton} w-full py-1.5`}>
                Apply range
              </button>
            </form>
          </m.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
