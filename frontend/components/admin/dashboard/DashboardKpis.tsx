"use client";

import { m } from "motion/react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  CircleDollarSign,
  Gauge,
  Layers,
  Search,
  ThumbsUp,
  UserCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import { ActiveVisitorsInfo } from "@/components/admin/ActiveVisitorsPanel";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import type { SparkPoint } from "@/components/charts/Sparkline";
import { CountUp } from "@/components/ui/CountUp";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useAuth } from "@/lib/auth-context";
import { ACTIVE_VISITORS_LABEL } from "@/lib/active-visitors";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import { useCarryHref } from "@/lib/entity-routes";
import { usePrefetchOnIntent, type QueryKey } from "@/lib/query-cache";
import { feedbackKeys } from "@/lib/feedback-api";
import { governanceKeys } from "@/lib/governance-api";
import { usageKeys } from "@/lib/use-usage-data";
import type { Audience } from "@/lib/types";
import type { DateRange } from "@/lib/range-context";
import {
  bucketLabel,
  dashboardApi,
  type DashboardData,
  type DashboardKpiKey,
  type SeriesPoint,
} from "@/lib/dashboard-api";

/**
 * Cache key for a KPI's intent prefetch: the target page's own query key when
 * it reads the same endpoint, so a hover-warmed response renders the page
 * instantly instead of only warming the server.
 */
export function prefetchKey(path: string, audience: Audience): QueryKey {
  switch (path) {
    case "/usage/summary":
      return usageKeys.summary(audience);
    case "/governance/monitoring":
      return governanceKeys.monitoring(audience);
    case "/feedback/analytics":
      return feedbackKeys.analytics(audience);
    default:
      return ["prefetch", path, audience];
  }
}

const integer = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

export interface KpiDef {
  key: DashboardKpiKey;
  label: string;
  icon: LucideIcon;
  /** Deep link to the page that owns the number (newplan §1). */
  href: string;
  /** Admin-relative GET warmed on intent (the target page's first query). */
  prefetchPath: string;
  goodDirection: "up" | "down";
  format: (value: number) => string;
  /** Window of the legacy value, when the server ignores `from` / `to`. */
  legacyWindow: string;
  /** Window of the windowed value when it is not the whole range. */
  rangeWindow?: string;
  /** Shows the "Active visitors" definition tip. */
  activeVisitors?: boolean;
}

export const DASHBOARD_KPIS: readonly KpiDef[] = [
  {
    key: "users",
    label: ACTIVE_VISITORS_LABEL,
    icon: Users,
    href: "/admin/visitors",
    prefetchPath: "/users",
    goodDirection: "up",
    format: (v) => integer.format(v),
    legacyWindow: "All time",
    activeVisitors: true,
  },
  {
    key: "active_users",
    label: `${ACTIVE_VISITORS_LABEL} (24h)`,
    icon: UserCheck,
    href: "/admin/usage?tab=users",
    prefetchPath: "/usage/summary",
    goodDirection: "up",
    format: (v) => integer.format(v),
    legacyWindow: "Last 24 hours",
    rangeWindow: "Last 24 hours of the range",
    activeVisitors: true,
  },
  {
    key: "sessions",
    label: "Sessions",
    icon: Layers,
    href: "/admin/usage?tab=sessions",
    prefetchPath: "/usage/summary",
    goodDirection: "up",
    format: (v) => integer.format(v),
    legacyWindow: "All time",
  },
  {
    key: "queries",
    label: "Queries",
    icon: Search,
    href: "/admin/usage?tab=queries",
    prefetchPath: "/usage/summary",
    goodDirection: "up",
    format: (v) => integer.format(v),
    legacyWindow: "All time",
  },
  {
    key: "feedback",
    label: "Feedback",
    icon: ThumbsUp,
    href: "/admin/feedback",
    prefetchPath: "/feedback/analytics",
    goodDirection: "up",
    format: (v) => integer.format(v),
    legacyWindow: "All time",
  },
  {
    key: "avg_latency_ms",
    label: "Average Latency",
    icon: Gauge,
    href: "/admin/governance?tab=Monitoring",
    prefetchPath: "/governance/monitoring",
    goodDirection: "down",
    format: (v) => formatLatencyMs(v),
    legacyWindow: "All time",
  },
  {
    key: "error_rate",
    label: "Error Rate",
    icon: AlertTriangle,
    href: "/admin/governance?tab=Monitoring",
    prefetchPath: "/governance/monitoring",
    goodDirection: "down",
    format: (v) => `${v.toFixed(2)}%`,
    legacyWindow: "Since server start",
  },
  {
    key: "cost_usd",
    label: "Cost",
    icon: CircleDollarSign,
    href: "/admin/usage",
    prefetchPath: "/usage/summary",
    goodDirection: "down",
    format: (v) => usd.format(v),
    legacyWindow: "Month to date",
  },
];

