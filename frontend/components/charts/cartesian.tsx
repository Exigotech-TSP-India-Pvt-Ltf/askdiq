"use client";

import type { ChartTheme } from "@/lib/theme-tokens";
import { AXIS_FONT_SIZE, axisTicks, fitText, formatCompact, scaleLinear, textWidth } from "./primitives";

/** Plot margins shared by the cartesian charts (room for axis labels). */
export const MARGIN = { top: 10, right: 10, bottom: 22, left: 40 } as const;

/** Extra room reserved for an axis title (only when one is set). */
export const AXIS_TITLE_SPACE = 16;

/**
 * Axis titles shared by the chart kit. Both are optional; the plot margin
 * only grows when a title is set, so charts without titles are unchanged.
 * Include the unit, e.g. `yAxisLabel="Queries per day"`, `xAxisLabel="Date"`.
 */
export interface AxisLabelProps {
  /** Title under the horizontal axis (category / time axis on column charts, value axis on bar-row charts). */
  xAxisLabel?: string;
  /** Rotated title left of the vertical axis (value axis on column charts, category axis on bar-row charts). */
  yAxisLabel?: string;
}

export interface PlotArea {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  /** How the category labels are drawn (set when `plotArea` was given them). */
  xLabels?: XLabelLayout;
}

/** The category labels a chart draws under its plot, and how they are spaced. */
export interface XLabelInput {
  labels: readonly string[];
  /** `point`: one label per point, ends on the plot edges; `band`: one per column. */
  placement: "point" | "band";
  /** Optional upper bound on the number of labels (non-date axes, e.g. histogram bins). */
  maxLabels?: number;
}

/** Extra left / bottom inset for the axis titles that are set. */
export function titleInsets(titles: AxisLabelProps = {}): { left: number; bottom: number } {
  return {
    left: titles.yAxisLabel ? AXIS_TITLE_SPACE : 0,
    bottom: titles.xAxisLabel ? AXIS_TITLE_SPACE : 0,
  };
}

/**
 * The plot rectangle. With `xLabels`, the category labels are laid out for
 * the plot width (`layoutXLabels`) and rotated labels get their bottom space.
 */
export function plotArea(width: number, height: number, titles?: AxisLabelProps, xLabels?: XLabelInput): PlotArea {
  const inset = titleInsets(titles);
  const left = MARGIN.left + inset.left;
  const bottom = MARGIN.bottom + inset.bottom;
  const x0 = left;
  const x1 = Math.max(left + 1, width - MARGIN.right);
  const base = height - bottom;
  if (!xLabels) return { x0, x1, y0: MARGIN.top, y1: Math.max(MARGIN.top + 1, base) };
  const n = xLabels.labels.length;
  const slots = xLabels.placement === "band" ? n : n - 1;
  const spacing = slots > 0 ? (x1 - x0) / slots : x1 - x0;
  const layout = layoutXLabels(xLabels.labels, spacing, xLabels.maxLabels);
  return { x0, x1, y0: MARGIN.top, y1: Math.max(MARGIN.top + 1, base - layout.extraBottom), xLabels: layout };
}

/**
 * SVG axis titles: the x title centred under the tick labels (bottom edge of
 * the chart), the y title rotated on the left edge. Long titles are cut with
 * an ellipsis to the space available; the full text stays in a native
 * tooltip and in the chart's accessible description.
 */
export function AxisTitles({
  xAxisLabel,
  yAxisLabel,
  area,
  height,
  ct,
}: AxisLabelProps & { area: PlotArea; height: number; ct: ChartTheme }) {
  if (!xAxisLabel && !yAxisLabel) return null;
  const midX = (area.x0 + area.x1) / 2;
  const midY = (area.y0 + area.y1) / 2;
  const yX = AXIS_TITLE_SPACE / 2;
  return (
    <g data-testid="chart-axis-titles" aria-hidden="true">
      {xAxisLabel && (
        <text
          data-axis="x"
          x={midX}
          y={height - 3}
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fontWeight={500}
          fill={ct.axis}
        >
          <title>{xAxisLabel}</title>
          {fitText(xAxisLabel, Math.max(0, area.x1 - area.x0))}
        </text>
      )}
      {yAxisLabel && (
        <text
          data-axis="y"
          x={yX}
          y={midY}
          dy="0.32em"
          textAnchor="middle"
          fontSize={AXIS_FONT_SIZE}
          fontWeight={500}
          fill={ct.axis}
          transform={`rotate(-90 ${yX} ${midY})`}
        >
          <title>{yAxisLabel}</title>
          {fitText(yAxisLabel, Math.max(0, area.y1 - area.y0))}
        </text>
      )}
    </g>
  );
}

