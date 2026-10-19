/**
 * Governance display helpers (G1, G7). The API always sends milliseconds;
 * these pick ms or s per value so an axis never shows `0.00018 s` and labels
 * never clip.
 */
import { parseServerDate } from "@/lib/time";

const MS_PER_S = 1000;
/** Approximate width of one 11 px axis glyph, plus tick padding. */
const AXIS_CHAR_PX = 6.6;
const AXIS_PAD_PX = 14;
const AXIS_MIN_PX = 36;

function trim(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}

/** `0.18 → "0.18 ms"`, `42.5 → "42.5 ms"`, `1200 → "1.2 s"`, `null → "—"`. */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const abs = Math.abs(ms);
  if (abs === 0) return "0 ms";
  if (abs < 1) return `${trim(ms.toFixed(2))} ms`;
  if (abs < 10) return `${trim(ms.toFixed(1))} ms`;
  if (abs < MS_PER_S) return `${Math.round(ms)} ms`;
  const s = ms / MS_PER_S;
  return `${trim(s.toFixed(Math.abs(s) < 10 ? 1 : 0))} s`;
}

/** Axis tick formatter for latency charts (alias kept for clarity at call sites). */
export const latencyTick = formatMs;

/** Pixel width that fits the longest tick label (G1: replaces `width={40}`). */
export function axisWidthFor(labels: readonly string[]): number {
  const longest = labels.reduce((max, l) => Math.max(max, l.length), 0);
  return Math.max(AXIS_MIN_PX, Math.ceil(longest * AXIS_CHAR_PX + AXIS_PAD_PX));
}

/** "Nice" upper bound and `count + 1` evenly spaced ticks from 0. */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const raw = max / count;
  const exp = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((n) => raw / exp <= n) ?? 10) * exp;
  return Array.from({ length: count + 1 }, (_, i) => Number((step * i).toPrecision(12)));
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const integer = new Intl.NumberFormat("en");

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return Math.abs(value) >= 10_000 ? compact.format(value) : integer.format(Math.round(value));
}

/** Full value with "en" digit grouping (19,452,869), e.g. for a compact value's `title`. */
export function formatCountExact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return integer.format(Math.round(value));
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(value !== 0 && Math.abs(value) < 1 ? 4 : 2)}`;
}

/** Formats a KPI by its unit (`%`, `ms`, `USD` or a plain count). */
export function formatByUnit(value: number | null | undefined, unit: string | null | undefined): string {
  switch (unit) {
    case "%":
      return formatPct(value);
    case "ms":
      return formatMs(value);
    case "USD":
      return formatUsd(value);
    default:
      return formatCount(value);
  }
}

const SOURCE_LABELS: Record<string, string> = {
  in_memory_counter: "process counter",
  latency_metrics: "latency records",
  // Overall p50 / p95: queries that ran the RAG pipeline (conversational replies excluded).
  "latency_metrics.pipeline": "RAG queries",
  chat_sessions: "chat sessions",
  chat_messages: "chat turns",
};

const WINDOW_LABELS: Record<string, string> = {
  since_process_start: "since restart",
  all_time: "all time",
  now: "right now",
  today_utc: "since 00:00 UTC",
};

/** "24h · chat turns" — where a count comes from and over which window (G7). */
export function windowSourceLabel(window: string, source: string): string {
  const w = WINDOW_LABELS[window] ?? window;
  const s = source
    .split("+")
    .map((part) => SOURCE_LABELS[part] ?? part.replace(/_/g, " "))
    .join(" + ");
  return `${w} · ${s}`;
}

const timeFormat = new Intl.DateTimeFormat("en-AU", { hour: "2-digit", minute: "2-digit" });
const dateTimeFormat = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function formatHour(iso: string): string {
  return timeFormat.format(parseServerDate(iso));
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = parseServerDate(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormat.format(date);
}

/** Start of the hour containing `iso` (local Date), for incident → hour markers. */
export function hourStart(iso: string): Date {
  const d = parseServerDate(iso);
  d.setMinutes(0, 0, 0);
  return d;
}

export function humanize(value: string): string {
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
