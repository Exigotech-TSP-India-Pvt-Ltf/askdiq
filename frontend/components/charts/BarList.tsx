"use client";

import { useCallback, useMemo } from "react";
import type { ChartTheme } from "@/lib/theme-tokens";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";

export interface BarDatum {
  key: string;
  label: string;
  value: number;
}

export interface BarListProps {
  data: readonly BarDatum[];
  onSelect?: SelectHandler<BarDatum>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
  /** Bar colour from the chart theme (default: first categorical colour). */
  colorFor?: (datum: BarDatum, index: number, ct: ChartTheme) => string;
}

const LABEL_WIDTH = 104;
const VALUE_WIDTH = 44;
const MAX_BAR = 26;

/** Ranked horizontal bars (rating distribution, reasons, outcomes). */
export function BarList({
  data,
  onSelect,
  label = "Bar chart",
  height,
  formatValue = formatCompact,
  colorFor,
}: BarListProps) {
  const ct = useChartTheme();
  const describe = useCallback((d: BarDatum) => `${d.label}: ${formatValue(d.value)}`, [formatValue]);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const max = useMemo(() => Math.max(1, ...data.map((d) => d.value)), [data]);
  const color = (d: BarDatum, i: number) => colorFor?.(d, i, ct) ?? ct.categorical[0];

  if (data.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const labelW = Math.min(LABEL_WIDTH, width * 0.35);
    const plotW = Math.max(1, width - labelW - VALUE_WIDTH - 12);
    const rowH = h / data.length;
    const barH = Math.min(MAX_BAR, rowH * 0.62);
    return { labelW, plotW, rowH, barH };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { labelW, plotW, rowH } = layout(size);
    const d = data[active];
    return {
      x: labelW + 8 + ((d.value / max) * plotW) / 2,
      y: rowH * active + rowH * 0.2,
      content: <TooltipBody title={d.label} rows={[{ label: "Count", value: formatValue(d.value), color: color(d, active) }]} />,
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { labelW, plotW, rowH, barH } = layout(size);
        return (
          <>
            {data.map((d, i) => {
              const y = rowH * i + (rowH - barH) / 2;
              const w = Math.max(d.value > 0 ? 2 : 0, (d.value / max) * plotW);
              return (
                <g key={d.key} {...getMarkProps(i)} opacity={opacityFor(i)} data-key={d.key}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text x={labelW} y={rowH * i + rowH / 2} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                    {d.label}
                  </text>
                  <rect x={labelW + 8} y={y} width={plotW} height={barH} rx={4} fill={ct.track} />
                  <rect x={labelW + 8} y={y} width={w} height={barH} rx={4} fill={color(d, i)} />
                  <text
                    x={labelW + 8 + plotW + 6}
                    y={rowH * i + rowH / 2}
                    dy="0.32em"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                    className="tabular-nums"
                  >
                    {formatValue(d.value)}
                  </text>
                </g>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