/** Accessible description of the axes ("X axis: Date. Y axis: Queries."), or undefined. */
export function axisDescription(titles: AxisLabelProps): string | undefined {
  const parts = [
    titles.xAxisLabel ? `X axis: ${titles.xAxisLabel}.` : null,
    titles.yAxisLabel ? `Y axis: ${titles.yAxisLabel}.` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" ") : undefined;
}

/** Horizontal gridlines with value labels on the left. */
export function YGrid({
  area,
  max,
  ct,
  format = formatCompact,
  min = 0,
  integer = false,
}: {
  area: PlotArea;
  max: number;
  ct: ChartTheme;
  format?: (v: number) => string;
  min?: number;
  /** Count series: whole-number ticks only (see `isIntegerSeries`). */
  integer?: boolean;
}) {
  const y = scaleLinear([min, max], [area.y1, area.y0]);
  return (
    <g aria-hidden="true">
      {axisTicks(max - min, integer).map((t) => {
        const v = min + t;
        return (
          <g key={v}>
            <line x1={area.x0} x2={area.x1} y1={y(v)} y2={y(v)} stroke={ct.grid} strokeDasharray="3 3" />
            <text
              x={area.x0 - 6}
              y={y(v)}
              dy="0.32em"
              textAnchor="end"
              fontSize={AXIS_FONT_SIZE}
              fill={ct.axis}
            >
              {format(v)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** Minimum free space between two category labels (px). */
export const X_LABEL_GAP = 10;

/** Minimum free space between two compact date labels ("8", "9", ...). */
export const COMPACT_LABEL_GAP = 4;

/** Up to this many categories every label is drawn (compact or rotated); beyond it labels thin out. */
export const MAX_ALL_LABELS = 31;

/** Rotated labels slope at -45 degrees. */
export const X_LABEL_ROTATION = -45;

/** Baseline of horizontal labels / anchor of rotated ones, below the plot. */
const LABEL_BASELINE = 15;
const ROTATED_ANCHOR = 9;
const SIN_45 = Math.SQRT1_2;
const ROTATED_LINE_EM = 0.9;

/**
 * - `full`: every label as given;
 * - `compact`: every date label as its day ("8 Oct", "9", "10", ..., "1 Nov"),
 *   the month only on the first label and where it changes;
 * - `rotated`: every label, sloped at -45 degrees, with bottom space reserved;
 * - `thinned`: last resort beyond `MAX_ALL_LABELS` (or an explicit cap): a
 *   width-based subset (`xTickIndices`).
 */
export type XLabelMode = "full" | "compact" | "rotated" | "thinned";

export interface XLabelLayout {
  mode: XLabelMode;
  /** Text drawn for each category (compact form in `compact` mode). */
  texts: readonly string[];
  /** Indices of the categories that get a label. */
  shown: readonly number[];
  /** Extra bottom space the labels need beyond the default margin (px). */
  extraBottom: number;
}

const MONTHS = new Set(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"]);
const DAY_FIRST = /^(\d{1,2})\s+([A-Za-z]{3,4})\.?$/;
const MONTH_FIRST = /^([A-Za-z]{3,4})\.?\s+(\d{1,2})$/;

interface DayParts {
  day: string;
  month: string;
  dayFirst: boolean;
}

function dayParts(label: string): DayParts | null {
  const trimmed = label.trim();
  const a = DAY_FIRST.exec(trimmed);
  if (a && MONTHS.has(a[2].toLowerCase())) return { day: a[1], month: a[2], dayFirst: true };
  const b = MONTH_FIRST.exec(trimmed);
  if (b && MONTHS.has(b[1].toLowerCase())) return { day: b[2], month: b[1], dayFirst: false };
  return null;
}

/**
 * Compact day labels ("8 Oct", "9", "10", ..., "1 Nov") for a run of day
 * labels such as "8 Oct" or "Oct 8"; null when any label is not a day.
 */
export function compactDayLabels(labels: readonly string[]): string[] | null {
  const parts = labels.map(dayParts);
  if (parts.length === 0 || parts.some((p) => p === null)) return null;
  const days = parts as DayParts[];
  return days.map((p, i) => {
    if (i > 0 && days[i - 1].month === p.month) return p.day;
    return p.dayFirst ? `${p.day} ${p.month}` : `${p.month} ${p.day}`;
  });
}

/** True when neighbouring labels, centred `spacing` px apart, leave `gap` px between them. */
function neighboursFit(texts: readonly string[], spacing: number, gap: number): boolean {
  for (let i = 1; i < texts.length; i += 1) {
    if ((textWidth(texts[i - 1]) + textWidth(texts[i])) / 2 + gap > spacing) return false;
  }
  return true;
}

/** Sloped labels fit while parallel baselines stay a glyph height (~0.9 em) apart. */
function rotatedFits(spacing: number): boolean {
  return spacing * SIN_45 >= AXIS_FONT_SIZE * ROTATED_LINE_EM;
}

/** Bottom space sloped labels need beyond the default margin. */
function rotatedExtraBottom(texts: readonly string[]): number {
  const widest = texts.reduce((w, t) => Math.max(w, textWidth(t)), 0);
  const drop = ROTATED_ANCHOR + (widest + AXIS_FONT_SIZE) * SIN_45;
  return Math.max(0, Math.ceil(drop + 2 - MARGIN.bottom));
}

function thinned(labels: readonly string[], spacing: number, maxLabels?: number): XLabelLayout {
  const widest = labels.reduce((w, l) => Math.max(w, textWidth(l)), 0);
  return { mode: "thinned", texts: labels, shown: xTickIndices(labels.length, spacing, widest, maxLabels), extraBottom: 0 };
}

/**
 * How to draw `labels` whose centres sit `spacing` px apart. Up to
 * `MAX_ALL_LABELS` categories every label is drawn: as given when they fit,
 * else compact (dates), else rotated; labels are only dropped beyond that
 * (or above an explicit `maxLabels` cap).
 */
export function layoutXLabels(labels: readonly string[], spacing: number, maxLabels?: number): XLabelLayout {
  const n = labels.length;
  const every = Array.from({ length: n }, (_, i) => i);
  const full: XLabelLayout = { mode: "full", texts: labels, shown: every, extraBottom: 0 };
  if (n <= 1) return full;
  const capped = maxLabels !== undefined && maxLabels > 0 && n > maxLabels;
  if (capped || n > MAX_ALL_LABELS) return thinned(labels, spacing, maxLabels);
  if (neighboursFit(labels, spacing, X_LABEL_GAP)) return full;
  const compact = compactDayLabels(labels);
  if (compact && neighboursFit(compact, spacing, COMPACT_LABEL_GAP)) {
    return { mode: "compact", texts: compact, shown: every, extraBottom: 0 };
  }
  if (rotatedFits(spacing)) {
    return { mode: "rotated", texts: labels, shown: every, extraBottom: rotatedExtraBottom(labels) };
  }
  return thinned(labels, spacing, maxLabels);
}

/**
 * Which category labels to draw so none overlap: the step follows the
 * space each label gets (`spacing`, px between neighbouring categories)
 * and the widest label, so a wide card shows more dates than a narrow
 * one. The first and last labels are always kept; when the last one would
 * crowd the previous stepped label, that label is dropped instead.
 * `maxLabels` optionally caps the count on top of the width rule.
 */
export function xTickIndices(count: number, spacing: number, labelWidth: number, maxLabels?: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [0];
  const need = labelWidth + X_LABEL_GAP;
  let every = spacing > 0 ? Math.max(1, Math.ceil(need / spacing)) : count;
  if (maxLabels && maxLabels > 0) every = Math.max(every, Math.ceil(count / maxLabels));
  const out: number[] = [];
  for (let i = 0; i < count; i += every) out.push(i);
  const last = count - 1;
  if (out[out.length - 1] !== last) {
    if (out.length > 1 && (last - out[out.length - 1]) * spacing < need) out.pop();
    out.push(last);
  }
  return out;
}

/** Draws a label layout under a plot whose bottom edge is at `y`. */
export function XLabelTexts({
  layout,
  xAt,
  y,
  ct,
}: {
  layout: XLabelLayout;
  xAt: (index: number) => number;
  y: number;
  ct: ChartTheme;
}) {
  const rotated = layout.mode === "rotated";
  return (
    <g aria-hidden="true" data-testid="chart-x-labels" data-mode={layout.mode}>
      {layout.shown.map((i) => {
        const x = xAt(i);
        const text = layout.texts[i];
        if (rotated) {
          const ay = y + ROTATED_ANCHOR;
          return (
            <text
              key={i}
              x={x}
              y={ay}
              dy="0.32em"
              textAnchor="end"
              fontSize={AXIS_FONT_SIZE}
              fill={ct.axis}
              transform={`rotate(${X_LABEL_ROTATION} ${x} ${ay})`}
            >
              {text}
            </text>
          );
        }
        return (
          <text key={i} x={x} y={y + LABEL_BASELINE} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
            {text}
          </text>
        );
      })}
    </g>
  );
}

/**
 * Category labels under the plot. Uses the layout `plotArea` computed (so
 * rotated labels have their space); otherwise lays them out for `xAt`
 * without rotating (no bottom space was reserved).
 */
export function XLabels({
  labels,
  xAt,
  area,
  ct,
  maxLabels,
}: {
  labels: readonly string[];
  xAt: (index: number) => number;
  area: PlotArea;
  ct: ChartTheme;
  /** Optional upper bound on the number of labels (non-date axes). */
  maxLabels?: number;
}) {
  let layout = area.xLabels;
  if (layout?.texts.length !== labels.length) {
    const spacing = labels.length > 1 ? Math.abs(xAt(1) - xAt(0)) : area.x1 - area.x0;
    layout = layoutXLabels(labels, spacing, maxLabels);
    if (layout.mode === "rotated") layout = thinned(labels, spacing, maxLabels);
  }
  return <XLabelTexts layout={layout} xAt={xAt} y={area.y1} ct={ct} />;
}

/** Index-to-x mapping for point series; a single point sits in the middle. */
export function pointX(count: number, area: PlotArea): (i: number) => number {
  if (count <= 1) return () => (area.x0 + area.x1) / 2;
  return scaleLinear([0, count - 1], [area.x0, area.x1]);
}

/** SVG path through points. */
export function linePath(points: readonly (readonly [number, number])[]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x},${y}`).join(" ");
}

/** Closed area between an upper and a lower line (same x positions). */
export function bandPath(
  upper: readonly (readonly [number, number])[],
  lower: readonly (readonly [number, number])[],
): string {
  if (upper.length === 0) return "";
  const back = [...lower].reverse();
  return `${linePath(upper)} ${back.map(([x, y]) => `L${x},${y}`).join(" ")} Z`;
}

export interface SeriesDef<K extends string = string> {
  key: K;
  label: string;
  /** Token colour override; defaults to `chartTheme.categorical[i]`. */
  color?: string;
}

export function seriesColor(ct: ChartTheme, series: SeriesDef, index: number): string {
  return series.color ?? ct.categorical[index % ct.categorical.length];
}

/** HTML legend; hovering an item dims the other series. */
export function Legend({
  series,
  ct,
  onHover,
}: {
  series: readonly SeriesDef[];
  ct: ChartTheme;
  onHover?: (key: string | null) => void;
}) {
  return (
    <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-fg-muted">
      {series.map((s, i) => (
        <li
          key={s.key}
          className="flex items-center gap-1.5"
          onMouseEnter={() => onHover?.(s.key)}
          onMouseLeave={() => onHover?.(null)}
        >
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: seriesColor(ct, s, i) }} aria-hidden="true" />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

/** Reads a numeric field from a row (missing or non-numeric = 0). */
export function num(row: object, key: string): number {
  const v = (row as Record<string, unknown>)[key];
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