/** Value of a KPI; null when the server does not report it. */
export function kpiValue(data: DashboardData, key: DashboardKpiKey): number | null {
  if (key === "cost_usd") return data.cost_usd ?? data.cost?.mtd_usd ?? null;
  const v = data[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Sparkline points: the server series, else (queries only) the legacy 7-day trend. */
export function kpiTrend(data: DashboardData, key: DashboardKpiKey): SparkPoint[] {
  const bucket = data.window?.bucket ?? "day";
  const series: SeriesPoint[] | undefined = data.sparklines?.[key];
  if (series && series.length > 0) {
    return series.flatMap((p) => (p.value === null ? [] : [{ label: bucketLabel(p.t, bucket), value: p.value }]));
  }
  if (key === "queries" && data.query_trend?.length) {
    return data.query_trend.map((p) => ({ label: bucketLabel(p.date), value: p.queries }));
  }
  return [];
}

function windowLabelFor(def: KpiDef, data: DashboardData, range: DateRange): string {
  if (def.key === "cost_usd") return data.cost_usd !== undefined && data.window ? range.label : "Month to date";
  if (!data.window) return def.legacyWindow;
  return def.rangeWindow ?? range.label;
}

function KpiCard({ def, data, range }: { def: KpiDef; data: DashboardData; range: DateRange }) {
  const { token } = useAuth();
  const { audience } = useAudience();
  const router = useRouter();
  // Keep the dashboard's date range on the target page.
  const href = useCarryHref()(def.href);
  const intent = usePrefetchOnIntent(prefetchKey(def.prefetchPath, audience), (signal) =>
    dashboardApi.prefetch(token, def.prefetchPath, audience, signal),
  );
  const value = kpiValue(data, def.key);
  const trend = kpiTrend(data, def.key);
  const legacyTrendOnly = !data.sparklines?.[def.key] && def.key === "queries" && trend.length > 0;
  return (
    <KpiTrendCard
      label={def.label}
      icon={def.icon}
      value={value === null ? <span className="text-fg-subtle">—</span> : <CountUp value={value} format={def.format} />}
      delta={data.deltas?.[def.key] ?? null}
      goodDirection={def.goodDirection}
      trend={trend}
      href={href}
      onSelect={() => router.push(href)}
      intentHandlers={intent}
      windowLabel={windowLabelFor(def, data, range)}
      info={def.activeVisitors ? <ActiveVisitorsInfo /> : undefined}
      hint={value === null ? "Not reported by the server" : legacyTrendOnly ? "Trend: last 7 days" : undefined}
      tone={def.goodDirection === "down" && def.key === "error_rate" ? "danger" : "brand"}
      className="h-full"
    />
  );
}

export function DashboardKpis({
  data,
  loading,
  error,
  onRetry,
  range,
}: {
  data: DashboardData | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  range: DateRange;
}) {
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);

  if (!data && error) {
    return (
      <section aria-label="Key metrics" className="rounded-2xl border border-line bg-surface-card">
        <ErrorState title="Couldn't load the key metrics" message={error instanceof Error ? error.message : undefined} onRetry={onRetry} compact />
      </section>
    );
  }

  if (!data || loading) {
    return (
      <section aria-label="Key metrics" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {DASHBOARD_KPIS.map((def) => (
          <SkeletonCard key={def.key} label={`Loading ${def.label}`} />
        ))}
      </section>
    );
  }

  return (
    <m.section
      aria-label="Key metrics"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      variants={container}
      initial="hidden"
      animate="visible"
    >
      {DASHBOARD_KPIS.map((def) => (
        <m.div key={def.key} variants={item} data-testid={`kpi-${def.key}`}>
          <KpiCard def={def} data={data} range={range} />
        </m.div>
      ))}
    </m.section>
  );
}
