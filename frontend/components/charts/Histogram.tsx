"use client";

import { useCallback, useMemo } from "react";
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
import { XLabels, YGrid, plotArea } from "./cartesian";

export interface HistogramBin {
  /** Inclusive lower edge. */
  x0: number;
  /** Exclusive upper edge (inclusive for the last bin). */
  x1: number;
  count: number;
}

export interface HistogramProps {
  /** Pre-binned data (server aggregates); use `binValues` for raw values. */
  data: readonly HistogramBin[];
  onSelect?: SelectHandler<HistogramBin>;
  label?: string;
  height?: number;
  formatEdge?: (value: number) => string;
}

/** Splits raw values into `binCount` equal-width bins. */
export function binValues(values: readonly number[], binCount = 10): HistogramBin[] {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length === 0 || binCount < 1) return [];
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  if (lo === hi) return [{ x0: lo, x1: hi, count: finite.length }];
  const width = (hi - lo) / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => ({
    x0: lo + i * width,
    x1: i === binCount - 1 ? hi : lo + (i + 1) * width,
    count: 0,
  }));
  for (const v of finite) {
    const idx = Math.min(binCount - 1, Math.floor((v - lo) / width));
    bins[idx].count += 1;
  }
  return bins;
}

/** Distribution of a measure (turns per conversation, time to approve). */
export function Histogram({
  data,
  onSelect,
  label = "Histogram",
  height,
  formatEdge = formatCompact,
}: HistogramProps) {
  const ct = useChartTheme();
  const binLabel = useCallback(
    (b: HistogramBin) => (b.x0 === b.x1 ? formatEdge(b.x0) : `${formatEdge(b.x0)}–${formatEdge(b.x1)}`),
    [formatEdge],
  );
  const describe = useCallback((b: HistogramBin) => `${binLabel(b)}: ${b.count}`, [binLabel]);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const max = useMemo(() => niceMax(Math.max(0, ...data.map((b) => b.count))), [data]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const area = plotArea(width, h);
    const bw = (area.x1 - area.x0) / data.length;
    return { area, bw, y: scaleLinear([0, max], [area.y1, area.y0]) };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { area, bw, y } = layout(size);
    const b = data[active];
    return {
      x: area.x0 + bw * active + bw / 2,
      y: y(b.count),
      content: <TooltipBody title={binLabel(b)} rows={[{ label: "Count", value: String(b.count), color: ct.categorical[0] }]} />,
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { area, bw, y } = layout(size);
        return (
          <>
            <YGrid area={area} max={max} ct={ct} />
            <XLabels labels={data.map(binLabel)} xAt={(i) => area.x0 + bw * i + bw / 2} area={area} ct={ct} maxLabels={6} />
            {data.map((b, i) => (
              <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                <rect x={area.x0 + bw * i} y={area.y0} width={bw} height={area.y1 - area.y0} fill="transparent" />
                <rect
                  x={area.x0 + bw * i + 1}
                  y={y(b.count)}
                  width={Math.max(1, bw - 2)}
                  height={Math.max(0, area.y1 - y(b.count))}
                  rx={2}
                  fill={ct.categorical[0]}
                />
              </g>
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}
