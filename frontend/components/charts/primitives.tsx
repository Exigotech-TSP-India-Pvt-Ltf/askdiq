"use client";

/**
 * Shared building blocks for the chart kit (newplan §15.2, §15.4).
 *
 * Catalogue charts are hand-drawn SVG (no extra dependency, nothing to
 * lazy-load, testable in jsdom). Every mark goes through `useChartMarks`, so
 * all charts behave the same: one tab stop per chart (roving tabindex), arrow
 * keys move between marks, Enter / Space and click call `onSelect(datum)`,
 * hover or focus shows a card tooltip and dims the other marks.
 */
import type React from "react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

export type SelectHandler<T> = (datum: T) => void;

export interface ChartSize {
  width: number;
  height: number;
}

export const DEFAULT_CHART_WIDTH = 400;
export const DEFAULT_CHART_HEIGHT = 240;

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Measures an element; falls back to a default size where layout is unavailable (SSR, jsdom). */
export function useElementSize<E extends HTMLElement>(fallback: ChartSize): [React.RefObject<E>, ChartSize] {
  const ref = useRef<E>(null);
  const [size, setSize] = useState<ChartSize>(fallback);

  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const apply = (w: number, h: number) => {
      if (w > 0 && h > 0) setSize((s) => (s.width === w && s.height === h ? s : { width: w, height: h }));
    };
    const rect = el.getBoundingClientRect();
    apply(Math.round(rect.width), Math.round(rect.height));
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([entry]) => {
      apply(Math.round(entry.contentRect.width), Math.round(entry.contentRect.height));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return [ref, size];
}

export interface TooltipAnchor {
  x: number;
  y: number;
  content: React.ReactNode;
}

export interface ChartFrameProps {
  /** Accessible name of the whole chart. */
  label: string;
  /** Fixed height in px; omit to fill the parent (e.g. the `ChartCard` body). */
  height?: number;
  className?: string;
  /** Tooltip for the active mark, computed from the measured size. */
  tooltip?: (size: ChartSize) => TooltipAnchor | null;
  children: (size: ChartSize) => React.ReactNode;
  /** HTML rendered under the SVG (legends). */
  footer?: React.ReactNode;
  /** Longer accessible description (e.g. the axis titles), linked via aria-describedby. */
  description?: string;
}

/** Responsive SVG canvas with a themed tooltip card. */
export function ChartFrame({ label, height, className = "", tooltip, children, footer, description }: ChartFrameProps) {
  const descId = useId();
  const [ref, size] = useElementSize<HTMLDivElement>({
    width: DEFAULT_CHART_WIDTH,
    height: height ?? DEFAULT_CHART_HEIGHT,
  });
  const anchor = tooltip?.(size) ?? null;
  return (
    <div className={`flex w-full flex-col ${height === undefined ? "h-full" : ""} ${className}`}>
      <div
        ref={ref}
        className="relative min-h-0 w-full flex-1"
        style={height === undefined ? undefined : { height }}
      >
        <svg
          role="group"
          aria-label={label}
          aria-describedby={description ? descId : undefined}
          width={size.width}
          height={size.height}
          viewBox={`0 0 ${size.width} ${size.height}`}
          className="block overflow-visible"
        >
          {description && <desc id={descId}>{description}</desc>}
          {children(size)}
        </svg>
        {anchor && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-xl border border-line bg-surface-elevated px-3 py-2 text-xs text-fg shadow-md"
            style={{ left: anchor.x, top: anchor.y - 8 }}
          >
            {anchor.content}
          </div>
        )}
      </div>
      {footer}
    </div>
  );
}

export interface TooltipRow {
  label: string;
  value: string;
  color?: string;
}

/** Standard tooltip content: a title and coloured rows. */
export function TooltipBody({
  title,
  rows,
  caption,
}: {
  title?: string;
  rows: readonly TooltipRow[];
  /** Small measure / unit line under the title, e.g. "Queries per day". */
  caption?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {title && <p className={`${caption ? "" : "mb-0.5 "}font-semibold text-fg-muted`}>{title}</p>}
      {caption && <p className="mb-0.5 text-[10px] text-fg-subtle">{caption}</p>}
      {rows.map((r) => (
        <p key={r.label} className="flex items-center gap-2">
          {r.color && <span className="h-2 w-2 rounded-full" style={{ backgroundColor: r.color }} aria-hidden="true" />}
          <span className="text-fg-muted">{r.label}</span>
          <span className="ml-auto pl-2 font-semibold tabular-nums">{r.value}</span>
        </p>
      ))}
    </div>
  );
}

/** Message shown when a chart gets no data. */
export function ChartEmpty({ message = "No data for this period", height }: { message?: string; height?: number }) {
  return (
    <div
      role="status"
      className={`flex w-full items-center justify-center rounded-xl border border-dashed border-line-brand/70 bg-surface-studio/50 px-4 text-center text-xs text-fg-subtle dark:border-brand-500/20 dark:bg-white/[0.02] ${
        height === undefined ? "h-full min-h-[96px]" : ""
      }`}
      style={height === undefined ? undefined : { height }}
    >
      {message}
    </div>
  );
}

