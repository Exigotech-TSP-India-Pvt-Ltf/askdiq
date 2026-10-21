"use client";

/**
 * The two User Management charts the kit has no component for:
 * a daily bar and a donut. Both use the kit primitives (`ChartFrame`,
 * `useChartMarks`), so they share the tooltip, roving focus, Enter / click
 * `onSelect` and theme colours of every other chart.
 */
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
} from "@/components/charts/primitives";
import {
  AxisTitles,
  XLabels,
  YGrid,
  axisDescription,
  plotArea,
  type AxisLabelProps,
} from "@/components/charts/cartesian";

export interface DailyBarDatum {
  label: string;
  value: number;
}

export function DailyBars({
  data,
  onSelect,
  label = "Daily counts",
  valueLabel = "Count",
  xAxisLabel,
  yAxisLabel,
}: {
  data: readonly DailyBarDatum[];
  onSelect?: SelectHandler<DailyBarDatum>;
  label?: string;
  valueLabel?: string;
} & AxisLabelProps) {
  const ct = useChartTheme();
  const titles = { xAxisLabel, yAxisLabel };
  const describe = useCallback((d: DailyBarDatum) => `${d.label}: ${d.value}`, []);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const max = useMemo(() => niceMax(Math.max(0, ...data.map((d) => d.value))), [data]);

  if (data.length === 0) return <ChartEmpty />;

  const layout = ({ width, height }: ChartSize) => {
    const area = plotArea(width, height, titles, { labels: data.map((d) => d.label), placement: "band" });
    const bw = (area.x1 - area.x0) / data.length;
    return { area, bw, y: scaleLinear([0, max], [area.y1, area.y0]) };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { area, bw, y } = layout(size);
    const d = data[active];
    return {
      x: area.x0 + bw * active + bw / 2,
      y: y(d.value),
      content: <TooltipBody title={d.label} rows={[{ label: valueLabel, value: String(d.value), color: ct.categorical[0] }]} />,
    };
  };

  return (
    <ChartFrame label={label} tooltip={tooltip} description={axisDescription(titles)}>
      {(size) => {
        const { area, bw, y } = layout(size);
        const barW = Math.max(1, Math.min(28, bw * 0.7));
        return (
          <>
            <YGrid area={area} max={max} ct={ct} integer />
            <XLabels labels={data.map((d) => d.label)} xAt={(i) => area.x0 + bw * i + bw / 2} area={area} ct={ct} />
            <AxisTitles {...titles} area={area} height={size.height} ct={ct} />
            {data.map((d, i) => (
              <g key={d.label} {...getMarkProps(i)} opacity={opacityFor(i)}>
                <rect x={area.x0 + bw * i} y={area.y0} width={bw} height={area.y1 - area.y0} fill="transparent" />
                <rect
                  x={area.x0 + bw * i + (bw - barW) / 2}
                  y={y(d.value)}
                  width={barW}
                  height={Math.max(0, area.y1 - y(d.value))}
                  rx={3}
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

const DONUT_MAX_RADIUS = 72;

export interface DonutDatum {
  id: string;
  label: string;
  value: number;
}

function arcPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const sweep = a1 - a0;
  // A full circle cannot be drawn with one arc; stop just short of it.
  const end = sweep >= Math.PI * 2 ? a0 + Math.PI * 2 - 0.0001 : a1;
  const large = end - a0 > Math.PI ? 1 : 0;
  const p = (r: number, a: number) => `${cx + r * Math.sin(a)},${cy - r * Math.cos(a)}`;
  return [
    `M${p(r1, a0)}`,
    `A${r1},${r1} 0 ${large} 1 ${p(r1, end)}`,
    `L${p(r0, end)}`,
    `A${r0},${r0} 0 ${large} 0 ${p(r0, a0)}`,
    "Z",
  ].join(" ");
}

export function Donut({
  data,
  onSelect,
  label = "Distribution",
  centerLabel = "Total",
}: {
  data: readonly DonutDatum[];
  onSelect?: SelectHandler<DonutDatum>;
  label?: string;
  centerLabel?: string;
}) {
  const ct = useChartTheme();
  const total = useMemo(() => data.reduce((sum, d) => sum + d.value, 0), [data]);
  const pct = useCallback((v: number) => (total > 0 ? `${Math.round((v / total) * 100)}%` : "0%"), [total]);
  const describe = useCallback((d: DonutDatum) => `${d.label}: ${d.value} (${pct(d.value)})`, [pct]);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  if (data.length === 0 || total === 0) return <ChartEmpty />;

  const geometry = ({ width, height }: ChartSize) => {
    // Capped so a single-role donut stays compact beside its legend (VA-46).
    const r1 = Math.max(10, Math.min(DONUT_MAX_RADIUS, Math.min(width * 0.6, height) / 2 - 6));
    return { cx: Math.min(width * 0.32, r1 + 8), cy: height / 2, r1, r0: r1 * 0.62 };
  };

  const angles = data.reduce<{ a0: number; a1: number }[]>((acc, d) => {
    const start = acc.length ? acc[acc.length - 1].a1 : 0;
    acc.push({ a0: start, a1: start + (d.value / total) * Math.PI * 2 });
    return acc;
  }, []);

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { cx, cy, r1 } = geometry(size);
    const d = data[active];
    return {
      x: cx,
      y: cy - r1,
      content: (
        <TooltipBody
          title={d.label}
          rows={[
            { label: "Users", value: String(d.value), color: ct.categorical[active % ct.categorical.length] },
            { label: "Share", value: pct(d.value) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame label={label} tooltip={tooltip}>
      {(size) => {
        const { cx, cy, r0, r1 } = geometry(size);
        const legendX = cx + r1 + 24;
        return (
          <>
            {data.map((d, i) => {
              const color = ct.categorical[i % ct.categorical.length];
              const { a0, a1 } = angles[i];
              return (
                <g key={d.id} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  {d.value > 0 && <path d={arcPath(cx, cy, r0, r1, a0, a1)} fill={color} />}
                  <g transform={`translate(${legendX}, ${cy - (data.length * 22) / 2 + i * 22 + 11})`}>
                    <rect x={0} y={-6} width={10} height={10} rx={2} fill={color} />
                    <text x={16} y={0} dy="0.32em" fontSize={12} fill={ct.axis}>
                      {d.label}
                    </text>
                    <text x={120} y={0} dy="0.32em" fontSize={12} fill={ct.tooltip.text} className="tabular-nums">
                      {formatCompact(d.value)}
                    </text>
                  </g>
                </g>
              );
            })}
            <text x={cx} y={cy - 6} textAnchor="middle" fontSize={18} fontWeight={600} fill={ct.tooltip.text} className="tabular-nums">
              {formatCompact(total)}
            </text>
            <text x={cx} y={cy + 12} textAnchor="middle" fontSize={11} fill={ct.axis}>
              {centerLabel}
            </text>
          </>
        );
      }}
    </ChartFrame>
  );
}
