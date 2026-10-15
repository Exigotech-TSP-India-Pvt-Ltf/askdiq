"use client";

import React, { createContext, useCallback, useContext, useMemo } from "react";
import { useDeepLink } from "@/lib/use-deep-link";

/**
 * Global date range (newplan §15.1), shared like `AudienceProvider` and kept
 * in the URL so links and back / forward restore it:
 *   preset  -> `?range=24h|30d|90d` (7d is the default and leaves the URL clean)
 *   custom  -> `?from=YYYY-MM-DD&to=YYYY-MM-DD`
 * Invalid or out-of-bounds values are clamped, never trusted.
 */
export type RangePreset = "24h" | "7d" | "30d" | "90d";
export type RangeKind = RangePreset | "custom";

export const RANGE_PRESETS: readonly RangePreset[] = ["24h", "7d", "30d", "90d"] as const;
export const DEFAULT_PRESET: RangePreset = "7d";

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
/** Longest custom window the dashboards accept. */
export const MAX_RANGE_DAYS = 365;

const PRESET_MS: Record<RangePreset, number> = {
  "24h": DAY_MS,
  "7d": 7 * DAY_MS,
  "30d": 30 * DAY_MS,
  "90d": 90 * DAY_MS,
};

export const PRESET_LABEL: Record<RangePreset, string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
};

export interface DateRange {
  kind: RangeKind;
  from: Date;
  to: Date;
  /** Human label for KPI captions, e.g. "Last 7 days" or "1 Oct – 14 Oct 2026". */
  label: string;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function isRangePreset(value: unknown): value is RangePreset {
  return typeof value === "string" && (RANGE_PRESETS as readonly string[]).includes(value);
}

/** `YYYY-MM-DD` in local time. */
export function toIsoDay(date: Date): string {
  const y = date.getFullYear();
  const mo = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${mo}-${d}`;
}

function parseIsoDay(value: string | null): Date | null {
  if (!value || !ISO_DAY.test(value)) return null;
  const [y, mo, d] = value.split("-").map(Number);
  const date = new Date(y, mo - 1, d);
  // Rejects rollovers such as 2026-02-31.
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return date;
}

const dayFormat = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short", year: "numeric" });

export function presetRange(preset: RangePreset, now: Date = new Date()): DateRange {
  return { kind: preset, from: new Date(now.getTime() - PRESET_MS[preset]), to: now, label: PRESET_LABEL[preset] };
}

/**
 * Clamps a custom range: swaps reversed ends, caps `to` at today, and limits
 * the span to MAX_RANGE_DAYS. Both ends are whole local days (to is inclusive).
 */
export function clampCustomRange(from: Date, to: Date, now: Date = new Date()): DateRange {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let start = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  let end = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  if (start > end) [start, end] = [end, start];
  if (end > today) end = today;
  if (start > end) start = end;
  const earliest = new Date(end.getTime() - (MAX_RANGE_DAYS - 1) * DAY_MS);
  if (start < earliest) start = new Date(earliest.getFullYear(), earliest.getMonth(), earliest.getDate());
  const label = `${dayFormat.format(start)} – ${dayFormat.format(end)}`;
  return { kind: "custom", from: start, to: end, label };
}

/** Reads the range from URL params, falling back to the default preset. */
export function rangeFromParams(params: URLSearchParams, now: Date = new Date()): DateRange {
  const from = parseIsoDay(params.get("from"));
  const to = parseIsoDay(params.get("to"));
  if (from && to) return clampCustomRange(from, to, now);
  const preset = params.get("range");
  return presetRange(isRangePreset(preset) ? preset : DEFAULT_PRESET, now);
}

interface RangeContextValue {
  range: DateRange;
  setPreset: (preset: RangePreset) => void;
  setCustom: (from: Date, to: Date) => void;
}

const RangeContext = createContext<RangeContextValue | null>(null);

export function RangeProvider({ children }: { children: React.ReactNode }) {
  const { search, set } = useDeepLink();
  const range = useMemo(() => rangeFromParams(new URLSearchParams(search)), [search]);

  const setPreset = useCallback(
    (preset: RangePreset) => set({ range: preset === DEFAULT_PRESET ? null : preset, from: null, to: null }),
    [set],
  );

  const setCustom = useCallback(
    (from: Date, to: Date) => {
      const clamped = clampCustomRange(from, to);
      set({ range: null, from: toIsoDay(clamped.from), to: toIsoDay(clamped.to) });
    },
    [set],
  );

  const value = useMemo(() => ({ range, setPreset, setCustom }), [range, setPreset, setCustom]);
  return <RangeContext.Provider value={value}>{children}</RangeContext.Provider>;
}

/** The global range, or null outside a RangeProvider (the picker hides itself). */
export function useRange(): RangeContextValue | null {
  return useContext(RangeContext);
}
