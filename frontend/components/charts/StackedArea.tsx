"use client";

import { useCallback, useMemo, useState } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  niceMax,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";
import {
  Legend,
  XLabels,
  YGrid,
  bandPath,
  num,
  plotArea,
  pointX,
  seriesColor,
  type SeriesDef,
} from "./cartesian";

export interface StackedAreaProps<T extends object> {
  /** One row per x value, e.g. `{ day: "Mon", answered: 40, fallback: 3 }`. */
  data: readonly T[];
  xKey: Extract<keyof T, string>;
  series: readonly SeriesDef<Extract<keyof T, string>>[];
  onSelect?: SelectHandler<T>;
  /** 100 % stacked (share of total per x). */
  percent?: boolean;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

const pct = (v: number) => `${Math.round(v)}%`;
/** Half-width of the band drawn for a single x value. */
const SINGLE_HALF_WIDTH = 12;

/** Stacked area of a total split by category (e.g. queries by outcome). */
export function StackedArea<T extends object>({
  data,
  xKey,
  series,
  onSelect,
  percent = false,
  label = "Stacked area chart",
  height,
  formatValue = formatCompact,
}: StackedAreaProps<T>) {
  const ct = useChartTheme();
  const [hoverSeries, setHoverSeries] = useState<string | null>(null);
  const xLabel = useCallback((row: T) => String((row as Record<string, unknown>)[xKey] ?? ""), [xKey]);
  const describe = useCallback(
    (row: T) => `${xLabel(row)}: ${series.map((s) => `${s.label} ${formatValue(num(row, s.key))}`).join(", ")}`,
    [formatValue, series, xLabel],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  // stacks[s][i] = [lower, upper]
  const { stacks, max } = useMemo(() => {
    const totals = data.map((row) => series.reduce((sum, s) => sum + num(row, s.key), 0));
    const running = data.map(() => 0);
    const out = series.map((s) =>
      data.map((row, i) => {
        const raw = num(row, s.key);
        const v = percent ? (totals[i] > 0 ? (raw / totals[i]) * 100 : 0) : raw;
        const lower = running[i];
        running[i] += v;
        return [lower, running[i]] as const;
      }),
    );
    return { stacks: out, max: percent ? 100 : niceMax(Math.max(0, ...totals)) };
  }, [data, percent, series]);

  if (data.length === 0 || series.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const area = plotArea(width, h);
    return { area, x: pointX(data.length, area), y: scaleLinear([0, max], [area.y1, area.y0]) };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, y } = layout(size);
    const row = data[active];
    const top = stacks[stacks.length - 1][active][1];
    return {
      x: x(active),
      y: y(top),
      content: (
        <TooltipBody
          title={xLabel(row)}
          rows={series.map((s, i) => ({
            label: s.label,
            value: formatValue(num(row, s.key)),
            color: seriesColor(ct, s, i),
          }))}
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
        const { area, x, y } = layout(size);
        const band = data.length > 1 ? (area.x1 - area.x0) / (data.length - 1) : area.x1 - area.x0;
        const widen = (pts: (readonly [number, number])[]) =>
          data.length === 1
            ? [
                [pts[0][0] - SINGLE_HALF_WIDTH, pts[0][1]] as const,
                [pts[0][0] + SINGLE_HALF_WIDTH, pts[0][1]] as const,
              ]
            : pts;
        return (
          <>
            <YGrid area={area} max={max} ct={ct} format={percent ? pct : formatValue} />
            <XLabels labels={data.map(xLabel)} xAt={x} area={area} ct={ct} />
            {series.map((s, si) => {
              const color = seriesColor(ct, s, si);
              const upper = widen(stacks[si].map(([, u], i) => [x(i), y(u)] as const));
              const lower = widen(stacks[si].map(([l], i) => [x(i), y(l)] as const));
              const dim = hoverSeries !== null && hoverSeries !== s.key;
              return (
                <path
                  key={s.key}
                  d={bandPath(upper, lower)}
                  fill={color}
                  fillOpacity={dim ? 0.1 : 0.35}
                  stroke={color}
                  strokeOpacity={dim ? ct.dimOpacity : 1}
                  strokeWidth={1.5}
                  aria-hidden="true"
                  data-series={s.key}
                />
              );
            })}
            {active !== null && (
              <line
                x1={x(active)}
                x2={x(active)}
                y1={area.y0}
                y2={area.y1}
                stroke={ct.axis}
                strokeDasharray="3 3"
                aria-hidden="true"
              />
            )}
            {data.map((_, i) => (
              <rect
                key={i}
                {...getMarkProps(i)}
                x={x(i) - band / 2}
                y={area.y0}
                width={band}
                height={area.y1 - area.y0}
                fill="transparent"
              />
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}
