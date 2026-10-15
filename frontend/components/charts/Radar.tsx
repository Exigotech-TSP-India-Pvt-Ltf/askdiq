"use client";

import { useCallback, useState } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";
import { Legend, num, seriesColor, type SeriesDef } from "./cartesian";

export interface RadarProps<T extends object> {
  /** One row per axis, e.g. `{ dimension: "Groundedness", current: 82, previous: 76 }`. */
  data: readonly T[];
  axisKey: Extract<keyof T, string>;
  series: readonly SeriesDef<Extract<keyof T, string>>[];
  onSelect?: SelectHandler<T>;
  /** Scale end. Default 100. */
  max?: number;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

const RINGS = 4;
const defaultFormat = (v: number) => String(Math.round(v));

/** Multi-dimension comparison (quality dimensions, current vs previous run). */
export function Radar<T extends object>({
  data,
  axisKey,
  series,
  onSelect,
  max = 100,
  label = "Radar chart",
  height,
  formatValue = defaultFormat,
}: RadarProps<T>) {
  const ct = useChartTheme();
  const [hoverSeries, setHoverSeries] = useState<string | null>(null);
  const axisLabel = useCallback((row: T) => String((row as Record<string, unknown>)[axisKey] ?? ""), [axisKey]);
  const describe = useCallback(
    (row: T) => `${axisLabel(row)}: ${series.map((s) => `${s.label} ${formatValue(num(row, s.key))}`).join(", ")}`,
    [axisLabel, formatValue, series],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  if (data.length === 0 || series.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const cx = width / 2;
    const cy = h / 2;
    const r = Math.max(10, Math.min(width, h) / 2 - 28);
    const n = data.length;
    const angle = (i: number) => (n === 1 ? -Math.PI / 2 : -Math.PI / 2 + (2 * Math.PI * i) / n);
    const at = (i: number, v: number): [number, number] => {
      const rr = (Math.max(0, Math.min(max, v)) / (max || 1)) * r;
      return [cx + rr * Math.cos(angle(i)), cy + rr * Math.sin(angle(i))];
    };
    return { cx, cy, r, angle, at };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { at } = layout(size);
    const row = data[active];
    const [x, y] = at(active, max);
    return {
      x,
      y,
      content: (
        <TooltipBody
          title={axisLabel(row)}
          rows={series.map((s, i) => ({ label: s.label, value: formatValue(num(row, s.key)), color: seriesColor(ct, s, i) }))}
        />
      ),
    };
  };

  return (
    <ChartFrame
      label={label}
      height={height}
      tooltip={tooltip}
      footer={<Legend series={series} ct={ct} onHover={setHoverSeries} />}
    >
      {(size) => {
        const { cx, cy, r, angle, at } = layout(size);
        const ring = (f: number) =>
          data.length < 3
            ? null
            : data.map((_, i) => at(i, max * f).join(",")).join(" ");
        return (
          <>
            <g aria-hidden="true">
              {Array.from({ length: RINGS }, (_, k) => (k + 1) / RINGS).map((f) =>
                data.length < 3 ? (
                  <circle key={f} cx={cx} cy={cy} r={r * f} fill="none" stroke={ct.grid} />
                ) : (
                  <polygon key={f} points={ring(f) ?? ""} fill="none" stroke={ct.grid} />
                ),
              )}
              {data.map((row, i) => {
                const [x, y] = at(i, max);
                const cos = Math.cos(angle(i));
                return (
                  <g key={i}>
                    <line x1={cx} y1={cy} x2={x} y2={y} stroke={ct.grid} />
                    <text
                      x={cx + (r + 12) * cos}
                      y={cy + (r + 12) * Math.sin(angle(i))}
                      dy="0.32em"
                      textAnchor={Math.abs(cos) < 0.2 ? "middle" : cos > 0 ? "start" : "end"}
                      fontSize={AXIS_FONT_SIZE}
                      fill={active === i ? ct.tooltip.text : ct.axis}
                    >
                      {axisLabel(row)}
                    </text>
                  </g>
                );
              })}
            </g>
            {series.map((s, si) => {
              const color = seriesColor(ct, s, si);
              const dim = hoverSeries !== null && hoverSeries !== s.key;
              const pts = data.map((row, i) => at(i, num(row, s.key)));
              return (
                <g key={s.key} opacity={dim ? ct.dimOpacity : 1} aria-hidden="true" data-series={s.key}>
                  {pts.length >= 3 && (
                    <polygon points={pts.map((p) => p.join(",")).join(" ")} fill={color} fillOpacity={0.2} stroke={color} strokeWidth={2} />
                  )}
                  {pts.length === 2 && <line x1={pts[0][0]} y1={pts[0][1]} x2={pts[1][0]} y2={pts[1][1]} stroke={color} strokeWidth={2} />}
                  {pts.map(([x, y], i) => (
                    <circle key={i} cx={x} cy={y} r={active === i ? 4 : 2.5} fill={color} />
                  ))}
                </g>
              );
            })}
            {data.map((_, i) => {
              const [x, y] = at(i, max);
              return (
                <g key={i} {...getMarkProps(i)}>
                  <line x1={cx} y1={cy} x2={x} y2={y} stroke="transparent" strokeWidth={18} />
                  <circle cx={x} cy={y} r={10} fill="transparent" />
                </g>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
