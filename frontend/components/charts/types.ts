import type { SelectHandler } from "./primitives";

export interface TreemapDatum {
  name: string;
  value: number;
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
