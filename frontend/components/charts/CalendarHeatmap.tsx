"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  rampColor,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";

export interface HeatCell {
  row: string;
  col: string;
  value: number;
}

export interface CalendarHeatmapProps {
  data: readonly HeatCell[];
  /** Row order (e.g. Mon..Sun). Defaults to first appearance in `data`. */
  rows?: readonly string[];
  /** Column order (e.g. 0..23). Defaults to first appearance in `data`. */
  cols?: readonly string[];
  onSelect?: SelectHandler<HeatCell>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
  /** Show every n-th column label. Default: about 12 labels. */
  colLabelEvery?: number;
}

const LEFT = 36;
const BOTTOM = 18;
const GAP = 2;

function uniq(values: readonly string[]): string[] {
  return Array.from(new Set(values));
}

/** Weekday x hour (or any 2-D) intensity grid on the sequential teal ramp. */
export function CalendarHeatmap({
  data,
  rows,
  cols,
  onSelect,
  label = "Activity heatmap",
  height,
  formatValue = formatCompact,
  colLabelEvery,
}: CalendarHeatmapProps) {
  const ct = useChartTheme();
  const describe = useCallback(
    (c: HeatCell) => `${c.row} ${c.col}: ${formatValue(c.value)}`,
    [formatValue],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const rowKeys = useMemo(() => rows ?? uniq(data.map((c) => c.row)), [data, rows]);
  const colKeys = useMemo(() => cols ?? uniq(data.map((c) => c.col)), [data, cols]);
  const max = useMemo(() => Math.max(0, ...data.map((c) => c.value)), [data]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const every = colLabelEvery ?? Math.max(1, Math.ceil(colKeys.length / 12));

  const layout = ({ width, height: h }: ChartSize) => {
    const cw = Math.max(2, (width - LEFT) / Math.max(1, colKeys.length));
    const rh = Math.max(2, (h - BOTTOM) / Math.max(1, rowKeys.length));
    const pos = (c: HeatCell) => ({
      x: LEFT + colKeys.indexOf(c.col) * cw,
      y: rowKeys.indexOf(c.row) * rh,
    });
    return { cw, rh, pos };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { cw, pos } = layout(size);
    const c = data[active];
    const p = pos(c);
    return {
      x: p.x + cw / 2,
      y: p.y,
      content: <TooltipBody title={`${c.row} · ${c.col}`} rows={[{ label: "Value", value: formatValue(c.value) }]} />,
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { cw, rh, pos } = layout(size);
        return (
          <>
            {rowKeys.map((r, ri) => (
              <g key={`r-${r}`} aria-hidden="true">
                <text x={LEFT - 6} y={ri * rh + rh / 2} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                  {r}
                </text>
                {colKeys.map((c, ci) => (
                  <rect
                    key={c}
                    x={LEFT + ci * cw + GAP / 2}
                    y={ri * rh + GAP / 2}
                    width={cw - GAP}
                    height={rh - GAP}
                    rx={2}
                    fill={ct.track}
                    fillOpacity={0.5}
                  />
                ))}
              </g>
            ))}
            {colKeys.map((c, ci) =>
              ci % every === 0 ? (
                <text
                  key={`c-${c}`}
                  x={LEFT + ci * cw + cw / 2}
                  y={size.height - 4}
                  textAnchor="middle"
                  fontSize={AXIS_FONT_SIZE}
                  fill={ct.axis}
                  aria-hidden="true"
                >
                  {c}
                </text>
              ) : null,
            )}
            {data.map((cell, i) => {
              const p = pos(cell);
              if (p.x < LEFT || p.y < 0) return null;
              return (
                <rect
                  key={i}
                  {...getMarkProps(i)}
                  x={p.x + GAP / 2}
                  y={p.y + GAP / 2}
                  width={cw - GAP}
                  height={rh - GAP}
                  rx={2}
                  fill={rampColor(ct.sequential, cell.value, max)}
                  stroke={active === i ? ct.axis : "none"}
                />
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
