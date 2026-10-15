"use client";

import type React from "react";
import { ResponsiveContainer, Treemap, type TreemapNode } from "recharts";
import { useChartTheme } from "@/lib/use-chart-theme";
import { ChartEmpty } from "../primitives";
import type { TreemapChartProps, TreemapDatum } from "../types";

const MIN_LABEL_WIDTH = 48;
const MIN_LABEL_HEIGHT = 22;
const MIN_DETAIL_HEIGHT = 40;

/** Loaded through `next/dynamic` (see `TreemapChart`); do not import directly. */
export default function TreemapImpl({ data, onSelect, label = "Treemap", height }: TreemapChartProps) {
  const ct = useChartTheme();
  if (data.length === 0) return <ChartEmpty height={height} />;

  const byName = new Map<string, TreemapDatum>(data.map((d) => [d.name, d]));
  const Tile = (node: TreemapNode): React.ReactElement => {
    const datum = byName.get(String(node.name));
    if (node.depth !== 1 || !datum) return <g />;
    const color = datum.color ?? ct.categorical[node.index % ct.categorical.length];
    const labelColor = datum.labelColor ?? ct.tooltip.background;
    const select = onSelect ? () => onSelect(datum) : undefined;
    return (
      <g
        role={select ? "button" : "img"}
        tabIndex={select ? 0 : undefined}
        aria-label={`${datum.name}: ${datum.value}${datum.detail ? `, ${datum.detail}` : ""}`}
        aria-pressed={select && datum.selected !== undefined ? datum.selected : undefined}
        className="chart-mark"
        style={select ? { cursor: "pointer" } : undefined}
        onClick={select}
        onKeyDown={(e) => {
          if (select && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            select();
          }
        }}
      >
        <rect x={node.x} y={node.y} width={node.width} height={node.height} rx={4} fill={color} stroke={ct.tooltip.background} strokeWidth={datum.selected ? 3 : 2} />
        {node.width > MIN_LABEL_WIDTH && node.height > MIN_LABEL_HEIGHT && (
          <text x={node.x + 6} y={node.y + 16} fontSize={11} fontWeight={600} fill={labelColor}>
            {datum.name}
          </text>
        )}
        {datum.detail && node.width > MIN_LABEL_WIDTH && node.height > MIN_DETAIL_HEIGHT && (
          <text x={node.x + 6} y={node.y + node.height - 8} fontSize={10.5} fill={labelColor} opacity={0.9}>
            {datum.detail}
          </text>
        )}
      </g>
    );
  };

  return (
    <div role="group" aria-label={label} className="h-full w-full" style={height === undefined ? undefined : { height }}>
      <ResponsiveContainer width="100%" height="100%">
        <Treemap data={data.map((d) => ({ ...d }))} dataKey="value" nameKey="name" content={Tile} isAnimationActive={false} />
      </ResponsiveContainer>
    </div>
  );
}