const PREV_KEYS = new Set(["ArrowLeft", "ArrowUp"]);
const NEXT_KEYS = new Set(["ArrowRight", "ArrowDown"]);

export interface MarkProps {
  ref: (el: SVGElement | null) => void;
  role: "button" | "img";
  tabIndex?: number;
  "aria-label": string;
  className: string;
  style?: React.CSSProperties;
  onClick?: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
}

export interface ChartMarks {
  /** Index under the pointer or keyboard focus (tooltip, dimming), else null. */
  active: number | null;
  getMarkProps: (index: number) => MarkProps;
  /** Opacity for mark `index`: dimmed while another mark is active. */
  opacityFor: (index: number) => number;
}

/**
 * Interaction contract shared by every catalogue chart: click / Enter / Space
 * call `onSelect`, arrows move focus (roving tabindex), hover and focus set
 * the active mark.
 */
export function useChartMarks<T>(
  data: readonly T[],
  describe: (datum: T, index: number) => string,
  onSelect: SelectHandler<T> | undefined,
  dimOpacity: number,
): ChartMarks {
  const [active, setActive] = useState<number | null>(null);
  const [tabStop, setTabStop] = useState(0);
  const refs = useRef<(SVGElement | null)[]>([]);
  const count = data.length;
  const safeTabStop = tabStop < count ? tabStop : 0;

  const focusMark = useCallback((i: number) => {
    setTabStop(i);
    refs.current[i]?.focus();
  }, []);

  const getMarkProps = useCallback(
    (i: number): MarkProps => {
      const datum = data[i];
      const select = onSelect ? () => onSelect(datum) : undefined;
      return {
        ref: (el) => {
          refs.current[i] = el;
        },
        role: select ? "button" : "img",
        tabIndex: select ? (i === safeTabStop ? 0 : -1) : undefined,
        "aria-label": describe(datum, i),
        className: "chart-mark",
        style: select ? { cursor: "pointer" } : undefined,
        onClick: select,
        onKeyDown: (e) => {
          if (select && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            select();
          } else if (NEXT_KEYS.has(e.key) && count > 0) {
            e.preventDefault();
            focusMark((i + 1) % count);
          } else if (PREV_KEYS.has(e.key) && count > 0) {
            e.preventDefault();
            focusMark((i - 1 + count) % count);
          } else if (e.key === "Home" && count > 0) {
            e.preventDefault();
            focusMark(0);
          } else if (e.key === "End" && count > 0) {
            e.preventDefault();
            focusMark(count - 1);
          }
        },
        onMouseEnter: () => setActive(i),
        onMouseLeave: () => setActive(null),
        onFocus: () => {
          setActive(i);
          setTabStop(i);
        },
        onBlur: () => setActive(null),
      };
    },
    [count, data, describe, focusMark, onSelect, safeTabStop],
  );

  const opacityFor = useCallback(
    (i: number) => (active === null || active === i ? 1 : dimOpacity),
    [active, dimOpacity],
  );

  return { active, getMarkProps, opacityFor };
}

/** Linear scale from `[d0, d1]` to `[r0, r1]`; a zero-width domain maps to the range middle. */
export function scaleLinear(domain: [number, number], range: [number, number]): (v: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** Rounds a maximum up to 1, 2, 2.5 or 5 x 10^n so axes end on a readable tick. */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(value));
  const f = value / exp;
  const nice = [1, 2, 2.5, 5, 10].find((n) => f <= n) ?? 10;
  return nice * exp;
}

/** Evenly spaced ticks from 0 to `max` (inclusive). */
export function ticks(max: number, count = 4): number[] {
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i);
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export function formatCompact(value: number): string {
  return compact.format(value);
}

/** Picks a colour from a ramp for `value` in `[0, max]`. */
export function rampColor(ramp: readonly string[], value: number, max: number): string {
  if (ramp.length === 0) return "currentColor";
  if (max <= 0) return ramp[0];
  const idx = Math.min(ramp.length - 1, Math.max(0, Math.floor((value / max) * ramp.length)));
  return ramp[idx];
}

export const AXIS_FONT_SIZE = 11;

/** Approximate glyph width of axis text, as a share of the font size. */
const CHAR_WIDTH_RATIO = 0.58;

/**
 * Cuts `text` with an ellipsis so it fits roughly `maxWidth` px at
 * `fontSize` (SVG text cannot wrap or ellipsize on its own).
 */
export function fitText(text: string, maxWidth: number, fontSize: number = AXIS_FONT_SIZE): string {
  const maxChars = Math.floor(maxWidth / (fontSize * CHAR_WIDTH_RATIO));
  if (text.length <= maxChars) return text;
  if (maxChars <= 1) return "…";
  return `${text.slice(0, maxChars - 1).trimEnd()}…`;
}
