// Usage & Cost console tab ids and the `?tab=` URL parser (newplan §3).
//
// Live Telemetry Studio is now the "Overview" tab. Old bookmarks keep working:
// `?tab=live_telemetry` (the former Live Telemetry id) and `?tab=overall`
// (an older alias) both resolve to `overview`.

export const USAGE_TAB_IDS = ["overview", "users", "sessions", "queries"] as const;

export type UsageTabId = (typeof USAGE_TAB_IDS)[number];

export const DEFAULT_USAGE_TAB: UsageTabId = "overview";

export const USAGE_TAB_ALIASES: Readonly<Record<string, UsageTabId>> = {
  live_telemetry: "overview",
  overall: "overview",
};

function isUsageTabId(value: string): value is UsageTabId {
  return (USAGE_TAB_IDS as readonly string[]).includes(value);
}

/** Resolve a `?tab=` value to a tab id; unknown or missing values give the default. */
export function parseUsageTab(param: string | null | undefined): UsageTabId {
  if (!param) return DEFAULT_USAGE_TAB;
  if (isUsageTabId(param)) return param;
  return Object.prototype.hasOwnProperty.call(USAGE_TAB_ALIASES, param)
    ? USAGE_TAB_ALIASES[param]
    : DEFAULT_USAGE_TAB;
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

/** `?from=` / `?to=` day (YYYY-MM-DD) from a deep link; anything else is ignored. */
export function parseUsageDay(param: string | null | undefined): string {
  if (!param || !DAY_RE.test(param)) return "";
  return Number.isNaN(Date.parse(`${param}T00:00:00Z`)) ? "" : param;
}
