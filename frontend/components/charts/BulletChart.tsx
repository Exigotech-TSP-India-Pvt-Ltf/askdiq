"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  niceMax,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
  type TooltipRow,
} from "./primitives";

export interface BulletDatum {
  label: string;
  /** Actual (e.g. cost month-to-date). */
  value: number;
  /** Target or budget marker. */
  target?: number;
  /** Secondary marker, e.g. the month-end forecast. */
  forecast?: number;
  /** Qualitative band edges, low to high (e.g. [50, 80, 100] % of budget). */
  ranges?: readonly number[];
  /** Scale end for this row. Defaults to the largest value across rows. */
  max?: number;
}

export interface BulletChartProps {
  data: readonly BulletDatum[];
  onSelect?: SelectHandler<BulletDatum>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

const LABEL_WIDTH = 96;
const RANGE_OPACITY = [0.9, 0.6, 0.35];

/** Actual vs target (and forecast) in one compact bar (budget, SLO). */
export function BulletChart({
  data,
  onSelect,
  label = "Bullet chart",
  height,
  formatValue = formatCompact,
}: BulletChartProps) {
  const ct = useChartTheme();
  const describe = useCallback(
    (d: BulletDatum) =>
      `${d.label}: ${formatValue(d.value)}${d.target === undefined ? "" : ` of ${formatValue(d.target)} target`}${
        d.forecast === undefined ? "" : `, forecast ${formatValue(d.forecast)}`
      }`,
    [formatValue],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const sharedMax = useMemo(
    () =>
      niceMax(
        Math.max(0, ...data.flatMap((d) => [d.value, d.target ?? 0, d.forecast ?? 0, ...(d.ranges ?? [])])),
      ),
    [data],
  );

  if (data.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const labelW = Math.min(LABEL_WIDTH, width * 0.35);
    const rowH = h / data.length;
    const xFor = (d: BulletDatum) => scaleLinear([0, d.max ?? sharedMax], [labelW + 8, Math.max(labelW + 9, width - 8)]);
    return { labelW, rowH, xFor };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { rowH, xFor } = layout(size);
    const d = data[active];
    const rows: TooltipRow[] = [{ label: "Actual", value: formatValue(d.value), color: ct.categorical[0] }];
    if (d.target !== undefined) rows.push({ label: "Target", value: formatValue(d.target) });
    if (d.forecast !== undefined) rows.push({ label: "Forecast", value: formatValue(d.forecast), color: ct.status.warn });
    return { x: xFor(d)(d.value), y: rowH * active + rowH * 0.2, content: <TooltipBody title={d.label} rows={rows} /> };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { labelW, rowH, xFor } = layout(size);
        const bandH = Math.max(6, rowH * 0.6);
        return (
          <>
            {data.map((d, i) => {
              const x = xFor(d);
              const top = rowH * i + (rowH - bandH) / 2;
              const cy = rowH * i + rowH / 2;
              const ranges = d.ranges ?? [d.max ?? sharedMax];
              const over = d.target !== undefined && d.value > d.target;
              return (
                <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text x={labelW} y={cy} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                    {d.label}
                  </text>
                  {[...ranges]
                    .sort((a, b) => b - a)
                    .map((edge, ri) => (
                      <rect
                        key={ri}
                        x={x(0)}
                        y={top}
                        width={Math.max(0, x(edge) - x(0))}
                        height={bandH}
                        fill={ct.track}
                        fillOpacity={RANGE_OPACITY[ri % RANGE_OPACITY.length]}
                      />
                    ))}
                  <rect
                    x={x(0)}
                    y={cy - bandH / 6}
                    width={Math.max(1, x(d.value) - x(0))}
                    height={bandH / 3}
                    fill={over ? ct.status.danger : ct.categorical[0]}
                  />
                  {d.forecast !== undefined && (
                    <circle cx={x(d.forecast)} cy={cy} r={Math.max(2.5, bandH / 6)} fill={ct.status.warn} />
                  )}
                  {d.target !== undefined && (
                    <line x1={x(d.target)} x2={x(d.target)} y1={top - 2} y2={top + bandH + 2} stroke={ct.tooltip.text} strokeWidth={2} />
                  )}
                </g>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
