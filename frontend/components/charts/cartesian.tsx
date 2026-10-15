"use client";

import type { ChartTheme } from "@/lib/theme-tokens";
import { AXIS_FONT_SIZE, formatCompact, scaleLinear, ticks } from "./primitives";

/** Plot margins shared by the cartesian charts (room for axis labels). */
export const MARGIN = { top: 10, right: 10, bottom: 22, left: 40 } as const;

export interface PlotArea {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

export function plotArea(width: number, height: number): PlotArea {
  return {
    x0: MARGIN.left,
    x1: Math.max(MARGIN.left + 1, width - MARGIN.right),
    y0: MARGIN.top,
    y1: Math.max(MARGIN.top + 1, height - MARGIN.bottom),
  };
}

/** Horizontal gridlines with value labels on the left. */
export function YGrid({
  area,
  max,
  ct,
  format = formatCompact,
  min = 0,
}: {
  area: PlotArea;
  max: number;
  ct: ChartTheme;
  format?: (v: number) => string;
  min?: number;
}) {
  const y = scaleLinear([min, max], [area.y1, area.y0]);
  return (
    <g aria-hidden="true">
      {ticks(max - min).map((t) => {
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

/** Category labels under the plot; thins them out so they never overlap. */
export function XLabels({
  labels,
  xAt,
  area,
  ct,
  maxLabels = 7,
}: {
  labels: readonly string[];
  xAt: (index: number) => number;
  area: PlotArea;
  ct: ChartTheme;
  maxLabels?: number;
}) {
  const every = Math.max(1, Math.ceil(labels.length / maxLabels));
  return (
    <g aria-hidden="true">
      {labels.map((label, i) =>
        i % every === 0 || i === labels.length - 1 ? (
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

/** HTML legend; hovering an item dims the other series (newplan §15.2). */
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
