/**
 * Chart kit (newplan §15.4). Import from "@/components/charts".
 * Every chart reads colours from `useChartTheme()`, takes typed data and an
 * optional `onSelect(datum)` that fires on click and on Enter / Space.
 */
export { ChartCard, ChartSkeleton, ChartTable, toChartTable } from "./ChartCard";
export type { ChartCardProps, ChartCardTitleStyle, ChartCardVariant, ChartTableColumn, ChartTableData } from "./ChartCard";
export { Sparkline } from "./Sparkline";
export type { SparkPoint, SparkTone, SparklineProps } from "./Sparkline";
export { KpiTrendCard } from "./KpiTrendCard";
export type { KpiTrendCardProps } from "./KpiTrendCard";
export { StackedArea } from "./StackedArea";
export type { StackedAreaProps } from "./StackedArea";
export { PercentileBand } from "./PercentileBand";
export type { PercentileBandProps, PercentilePoint } from "./PercentileBand";
export { Waterfall } from "./Waterfall";
export type { WaterfallProps, WaterfallStep } from "./Waterfall";
export { CalendarHeatmap } from "./CalendarHeatmap";
export type { CalendarHeatmapProps, HeatCell } from "./CalendarHeatmap";
export { RadialGauge } from "./RadialGauge";
export type { GaugeBands, GaugeDatum, RadialGaugeProps } from "./RadialGauge";
export { BulletChart } from "./BulletChart";
export type { BulletChartProps, BulletDatum } from "./BulletChart";
export { Funnel } from "./Funnel";
export type { FunnelProps, FunnelStage } from "./Funnel";
export { QuadrantScatter } from "./QuadrantScatter";
export type { QuadrantLabels, QuadrantScatterProps, ScatterPoint } from "./QuadrantScatter";
export { Histogram, binValues } from "./Histogram";
export type { HistogramBin, HistogramProps } from "./Histogram";
export { Radar } from "./Radar";
export type { RadarProps } from "./Radar";
export { TreemapChart, SankeyChart } from "./HeavyCharts";
export type { SankeyChartProps, SankeyLinkDatum, SankeyNodeDatum, TreemapChartProps, TreemapDatum } from "./types";
export type { AxisLabelProps, SeriesDef } from "./cartesian";
export type { SelectHandler } from "./primitives";
