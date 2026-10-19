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
  ticks,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";
import { AxisTitles, YGrid, axisDescription, plotArea, type AxisLabelProps } from "./cartesian";

export interface ScatterPoint {
  id: string;
  label: string;
  x: number;
  y: number;
  /** Optional weight for the dot radius. */
  size?: number;
}

export interface QuadrantLabels {
  topLeft?: string;
  topRight?: string;
  bottomLeft?: string;
  bottomRight?: string;
}

/**
 * `xAxisLabel` / `yAxisLabel` (with units) title the axes; the older
 * `xLabel` / `yLabel` still work and are used when the new props are unset.
 */
export interface QuadrantScatterProps extends AxisLabelProps {
  data: readonly ScatterPoint[];
  onSelect?: SelectHandler<ScatterPoint>;
  /** Vertical divider (x value). Defaults to the x range middle. */
  xThreshold?: number;
  /** Horizontal divider (y value). Defaults to the y range middle. */
  yThreshold?: number;
  quadrants?: QuadrantLabels;
  xLabel?: string;
  yLabel?: string;
  xMax?: number;
  yMax?: number;
  label?: string;
  height?: number;
  formatX?: (value: number) => string;
  formatY?: (value: number) => string;
  /** Numeric tick labels under the x axis (default true); turn off for categorical `formatX`. */
  showXTicks?: boolean;
}

const MIN_R = 4;
const MAX_R = 12;

/** Two-metric scatter split into named quadrants (e.g. human rating vs groundedness). */
export function QuadrantScatter({
  data,
  onSelect,
  xThreshold,
  yThreshold,
  quadrants = {},
  xLabel = "x",
  yLabel = "y",
  xMax,
  yMax,
  label = "Quadrant scatter",
  height,
  formatX = formatCompact,
  formatY = formatCompact,
  xAxisLabel,
  yAxisLabel,
  showXTicks = true,
}: QuadrantScatterProps) {
  const ct = useChartTheme();
  const titles = { xAxisLabel: xAxisLabel ?? xLabel, yAxisLabel: yAxisLabel ?? yLabel };
  const describe = useCallback(
    (p: ScatterPoint) =>
      `${p.label}: ${xAxisLabel ?? xLabel} ${formatX(p.x)}, ${yAxisLabel ?? yLabel} ${formatY(p.y)}`,
    [formatX, formatY, xAxisLabel, xLabel, yAxisLabel, yLabel],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const { mx, my, maxSize } = useMemo(
    () => ({
      mx: xMax ?? niceMax(Math.max(0, ...data.map((p) => p.x))),
      my: yMax ?? niceMax(Math.max(0, ...data.map((p) => p.y))),
      maxSize: Math.max(1, ...data.map((p) => p.size ?? 1)),
    }),
    [data, xMax, yMax],
  );

  if (data.length === 0) return <ChartEmpty height={height} />;

  const tx = xThreshold ?? mx / 2;
  const ty = yThreshold ?? my / 2;
  const radius = (p: ScatterPoint) => (p.size === undefined ? 5 : MIN_R + (p.size / maxSize) * (MAX_R - MIN_R));
  const quadrantColor = (p: ScatterPoint) => {
    if (p.x >= tx && p.y >= ty) return ct.status.ok;
    if (p.x < tx && p.y < ty) return ct.status.danger;
    return ct.status.warn;
  };

  const layout = ({ width, height: h }: ChartSize) => {
    const area = plotArea(width, h, titles);
    return {
      area,
      x: scaleLinear([0, mx], [area.x0, area.x1]),
      y: scaleLinear([0, my], [area.y1, area.y0]),
    };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, y } = layout(size);
    const p = data[active];
    return {
      x: x(p.x),
      y: y(p.y) - radius(p),
      content: (
        <TooltipBody
          title={p.label}
          rows={[
            { label: titles.xAxisLabel, value: formatX(p.x) },
            { label: titles.yAxisLabel, value: formatY(p.y) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip} description={axisDescription(titles)}>
      {(size) => {
        const { area, x, y } = layout(size);
        const corner = (text: string | undefined, cx: number, cy: number, anchor: "start" | "end") =>
          text ? (
            <text x={cx} y={cy} textAnchor={anchor} fontSize={AXIS_FONT_SIZE} fill={ct.axis} fontWeight={600} aria-hidden="true">
              {text}
            </text>
          ) : null;
        return (
          <>
            <YGrid area={area} max={my} ct={ct} format={formatY} />
            <line x1={x(tx)} x2={x(tx)} y1={area.y0} y2={area.y1} stroke={ct.axis} strokeDasharray="4 3" aria-hidden="true" />
            <line x1={area.x0} x2={area.x1} y1={y(ty)} y2={y(ty)} stroke={ct.axis} strokeDasharray="4 3" aria-hidden="true" />
            {corner(quadrants.topLeft, area.x0 + 4, area.y0 + 12, "start")}
            {corner(quadrants.topRight, area.x1 - 4, area.y0 + 12, "end")}
            {corner(quadrants.bottomLeft, area.x0 + 4, area.y1 - 6, "start")}
            {corner(quadrants.bottomRight, area.x1 - 4, area.y1 - 6, "end")}
            {showXTicks && (
              <g aria-hidden="true">
                {ticks(mx).map((t) => (
                  <text key={t} x={x(t)} y={area.y1 + 15} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                    {formatX(t)}
                  </text>
                ))}
              </g>
            )}
            <AxisTitles {...titles} area={area} height={size.height} ct={ct} />
            {data.map((p, i) => (
              <circle
                key={p.id}
                {...getMarkProps(i)}
                cx={x(p.x)}
                cy={y(p.y)}
                r={radius(p) + (active === i ? 2 : 0)}
                fill={quadrantColor(p)}
                fillOpacity={0.75 * opacityFor(i)}
                stroke={quadrantColor(p)}
              />
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}

