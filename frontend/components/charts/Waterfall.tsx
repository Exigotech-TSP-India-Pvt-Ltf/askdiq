"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { ChartTheme } from "@/lib/theme-tokens";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  fitText,
  formatCompact,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";
import { AxisTitles, axisDescription, titleInsets, type AxisLabelProps } from "./cartesian";

export interface WaterfallStep {
  label: string;
  /** Duration or delta. Negative values draw leftwards in the danger colour. */
  value: number;
  /** Explicit offset (trace spans). Omitted = starts where the previous step ended. */
  start?: number;
  tone?: keyof ChartTheme["status"];
}

/** `xAxisLabel` titles the value (time / amount) axis, `yAxisLabel` the stage column. */
export interface WaterfallProps extends AxisLabelProps {
  data: readonly WaterfallStep[];
  onSelect?: SelectHandler<WaterfallStep>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
  /** Width of the stage label column in px (default 110); capped to 35 % of the chart on narrow screens. */
  labelWidth?: number;
  /** Tooltip name of the bar value (default "Value"), e.g. "Duration". */
  valueLabel?: string;
}

const LABEL_WIDTH = 110;
const ROW_GAP = 0.3;

/** Per-stage waterfall (request stages, trace spans, cost deltas). */
export function Waterfall({
  data,
  onSelect,
  label = "Waterfall",
  height,
  formatValue = formatCompact,
  valueLabel = "Value",
  labelWidth = LABEL_WIDTH,
  xAxisLabel,
  yAxisLabel,
}: WaterfallProps) {
  const titles = { xAxisLabel, yAxisLabel };
  const ct = useChartTheme();
  const describe = useCallback(
    (d: WaterfallStep) => `${d.label}: ${formatValue(d.value)}`,
    [formatValue],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  const spans = useMemo(() => {
    let cursor = 0;
    return data.map((d) => {
      const from = d.start ?? cursor;
      const to = from + d.value;
      cursor = to;
      return [Math.min(from, to), Math.max(from, to)] as const;
    });
  }, [data]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const lo = Math.min(0, ...spans.map(([a]) => a));
  const hi = Math.max(...spans.map(([, b]) => b), lo + 1);
  const colorFor = (d: WaterfallStep) =>
    d.tone ? ct.status[d.tone] : d.value < 0 ? ct.status.danger : ct.categorical[0];

  const layout = ({ width, height: h }: ChartSize) => {
    const inset = titleInsets(titles);
    const labelW = inset.left + Math.min(labelWidth, (width - inset.left) * 0.35);
    const x = scaleLinear([lo, hi], [labelW + 8, Math.max(labelW + 9, width - 48)]);
    const rowsH = Math.max(1, h - inset.bottom);
    const rowH = rowsH / data.length;
    const titleArea = { x0: labelW + 8, x1: Math.max(labelW + 9, width - 48), y0: 0, y1: rowsH };
    return { labelW, x, rowH, rowsH, titleArea, labelInset: inset.left };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, rowH } = layout(size);
    const [a, b] = spans[active];
    const d = data[active];
    return {
      x: (x(a) + x(b)) / 2,
      y: rowH * active + rowH * (ROW_GAP / 2),
      content: (
        <TooltipBody
          title={d.label}
          caption={xAxisLabel}
          rows={[
            { label: valueLabel, value: formatValue(d.value), color: colorFor(d) },
            { label: "Starts at", value: formatValue(d.value < 0 ? b : a) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip} description={axisDescription(titles)}>
      {(size) => {
        const { labelW, x, rowH, rowsH, titleArea, labelInset: inset } = layout(size);
        const barH = Math.max(2, rowH * (1 - ROW_GAP));
        return (
          <>
            <AxisTitles {...titles} area={titleArea} height={size.height} ct={ct} />
            <line x1={x(lo)} x2={x(lo)} y1={0} y2={rowsH} stroke={ct.grid} aria-hidden="true" />
            {data.map((d, i) => {
              const [a, b] = spans[i];
              const cy = rowH * i + rowH / 2;
              return (
                <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text
                    x={labelW}
                    y={cy}
                    dy="0.32em"
                    textAnchor="end"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                  >
                    <title>{d.label}</title>
                    {fitText(d.label, labelW - inset - 4)}
                  </text>
                  <rect
                    x={x(a)}
                    y={cy - barH / 2}
                    width={Math.max(2, x(b) - x(a))}
                    height={barH}
                    rx={3}
                    fill={colorFor(d)}
                  />
                  <text
                    x={x(b) + 4}
                    y={cy}
                    dy="0.32em"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                    aria-hidden="true"
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
