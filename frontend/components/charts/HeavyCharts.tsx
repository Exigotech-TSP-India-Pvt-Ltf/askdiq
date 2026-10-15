"use client";

/**
 * Heavy Recharts charts (Treemap, Sankey) are code-split with `next/dynamic`
 * (newplan §13.3) so pages that do not show them never download them.
 */
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./ChartCard";
import type { SankeyChartProps, TreemapChartProps } from "./types";

export const TreemapChart = dynamic<TreemapChartProps>(() => import("./heavy/TreemapImpl"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});

export const SankeyChart = dynamic<SankeyChartProps>(() => import("./heavy/SankeyImpl"), {
  ssr: false,
  loading: () => <ChartSkeleton />,
});
