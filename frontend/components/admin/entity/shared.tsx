"use client";

import React from "react";
import { X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { rangeUtcDays, useRange } from "@/lib/range-context";
import { focusRing } from "@/components/ui/styles";
import type { RangeParams } from "@/lib/entity-stats-api";

/** Shared helpers for the entity pages (Visitors, Conversations, Documents). */

export function errorMessage(error: unknown, fallback: string): string | null {
  if (error === undefined || error === null) return null;
  if (error instanceof ApiError || error instanceof Error) return error.message || fallback;
  return fallback;
}

const dayFmt = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short" });

/** `2026-10-09` -> `9 Oct` (parsed as a calendar day, no timezone shift). */
export function formatDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return dayFmt.format(new Date(y, m - 1, d));
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });

export function formatUsd(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : usd.format(value);
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

/** The global range (admin layout) as inclusive UTC `from` / `to` days; empty outside a RangeProvider. */
export function useRangeParams(): RangeParams & { label: string | null } {
  const ctx = useRange();
  if (!ctx) return { label: null };
  return { ...rangeUtcDays(ctx.range), label: ctx.range.label };
}

/** Removable chip for a filter set from a chart (heatmap cell, histogram bin, bar segment). */
export function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-brand-300 bg-brand-50 py-0.5 pl-2.5 pr-1 text-[11px] font-semibold text-brand-700 dark:border-brand-700/60 dark:bg-brand-950/60 dark:text-brand-300">
      {label}
      <button
        type="button"
        onClick={onClear}
        aria-label={`Clear filter: ${label}`}
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full hover:bg-brand-100 dark:hover:bg-brand-900/60 ${focusRing}`}
      >
        <X className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      </button>
    </span>
  );
}

/** Section card used for lists on the detail pages. */
export function SectionCard({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      aria-label={title}
      className={`rounded-xl border border-line bg-surface-card p-5 transition duration-150 hover:border-brand-300 hover:shadow-md motion-reduce:transition-none dark:hover:border-brand-700/60 ${className}`}
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-fg-subtle">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Small rating / tag chip with status tokens. */
export function ToneChip({ tone, children }: { tone: "ok" | "warn" | "danger" | "neutral"; children: React.ReactNode }) {
  const cls = {
    ok: "bg-status-ok-bg text-status-ok-fg",
    warn: "bg-status-warn-bg text-status-warn-fg",
    danger: "bg-status-danger-bg text-status-danger-fg",
    neutral: "bg-surface-muted text-fg-muted",
  }[tone];
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${cls}`}>{children}</span>;
}

export function outcomeTone(outcome: string | null | undefined): "ok" | "warn" | "danger" | "neutral" {
  if (outcome === "answered") return "ok";
  if (outcome === "ungrounded") return "warn";
  if (outcome === "blocked" || outcome === "llm_failed") return "danger";
  return "neutral";
}

export function ratingTone(rating: number): "ok" | "warn" | "danger" {
  if (rating >= 4) return "ok";
  if (rating <= 2) return "danger";
  return "warn";
}
