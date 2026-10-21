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
}

/** Extra left / bottom inset for the axis titles that are set. */
export function titleInsets(titles: AxisLabelProps = {}): { left: number; bottom: number } {
  return {
    left: titles.yAxisLabel ? AXIS_TITLE_SPACE : 0,
    bottom: titles.xAxisLabel ? AXIS_TITLE_SPACE : 0,
  };
}

export function plotArea(width: number, height: number, titles?: AxisLabelProps): PlotArea {
  const inset = titleInsets(titles);
  const left = MARGIN.left + inset.left;
  const bottom = MARGIN.bottom + inset.bottom;
  return {
    x0: left,
    x1: Math.max(left + 1, width - MARGIN.right),
    y0: MARGIN.top,
    y1: Math.max(MARGIN.top + 1, height - bottom),
  };
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

/** Category labels under the plot; thinned by the available width so they never overlap. */
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
  /** Optional upper bound on the number of labels (the width rule always applies). */
  maxLabels?: number;
}) {
  const widest = labels.reduce((w, l) => Math.max(w, textWidth(l)), 0);
  const spacing = labels.length > 1 ? Math.abs(xAt(1) - xAt(0)) : area.x1 - area.x0;
  const shown = new Set(xTickIndices(labels.length, spacing, widest, maxLabels));
  return (
    <g aria-hidden="true">
      {labels.map((label, i) =>
        shown.has(i) ? (
          <text
            key={i}
            x={xAt(i)}
            y={area.y1 + 15}
            textAnchor="middle"
            fontSize={AXIS_FONT_SIZE}
            fill={ct.axis}
          >
            {label}
          </text>
        ) : null,
      )}
    </g>
  );
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
