"use client";

import type React from "react";
import { ResponsiveContainer, Sankey } from "recharts";
import { useChartTheme } from "@/lib/use-chart-theme";
import { ChartEmpty } from "../primitives";
import type { SankeyChartProps } from "../types";

interface NodeShapeProps {
  x: number;
  y: number;
  width: number;
  height: number;
  index: number;
  payload: { name?: unknown; value?: unknown };
}

/** Loaded through `next/dynamic` (see `SankeyChart`); do not import directly. */
export default function SankeyImpl({ nodes, links, onSelect, label = "Flow", height }: SankeyChartProps) {
  const ct = useChartTheme();
  if (nodes.length === 0 || links.length === 0) return <ChartEmpty height={height} />;

  const Node = ({ x, y, width, height: h, index, payload }: NodeShapeProps): React.ReactElement => {
    const datum = nodes[index];
    const select = onSelect && datum ? () => onSelect(datum) : undefined;
    return (
      <g
        role={select ? "button" : "img"}
        tabIndex={select ? 0 : undefined}
        aria-label={`${String(payload.name ?? datum?.name ?? "")}: ${String(payload.value ?? "")}`}
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
        <rect x={x} y={y} width={width} height={h} rx={2} fill={ct.categorical[index % ct.categorical.length]} />
        <text x={x + width + 6} y={y + h / 2} dy="0.32em" fontSize={11} fill={ct.axis}>
          {String(payload.name ?? "")}
        </text>
      </g>
    );
  };

  return (
    <div role="group" aria-label={label} className="h-full w-full" style={height === undefined ? undefined : { height }}>
      <ResponsiveContainer width="100%" height="100%">
        <Sankey
          data={{ nodes: nodes.map((n) => ({ ...n })), links: links.map((l) => ({ ...l })) }}
          node={(props) => <Node {...(props as unknown as NodeShapeProps)} />}
          link={{ stroke: ct.sequential[2], strokeOpacity: 0.35 }}
          nodePadding={16}
          margin={{ top: 8, right: 96, bottom: 8, left: 8 }}
        />
      </ResponsiveContainer>
    </div>
  );
}
