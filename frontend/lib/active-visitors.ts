/**
 * Active visitors: one definition behind every "active users" number
 * (backend `app.dashboard.active_visitors`). Reported as an additive
 * `active_visitors` block on `GET /admin/dashboard` and
 * `GET /admin/visitors/stats`; counts only, never IPs or ids.
 */

export const ACTIVE_VISITORS_LABEL = "Active visitors";

/** Shown in every "Active visitors" info tooltip; the server sends the same text as `definition`. */
export const ACTIVE_VISITORS_DEFINITION =
  "Active visitors are distinct end users with at least one query in the period. " +
  "A visitor is identified by their client id, or by their IP address when the client id is " +
  "missing or the default anonymous value. Traffic from test IPs is excluded.";

export interface ActiveVisitorsPeriod {
  /** Active visitors in the last `days` days (ending now). */
  value: number;
  /** Active visitors in the equal window just before. */
  previous: number;
  /** `(value - previous) / previous`; null when `previous` is 0. */
  delta: number | null;
  days: number;
}

export interface ActiveVisitors {
  definition: string;
  /** Active visitors in the last `active_now_minutes` minutes. */
  active_now: number;
  active_now_minutes: number;
  dau: ActiveVisitorsPeriod;
  wau: ActiveVisitorsPeriod;
  mau: ActiveVisitorsPeriod;
}

export type ActivePeriodKey = "dau" | "wau" | "mau";

export const ACTIVE_PERIODS: readonly { key: ActivePeriodKey; title: string }[] = [
  { key: "dau", title: "Daily active visitors" },
  { key: "wau", title: "Weekly active visitors" },
  { key: "mau", title: "Monthly active visitors" },
];

const percent = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" });

/** "Last 24 hours" / "Last 7 days". */
export function periodWindow(days: number): string {
  return days === 1 ? "Last 24 hours" : `Last ${days} days`;
}

/** "vs previous 7 days": the window a period delta compares against. */
export function comparisonLabel(days: number): string {
  return days === 1 ? "vs previous 24 hours" : `vs previous ${days} days`;
}

/** Trend vs the previous equal window, or null when there is nothing to compare. */
export function trendLabel(period: ActiveVisitorsPeriod): { value: string; isPositive: boolean } | null {
  if (period.delta === null) return null;
  const previous = period.days === 1 ? "previous 24 hours" : `previous ${period.days} days`;
  return { value: `${percent.format(period.delta)} vs ${previous}`, isPositive: period.delta >= 0 };
}
