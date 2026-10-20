"use client";

/**
 * KPI strip and charts above the tabs (planchanges4 F1.4.1, newplan §7.8 and
 * §15.4). Every card and chart mark drills down by writing the Users-tab
 * filters to the URL.
 */
import React, { useMemo } from "react";
import { m } from "motion/react";
import { LogIn, UserCheck, UserRoundX, Users, UserPlus } from "lucide-react";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import type { SparkPoint } from "@/components/charts/Sparkline";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { Funnel, type FunnelStage } from "@/components/charts/Funnel";
import { Histogram, type HistogramBin } from "@/components/charts/Histogram";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import {
  APPROVAL_BUCKETS,
  ROLE_LABEL,
  type AuthEvent,
  type FunnelStageId,
  type Role,
  type UserStats,
  type UserStatus,
} from "@/lib/user-management-api";
import { DailyBars, Donut, type DonutDatum } from "./charts";
import { umHref } from "./use-um-url";
import type { DeepLinkUpdate } from "@/lib/use-deep-link";

export const SPARK_DAYS = 14;
const LOGIN_CHART_DAYS = 14;

const dayLabel = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" });

function formatDay(isoDate: string): string {
  const d = new Date(`${isoDate.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? isoDate : dayLabel.format(d);
}

/**
 * Daily counts of `actions` summed over the last `days` UTC days
 * (zero-filled), from the per-event series precomputed with the stats.
 */
export function dailyEventSeries(
  perDay: UserStats["events_per_day"],
  actions: readonly AuthEvent[],
  days: number,
  now: Date = new Date(),
): SparkPoint[] {
  const counts = new Map<string, number>();
  for (const action of actions) {
    for (const point of perDay?.[action] ?? []) {
      const key = point.date.slice(0, 10);
      counts.set(key, (counts.get(key) ?? 0) + point.count);
    }
  }
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Array.from({ length: days }, (_, i) => {
    const key = new Date(today - (days - 1 - i) * 86_400_000).toISOString().slice(0, 10);
    return { label: formatDay(key), value: counts.get(key) ?? 0 };
  });
}

const FUNNEL_LABEL: Record<FunnelStageId, string> = {
  signed_in: "Signed in",
  requested: "Requested",
  approved: "Approved",
  active: "Active",
};

/** Users-tab filter each funnel stage drills into. */
const FUNNEL_FILTER: Record<FunnelStageId, UserStatus | null> = {
  signed_in: null,
  requested: "pending",
  approved: "active",
  active: "active",
};

interface UserOverviewProps {
  stats: UserStats | undefined;
  statsLoading: boolean;
  statsError: string | null;
  onRetry: () => void;
  /** Applies a drill-down (switches to the Users tab with filters). */
  onDrill: (update: DeepLinkUpdate) => void;
}

export function UserOverview({ stats, statsLoading, statsError, onRetry, onDrill }: UserOverviewProps) {
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);
  const kpis = stats?.kpis;

  const loginSeries = useMemo<SparkPoint[]>(
    () => (stats?.logins_per_day ?? []).slice(-SPARK_DAYS).map((d) => ({ label: formatDay(d.date), value: d.count })),
    [stats],
  );
  const series = useMemo(() => {
    const perDay = stats?.events_per_day;
    return {
      total: dailyEventSeries(perDay, ["access_requested", "user_invited"], SPARK_DAYS),
      active: dailyEventSeries(perDay, ["access_approved", "user_reactivated", "user_invited"], SPARK_DAYS),
      pending: dailyEventSeries(perDay, ["access_requested"], SPARK_DAYS),
      suspended: dailyEventSeries(perDay, ["user_suspended"], SPARK_DAYS),
    };
  }, [stats]);

  const funnel = useMemo<(FunnelStage & { id: FunnelStageId })[]>(
    () => (stats?.funnel ?? []).map((s) => ({ id: s.stage, label: FUNNEL_LABEL[s.stage], value: s.count })),
    [stats],
  );
  const roles = useMemo<DonutDatum[]>(
    () => (stats?.role_mix ?? []).map((r) => ({ id: r.role, label: ROLE_LABEL[r.role], value: r.count })),
    [stats],
  );
  const approve = useMemo<HistogramBin[]>(() => {
    const counts = new Map((stats?.time_to_approve ?? []).map((b) => [b.bucket, b.count]));
    return APPROVAL_BUCKETS.map((bucket, i) => ({ x0: i, x1: i, count: counts.get(bucket) ?? 0 }));
  }, [stats]);
  const logins = useMemo(
    () => (stats?.logins_per_day ?? []).slice(-LOGIN_CHART_DAYS).map((d) => ({ label: formatDay(d.date), value: d.count, date: d.date })),
    [stats],
  );

  const value = (n: number | undefined) => (statsLoading || n === undefined ? "—" : n.toLocaleString());
  const chartState = { loading: statsLoading, error: statsError, onRetry };

  const cards: {
    key: string;
    label: string;
    value: React.ReactNode;
    trend: SparkPoint[];
    hint: string;
    href: string;
    icon: typeof Users;
    tone?: "brand" | "ok" | "warn" | "danger";
    highlight?: boolean;
  }[] = [
    { key: "total", label: "Total users", value: value(kpis?.total), trend: series.total, hint: "New requests and invites per day", href: umHref({ tab: "users" }), icon: Users },
    { key: "active", label: "Active", value: value(kpis?.active), trend: series.active, hint: "Activations per day", href: umHref({ tab: "users", status: "active" }), icon: UserCheck, tone: "ok" },
    {
      key: "pending",
      label: "Pending",
      value: value(kpis?.pending),
      trend: series.pending,
      hint: `${kpis?.open_requests ?? 0} open requests · new per day`,
      href: umHref({ tab: "users", status: "pending" }),
      icon: UserPlus,
      tone: "warn",
      highlight: (kpis?.pending ?? 0) > 0,
    },
    { key: "suspended", label: "Suspended", value: value(kpis?.suspended), trend: series.suspended, hint: "Suspensions per day", href: umHref({ tab: "users", status: "suspended" }), icon: UserRoundX, tone: "danger" },
    { key: "logins", label: "Logins (24h)", value: value(kpis?.logins_24h), trend: loginSeries, hint: "Sign-ins per day", href: umHref({ tab: "audit", action: "login_success" }), icon: LogIn },
  ];

  return (
    <div className="space-y-6">
      <m.div
        variants={container}
        initial="hidden"
        animate="visible"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5"
        data-testid="um-kpis"
        data-tour="user-management-kpis"
      >
        {cards.map((c) => (
          <m.div key={c.key} variants={item} data-testid={`kpi-${c.key}`} data-tour={`user-management-kpi-${c.key}`} data-highlight={c.highlight ? "true" : undefined}>
            <KpiTrendCard
              label={c.label}
              value={c.value}
              trend={c.trend}
              hint={c.hint}
              href={c.href}
              icon={c.icon}
              tone={c.tone}
              className={
                c.highlight
                  ? "h-full !border-status-warn/60 !bg-status-warn-bg ring-1 ring-status-warn/40"
                  : "h-full"
              }
            />
          </m.div>
        ))}
      </m.div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div data-tour="user-management-logins-chart">
        <ChartCard
          title="Logins per day"
          subtitle="Successful sign-ins; select a day to sort Users by last login"
          window={`Last ${LOGIN_CHART_DAYS} days`}
          {...chartState}
          empty={!statsLoading && !statsError && logins.length === 0}
          table={toChartTable(logins, [
            { key: "label", label: "Day" },
            { key: "value", label: "Sign-ins", numeric: true },
          ])}
        >
          <DailyBars
            data={logins}
            label="Logins per day"
            valueLabel="Sign-ins"
            xAxisLabel="Date"
            yAxisLabel="Sign-ins per day"
            onSelect={() => onDrill({ tab: "users", sort: "last_login_at", order: "desc", status: null, role: null })}
          />
        </ChartCard>
        </div>

        <div data-tour="user-management-funnel">
        <ChartCard
          title="Access-request funnel"
          subtitle="Select a stage to filter Users"
          {...chartState}
          empty={!statsLoading && !statsError && funnel.length === 0}
          table={toChartTable(funnel, [
            { key: "label", label: "Stage" },
            { key: "value", label: "Users", numeric: true },
          ])}
        >
          <Funnel
            data={funnel}
            label="Access-request funnel"
            valueLabel="Users"
            onSelect={(stage) => {
              const id = (stage as FunnelStage & { id: FunnelStageId }).id;
              onDrill({ tab: "users", status: FUNNEL_FILTER[id], role: null });
            }}
          />
        </ChartCard>
        </div>

        <div data-tour="user-management-role-distribution">
        <ChartCard
          title="Role distribution"
          subtitle="Active users; select a role to filter Users"
          {...chartState}
          empty={!statsLoading && !statsError && roles.every((r) => r.value === 0)}
          emptyMessage="No active users yet"
          table={toChartTable(roles, [
            { key: "label", label: "Role" },
            { key: "value", label: "Active users", numeric: true },
          ])}
        >
          <Donut
            data={roles}
            label="Role distribution"
            centerLabel="Active"
            onSelect={(d) => onDrill({ tab: "users", role: d.id as Role, status: "active" })}
          />
        </ChartCard>
        </div>

        <div data-tour="user-management-time-to-approve">
        <ChartCard
          title="Time to approve"
          subtitle="Approved requests by wait; select a bin to see decisions"
          window={stats ? `Last ${stats.days} days` : undefined}
          {...chartState}
          empty={!statsLoading && !statsError && approve.every((b) => b.count === 0)}
          emptyMessage="No approvals in this period"
          table={toChartTable(
            approve.map((b, i) => ({ bucket: APPROVAL_BUCKETS[i], count: b.count })),
            [
              { key: "bucket", label: "Wait" },
              { key: "count", label: "Approvals", numeric: true },
            ],
          )}
        >
          <Histogram
            data={approve}
            label="Time to approve"
            valueLabel="Approvals"
            xAxisLabel="Wait from request to approval"
            yAxisLabel="Approved requests"
            formatEdge={(i) => APPROVAL_BUCKETS[i] ?? ""}
            onSelect={() => onDrill({ tab: "requests", view: "approved" })}
          />
        </ChartCard>
        </div>
      </div>
    </div>
  );
}
