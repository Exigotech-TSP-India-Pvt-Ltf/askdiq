"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Clock,
  DollarSign,
  FileSearch,
  Gauge,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Timer,
  Users,
  type LucideIcon,
} from "lucide-react";
import { m } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useApiQuery } from "@/lib/query-cache";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import {
  governanceApi,
  type GovernanceLogItem,
  type GovernanceOverview,
  type GovernanceStreamEvent,
  type Kpi,
  type KpiKey,
} from "@/lib/governance-api";
import { formatByUnit, formatCount, formatHour, windowSourceLabel } from "@/lib/governance-format";
import { logsHref, tabHref, tracesHref } from "@/lib/governance-links";
import { useGovernanceStream } from "@/lib/use-governance-stream";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import type { SparkPoint } from "@/components/charts/Sparkline";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton, SkeletonCard } from "@/components/ui/Skeleton";
import { HealthBanner } from "@/components/admin/governance/HealthBanner";
import { StatusTimeline } from "@/components/admin/governance/StatusTimeline";
import { IncidentList } from "@/components/admin/governance/IncidentList";
import { LiveIndicator } from "@/components/admin/governance/badges";

/** How long a row pushed over SSE stays highlighted. */
export const FRESH_HIGHLIGHT_MS = 4_000;
const MAX_INCIDENTS = 50;

interface KpiDef {
  key: KpiKey;
  label: string;
  icon: LucideIcon;
  goodDirection: "up" | "down";
  href: string;
}

const KPI_DEFS: readonly KpiDef[] = [
  { key: "requests", label: "Requests", icon: Activity, goodDirection: "up", href: tabHref("Monitoring") },
  { key: "error_rate", label: "Error rate", icon: AlertTriangle, goodDirection: "down", href: logsHref({ level: ["ERROR", "CRITICAL"] }) },
  { key: "latency_p50", label: "Latency p50", icon: Timer, goodDirection: "down", href: tabHref("Monitoring") },
  { key: "latency_p95", label: "Latency p95", icon: Gauge, goodDirection: "down", href: tracesHref({ latency_bucket: "slow" }) },
  { key: "active_users", label: "Active users", icon: Users, goodDirection: "up", href: tabHref("Traces") },
  { key: "groundedness", label: "Groundedness", icon: Sparkles, goodDirection: "up", href: tracesHref({ status: "ungrounded" }) },
  { key: "blocked_or_flagged", label: "Blocked or flagged", icon: ShieldAlert, goodDirection: "down", href: tracesHref({ status: "blocked" }) },
  { key: "cost_today", label: "Cost today", icon: DollarSign, goodDirection: "down", href: tabHref("Traces") },
];

function sparkPoints(kpi: Kpi, hours: readonly string[]): SparkPoint[] {
  return kpi.sparkline.flatMap((v, i) =>
    v === null || v === undefined ? [] : [{ label: hours[i] ? formatHour(hours[i]) : String(i), value: v }],
  );
}

/** Applies one SSE event to the cached overview (pure; tested). */
export function applyStreamEvent(current: GovernanceOverview | undefined, event: GovernanceStreamEvent): GovernanceOverview | undefined {
  switch (event.type) {
    case "snapshot":
      return event.data;
    case "health":
      return current ? { ...current, health: event.data.health } : current;
    case "incident": {
      if (!current) return current;
      const rest = current.incidents.filter((i) => i.id !== event.data.id);
      return { ...current, incidents: [event.data, ...rest].slice(0, MAX_INCIDENTS) };
    }
    case "monitoring": {
      if (!current) return current;
      const kpis = { ...current.kpis };
      for (const [name, value] of Object.entries(event.data.kpis)) {
        if (kpis[name]) kpis[name] = { ...kpis[name], value };
      }
      return { ...current, kpis };
    }
    default:
      return current;
  }
}

function useFreshIds(): [ReadonlySet<string>, (id: string) => void] {
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((t) => clearTimeout(t));
  }, []);
  const mark = useCallback((id: string) => {
    setFresh((s) => new Set(s).add(id));
    const existing = timers.current.get(id);
    if (existing) clearTimeout(existing);
    timers.current.set(
      id,
      setTimeout(() => {
        timers.current.delete(id);
        setFresh((s) => {
          const next = new Set(s);
          next.delete(id);
          return next;
        });
      }, FRESH_HIGHLIGHT_MS),
    );
  }, []);
  return [fresh, mark];
}

function InfoRow({ icon: Icon, label, detail, value }: { icon: LucideIcon; label: string; detail: string; value: string }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-line-brand bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
        <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-semibold text-fg">{label}</span>
        <span className="block truncate text-[11px] text-fg-subtle">{detail}</span>
      </span>
      <span className="shrink-0 font-mono text-xs font-semibold tabular-nums text-fg">{value}</span>
    </li>
  );
}

function totalLabel(page: { total: number; total_is_estimate: boolean } | undefined): string {
  if (!page) return "…";
  return `${page.total_is_estimate ? "≈ " : ""}${formatCount(page.total)}`;
}

