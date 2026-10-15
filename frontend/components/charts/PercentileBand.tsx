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
  type TooltipRow,
} from "./primitives";
import { Legend, XLabels, YGrid, bandPath, linePath, plotArea, pointX } from "./cartesian";

export interface PercentilePoint {
  label: string;
  p50: number;
  p95: number;
  p99?: number;
}

export interface PercentileBandProps {
  data: readonly PercentilePoint[];
  onSelect?: SelectHandler<PercentilePoint>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

/** p50 line inside a p50 to p95 band, with an optional dashed p99 (latency). */
export function PercentileBand({
  data,
  onSelect,
  label = "Latency percentiles",
  height,
  formatValue = formatCompact,
}: PercentileBandProps) {
  const ct = useChartTheme();
  const describe = useCallback(
    (d: PercentilePoint) =>
      `${d.label}: p50 ${formatValue(d.p50)}, p95 ${formatValue(d.p95)}${
        d.p99 === undefined ? "" : `, p99 ${formatValue(d.p99)}`
      }`,
    [formatValue],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const hasP99 = data.some((d) => d.p99 !== undefined);
  const max = useMemo(
    () => niceMax(Math.max(0, ...data.map((d) => Math.max(d.p50, d.p95, d.p99 ?? 0)))),
    [data],
  );

  if (data.length === 0) return <ChartEmpty height={height} />;

  const color = ct.categorical[0];
  const p99Color = ct.status.warn;
  const legend = [
    { key: "p50", label: "p50", color },
    { key: "p95", label: "p50 to p95", color },
    ...(hasP99 ? [{ key: "p99", label: "p99", color: p99Color }] : []),
  ];

  const layout = ({ width, height: h }: ChartSize) => {
    const area = plotArea(width, h);
    return { area, x: pointX(data.length, area), y: scaleLinear([0, max], [area.y1, area.y0]) };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, y } = layout(size);
    const d = data[active];
    const rows: TooltipRow[] = [
      { label: "p50", value: formatValue(d.p50), color },
      { label: "p95", value: formatValue(d.p95), color },
    ];
    if (d.p99 !== undefined) rows.push({ label: "p99", value: formatValue(d.p99), color: p99Color });
    return { x: x(active), y: y(Math.max(d.p95, d.p99 ?? 0)), content: <TooltipBody title={d.label} rows={rows} /> };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip} footer={<Legend series={legend} ct={ct} />}>
      {(size) => {
        const { area, x, y } = layout(size);
        const p50 = data.map((d, i) => [x(i), y(d.p50)] as const);
        const p95 = data.map((d, i) => [x(i), y(d.p95)] as const);
        const p99 = data.flatMap((d, i) => (d.p99 === undefined ? [] : [[x(i), y(d.p99)] as const]));
        const band = data.length > 1 ? (area.x1 - area.x0) / (data.length - 1) : area.x1 - area.x0;
        return (
          <>
            <YGrid area={area} max={max} ct={ct} format={formatValue} />
            <XLabels labels={data.map((d) => d.label)} xAt={x} area={area} ct={ct} />
            {data.length > 1 && (
              <path d={bandPath(p95, p50)} fill={color} fillOpacity={0.18} aria-hidden="true" />
            )}
            {data.length > 1 && <path d={linePath(p50)} fill="none" stroke={color} strokeWidth={2} aria-hidden="true" />}
            {p99.length > 1 && (
              <path
                d={linePath(p99)}
                fill="none"
                stroke={p99Color}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                aria-hidden="true"
              />
            )}
            {data.map((d, i) => (
              <g key={i} {...getMarkProps(i)}>
                <rect x={x(i) - band / 2} y={area.y0} width={band} height={area.y1 - area.y0} fill="transparent" />
                {(data.length === 1 || active === i) && (
                  <>
                    <line x1={x(i)} x2={x(i)} y1={y(d.p95)} y2={y(d.p50)} stroke={color} strokeWidth={6} strokeOpacity={0.3} />
                    <circle cx={x(i)} cy={y(d.p50)} r={3.5} fill={color} />
                  </>
                )}
              </g>
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}
