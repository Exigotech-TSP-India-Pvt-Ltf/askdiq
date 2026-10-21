"use client";

import { useCallback, useMemo, useState } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  isIntegerSeries,
  niceMax,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
  type TooltipRow,
} from "./primitives";
import {
  AxisTitles,
  Legend,
  XLabels,
  YGrid,
  axisDescription,
  linePath,
  plotArea,
  pointX,
  seriesColor,
  type AxisLabelProps,
  type SeriesDef,
} from "./cartesian";

export interface LineSeriesDef<K extends string = string> extends SeriesDef<K> {
  /** Dashed stroke (forecasts, projections). */
  dashed?: boolean;
}

export interface LineTrendProps<T extends object> extends AxisLabelProps {
  /** One row per x value; a null / missing value breaks the line. */
  data: readonly T[];
  xKey: Extract<keyof T, string>;
  series: readonly LineSeriesDef<Extract<keyof T, string>>[];
  /** Draws a marker on every point (e.g. evaluation runs). */
  markers?: boolean;
  onSelect?: SelectHandler<T>;
  /** Fixed y maximum (e.g. 100 for percentages). */
  max?: number;
  /** Horizontal reference line (budget, target). */
  reference?: { value: number; label: string };
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

function valueOf(row: object, key: string): number | null {
  const v = (row as Record<string, unknown>)[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Splits a series into contiguous runs of non-null points. */
function segments(points: readonly ([number, number] | null)[]): [number, number][][] {
  const out: [number, number][][] = [];
  let current: [number, number][] = [];
  for (const p of points) {
    if (p) current.push(p);
    else if (current.length) {
      out.push(current);
      current = [];
    }
  }
  if (current.length) out.push(current);
  return out;
}

/** Lines over a category axis with optional clickable markers and a reference line. */
export function LineTrend<T extends object>({
  data,
  xKey,
  series,
  markers = false,
  onSelect,
  max: fixedMax,
  reference,
  label = "Trend chart",
  height,
  formatValue = formatCompact,
  xAxisLabel,
  yAxisLabel,
}: LineTrendProps<T>) {
  const titles = { xAxisLabel, yAxisLabel };
  const ct = useChartTheme();
  const [hoverSeries, setHoverSeries] = useState<string | null>(null);
  const xLabel = useCallback((row: T) => String((row as Record<string, unknown>)[xKey] ?? ""), [xKey]);
  const describe = useCallback(
    (row: T) =>
      `${xLabel(row)}: ${series
        .flatMap((s) => {
          const v = valueOf(row, s.key);
          return v === null ? [] : [`${s.label} ${formatValue(v)}`];
        })
        .join(", ")}`,
    [formatValue, series, xLabel],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  const { max, integer } = useMemo(() => {
    const values = data.flatMap((row) => series.map((s) => valueOf(row, s.key) ?? 0));
    const whole = isIntegerSeries(values);
    if (fixedMax !== undefined) return { max: fixedMax, integer: whole && Number.isInteger(fixedMax) };
    return { max: niceMax(Math.max(0, reference?.value ?? 0, ...values)), integer: whole };
  }, [data, fixedMax, reference, series]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const area = plotArea(width, h, titles);
    return { area, x: pointX(data.length, area), y: scaleLinear([0, max], [area.y1, area.y0]) };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, y } = layout(size);
    const row = data[active];
    const rows: TooltipRow[] = series.flatMap((s, i) => {
      const v = valueOf(row, s.key);
      return v === null ? [] : [{ label: s.label, value: formatValue(v), color: seriesColor(ct, s, i) }];
    });
    const top = Math.max(0, ...series.map((s) => valueOf(row, s.key) ?? 0));
    return { x: x(active), y: y(top), content: <TooltipBody title={xLabel(row)} caption={yAxisLabel} rows={rows} /> };
  };

  return (
    <ChartFrame
      label={label}
      height={height}
      tooltip={tooltip}
      description={axisDescription(titles)}
      footer={<Legend series={series} ct={ct} onHover={setHoverSeries} />}
    >
      {(size) => {
        const { area, x, y } = layout(size);
        const band = data.length > 1 ? (area.x1 - area.x0) / (data.length - 1) : area.x1 - area.x0;
        return (
          <>
            <YGrid area={area} max={max} ct={ct} format={formatValue} integer={integer} />
            <XLabels labels={data.map(xLabel)} xAt={x} area={area} ct={ct} />
            <AxisTitles {...titles} area={area} height={size.height} ct={ct} />
            {reference && (
              <g aria-hidden="true">
                <line
                  x1={area.x0}
                  x2={area.x1}
                  y1={y(reference.value)}
                  y2={y(reference.value)}
                  stroke={ct.status.danger}
                  strokeDasharray="2 3"
                />
                <text x={area.x1} y={y(reference.value) - 4} textAnchor="end" fontSize={10} fill={ct.status.danger}>
                  {reference.label}
                </text>
              </g>
            )}
            {series.map((s, si) => {
              const color = seriesColor(ct, s, si);
              const pts = data.map((row, i) => {
                const v = valueOf(row, s.key);
                return v === null ? null : ([x(i), y(v)] as [number, number]);
              });
              const dim = hoverSeries !== null && hoverSeries !== s.key;
              return (
                <g key={s.key} aria-hidden="true" opacity={dim ? ct.dimOpacity : 1} data-series={s.key}>
                  {segments(pts).map((seg, k) =>
                    seg.length > 1 ? (
                      <path
                        key={k}
                        d={linePath(seg)}
                        fill="none"
                        stroke={color}
                        strokeWidth={2}
                        strokeDasharray={s.dashed ? "5 4" : undefined}
                      />
                    ) : null,
                  )}
                  {pts.map((p, i) =>
                    p && (markers || active === i || data.length === 1) ? (
                      <circle key={i} cx={p[0]} cy={p[1]} r={active === i ? 4.5 : 3.5} fill={color} />
                    ) : null,
                  )}
                </g>
              );
            })}
            {data.map((_, i) => (
              <g key={i} {...getMarkProps(i)}>
                <rect x={x(i) - band / 2} y={area.y0} width={band} height={area.y1 - area.y0} fill="transparent" />
              </g>
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}
