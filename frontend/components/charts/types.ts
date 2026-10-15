import type { SelectHandler } from "./primitives";

export interface TreemapDatum {
  name: string;
  value: number;
  /** Tile fill; defaults to the categorical palette. */
  color?: string;
  /** Label colour on a custom fill (e.g. from `readableOn`). */
  labelColor?: string;
  /** Second line on large tiles, also appended to the accessible name. */
  detail?: string;
  /** Marks the tile as selected (`aria-pressed`). */
  selected?: boolean;
}

export interface TreemapChartProps {
  data: readonly TreemapDatum[];
  onSelect?: SelectHandler<TreemapDatum>;
  label?: string;
  height?: number;
}

export interface SankeyNodeDatum {
  name: string;
}

export interface SankeyLinkDatum {
  /** Index into `nodes`. */
  source: number;
  /** Index into `nodes`. */
  target: number;
  value: number;
}

export interface SankeyChartProps {
  nodes: readonly SankeyNodeDatum[];
  links: readonly SankeyLinkDatum[];
  onSelect?: SelectHandler<SankeyNodeDatum>;
  label?: string;
  height?: number;
}
