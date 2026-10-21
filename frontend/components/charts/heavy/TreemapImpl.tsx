"use client";

import type React from "react";
import { ResponsiveContainer, Treemap, type TreemapNode } from "recharts";
import { useChartTheme } from "@/lib/use-chart-theme";
import { ChartEmpty, fitText } from "../primitives";
import type { TreemapChartProps, TreemapDatum } from "../types";

const MIN_LABEL_WIDTH = 48;
const MIN_LABEL_HEIGHT = 22;
const MIN_DETAIL_HEIGHT = 40;
const LABEL_PAD = 12;
/** A label cut to fewer characters than this is hidden rather than shown as "Contact ar…". */
const MIN_LABEL_CHARS = 4;

/** Tile text that fits `width`, or null when only a stub would fit (VA-27). */
export function tileLabel(text: string, width: number, fontSize: number): string | null {
  const fitted = fitText(text, Math.max(0, width - LABEL_PAD), fontSize);
  if (fitted === text) return text;
  return fitted.length - 1 >= MIN_LABEL_CHARS ? fitted : null;
}

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
    const name = node.height > MIN_LABEL_HEIGHT && node.width > MIN_LABEL_WIDTH ? tileLabel(datum.name, node.width, 11) : null;
    const detail =
      datum.detail && name && node.height > MIN_DETAIL_HEIGHT ? tileLabel(datum.detail, node.width, 10.5) : null;
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
        <title>{`${datum.name}: ${datum.value}${datum.detail ? ` (${datum.detail})` : ""}`}</title>
        <rect x={node.x} y={node.y} width={node.width} height={node.height} rx={4} fill={color} stroke={ct.tooltip.background} strokeWidth={datum.selected ? 3 : 2} />
        {name && (
          <text x={node.x + 6} y={node.y + 16} fontSize={11} fontWeight={600} fill={labelColor}>
            {name}
          </text>
        )}
        {detail && (
          <text x={node.x + 6} y={node.y + node.height - 8} fontSize={10.5} fill={labelColor}>
            {detail}
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