export default function OverallView() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);
  const [freshIds, markFresh] = useFreshIds();

  const overview = useApiQuery<GovernanceOverview>(
    ["admin/governance/overview", audience],
    (signal) => governanceApi.overview(token, audience, signal),
    { enabled: Boolean(token), staleTime: 60_000, revalidateOnFocus: false },
  );
  const traces = useApiQuery(
    ["admin/governance/traces-total", audience],
    (signal) => governanceApi.traces(token, { audience, limit: 1 }, signal),
    { enabled: Boolean(token) },
  );
  const logs = useApiQuery(
    ["admin/governance/logs-total", audience],
    (signal) => governanceApi.logs(token, { audience, limit: 1 }, signal),
    { enabled: Boolean(token) },
  );

  const { setData } = overview;
  const onEvent = useCallback(
    (event: GovernanceStreamEvent) => {
      setData((current) => applyStreamEvent(current, event));
      if (event.type === "incident") markFresh(event.data.id);
    },
    [markFresh, setData],
  );
  const stream = useGovernanceStream({ audience, onEvent, enabled: Boolean(token) });

  const data = overview.data;
  const hours = useMemo(() => data?.hours ?? [], [data?.hours]);
  const incidents: GovernanceLogItem[] = data?.incidents ?? [];

  if (overview.error && !data) {
    return (
      <ErrorState
        title="Couldn't load the governance overview"
        message="The overview endpoint did not respond. Try again in a moment."
        onRetry={() => void overview.refetch()}
      />
    );
  }

  if (!data) {
    return (
      <div className="space-y-6" aria-busy="true">
        <Skeleton className="h-24 w-full rounded-2xl" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {KPI_DEFS.map((d) => (
            <SkeletonCard key={d.key} label={`Loading ${d.label}`} />
          ))}
        </div>
      </div>
    );
  }

  const errorKpi = data.kpis.error_rate;
  const requestsKpi = data.kpis.requests;
  const quality = data.online_quality_kpis;

  return (
    <m.div className="space-y-6" variants={container} initial="hidden" animate="visible">
      <div className="flex justify-end">
        <LiveIndicator status={stream.status} updatedAt={stream.lastEventAt || overview.updatedAt} />
      </div>

      <m.div variants={item}>
        <HealthBanner health={data.health} />
      </m.div>

      <m.div variants={item} className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {KPI_DEFS.map((def) => {
          const kpi = data.kpis[def.key];
          if (!kpi) return null;
          return (
            <KpiTrendCard
              key={def.key}
              label={def.label}
              value={formatByUnit(kpi.value, kpi.unit)}
              trend={sparkPoints(kpi, hours)}
              goodDirection={def.goodDirection}
              icon={def.icon}
              href={def.href}
              tone={def.goodDirection === "down" && def.key === "error_rate" ? "danger" : "brand"}
              hint={windowSourceLabel(kpi.window, kpi.source)}
            />
          );
        })}
      </m.div>

      <m.section variants={item} aria-labelledby="gov-timeline" className="rounded-xl border border-line bg-surface-card p-6">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 id="gov-timeline" className="text-base font-semibold text-fg">
              Status timeline
            </h2>
            <p className="mt-1 text-xs text-fg-subtle">
              Hourly error rate against the health thresholds, last {data.window_hours} h. Markers are incidents: select
              one to open Logs for that hour.
            </p>
          </div>
        </div>
        <StatusTimeline
          hours={hours}
          errorRates={errorKpi?.sparkline ?? []}
          requests={requestsKpi?.sparkline ?? []}
          incidents={incidents}
          signal={data.health.signals.error_rate}
        />
      </m.section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <m.section
          variants={item}
          aria-labelledby="gov-incidents"
          className="rounded-xl border border-line bg-surface-card p-6 xl:col-span-2"
        >
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 id="gov-incidents" className="text-base font-semibold text-fg">
              Recent incidents
            </h2>
            <span className="text-[11px] text-fg-subtle">CRITICAL and ALERT · newest first</span>
          </div>
          <IncidentList items={incidents} freshIds={freshIds} />
        </m.section>

        <m.section variants={item} aria-labelledby="gov-glance" className="rounded-xl border border-line bg-surface-card p-6">
          <h2 id="gov-glance" className="text-base font-semibold text-fg">
            At a glance
          </h2>
          <ul className="mt-2 divide-y divide-line">
            <InfoRow icon={FileSearch} label="Traces" detail="All recorded request journeys" value={totalLabel(traces.data)} />
            <InfoRow icon={ScrollText} label="Logs" detail="Audit and security entries (selected range: all)" value={totalLabel(logs.data)} />
            <InfoRow icon={AlertTriangle} label="Incidents" detail="CRITICAL / ALERT in the incident window" value={formatCount(incidents.length)} />
            <InfoRow icon={Clock} label="Health window" detail="Signals are evaluated over" value={`${data.health.window_minutes} min`} />
            <InfoRow
              icon={ShieldCheck}
              label="Online quality"
              detail={`${formatCount(quality.sampled_count ?? 0)} answers sampled`}
              value={
                quality.violation_rate === undefined ? "—" : `${(quality.violation_rate * 100).toFixed(1)}% violations`
              }
            />
          </ul>
        </m.section>
      </div>
    </m.div>
  );
}
