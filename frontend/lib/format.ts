// Shared display formatting helpers.
//
// All latency values are measured and stored in milliseconds everywhere
// (API responses, thresholds, chart data) — only how they're *rendered* to
// the user changes here, per the team's request to show latency in seconds.
export function formatLatencyMs(ms: unknown): string {
  if (typeof ms !== "number" || Number.isNaN(ms)) return "—";
  return `${(ms / 1000).toFixed(2)} s`;
}
