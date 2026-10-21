/**
 * Gauge data for Governance > Monitoring "Resources" (backend
 * `app/governance/resource_store.py`). Every gauge names its scope: the
 * server (host), the container (cgroup limit) or the API workers (process).
 */
import type { QuotaPoint, ResourcePoint, ResourcesSnapshot } from "@/lib/governance-api";

/** One sample of a gauge's 15-minute trend (value on the gauge's 0-100 scale). */
export interface TrendPoint {
  ts: string;
  value: number;
  /** Human reading of the sample (e.g. "42%", "219 MB", "160 ms"). */
  display: string;
}

/** Trend window under each gauge. */
export const TREND_WINDOW_MS = 15 * 60_000;

export interface ResourceGauge {
  key: string;
  label: string;
  /** 0-100 for the dial; null = no data. */
  value: number | null;
  caption: string;
  bands: { warn: number; danger: number };
  /** Last 15 minutes from `resources.history`, oldest first. */
  trend: TrendPoint[];
}

/** Event-loop lag shown on a 0-1 s dial (1 s = 100%). */
export const LOOP_LAG_FULL_SCALE_MS = 1000;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function scopeName(scope: string | undefined): string {
  return scope === "container" ? "Container" : "Server";
}

const pct = (v: number) => `${Math.round(v)}%`;

function recent<T extends { ts: string }>(points: readonly T[] | undefined, nowMs: number): T[] {
  const cut = nowMs - TREND_WINDOW_MS;
  return (points ?? []).filter((p) => {
    const t = Date.parse(p.ts);
    return Number.isFinite(t) ? t >= cut : true;
  });
}

function series(
  points: readonly ResourcePoint[],
  pick: (p: ResourcePoint) => number | null,
  display: (p: ResourcePoint, value: number) => string = (_p, v) => pct(v),
): TrendPoint[] {
  return points.flatMap((p) => {
    const value = pick(p);
    return value === null ? [] : [{ ts: p.ts, value, display: display(p, value) }];
  });
}

/** Busiest-of-chat-and-embedding utilisation per quota sample (last 15 min). */
export function quotaTrend(points: readonly QuotaPoint[] | undefined, kind: "tpm" | "rpm", nowMs = Date.now()): TrendPoint[] {
  return recent(points, nowMs).flatMap((p) => {
    const headrooms = (kind === "tpm"
      ? [p.chat_tpm_headroom_pct, p.embedding_tpm_headroom_pct]
      : [p.chat_rpm_headroom_pct, p.embedding_rpm_headroom_pct]
    ).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    if (headrooms.length === 0) return [];
    const value = Math.max(0, Math.min(100, 100 - Math.min(...headrooms)));
    return [{ ts: p.ts, value, display: pct(value) }];
  });
}

export function resourceGauges(r: Partial<ResourcesSnapshot> | undefined, nowMs = Date.now()): ResourceGauge[] {
  const history = recent(r?.history, nowMs);
  const effective = r?.effective;
  const rss = num(r?.process?.rss_mb);
  const memoryBase = num(r?.container?.memory_limit_mb) ?? num(r?.host?.memory_total_mb);
  const lag = num(r?.event_loop?.lag_p95_ms);
  const workers = num(r?.workers_live) ?? 0;
  const pool = r?.db_pool;
  const queries = r?.queries;
  return [
    {
      key: "cpu",
      label: `${scopeName(effective?.cpu_scope)} CPU`,
      value: num(effective?.cpu_percent),
      caption: `${scopeName(effective?.cpu_scope)} CPU`,
      bands: { warn: 80, danger: 95 },
      trend: series(history, (p) => num(p.cpu_percent)),
    },
    {
      key: "memory",
      label: `${scopeName(effective?.memory_scope)} memory`,
      value: num(effective?.memory_percent),
      caption:
        effective?.memory_scope === "container" && num(r?.container?.memory_mb) !== null
          ? `${scopeName(effective?.memory_scope)} memory · ${Math.round(r?.container?.memory_mb ?? 0)} MB`
          : `${scopeName(effective?.memory_scope)} memory`,
      bands: { warn: 85, danger: 95 },
      trend: series(history, (p) => num(p.memory_percent)),
    },
    {
      key: "rss",
      label: "API workers RSS",
      value: rss !== null && memoryBase ? Math.min(100, (rss * 100) / memoryBase) : null,
      caption: rss === null ? "API workers RSS" : `API workers RSS · ${Math.round(rss)} MB · ${workers} worker${workers === 1 ? "" : "s"}`,
      bands: { warn: 75, danger: 90 },
      trend: series(
        history,
        (p) => {
          const mb = num(p.rss_mb);
          return mb !== null && memoryBase ? Math.min(100, (mb * 100) / memoryBase) : null;
        },
        (p) => `${Math.round(num(p.rss_mb) ?? 0)} MB`,
      ),
    },
    {
      key: "loop",
      label: "Event-loop lag",
      value: lag === null ? null : Math.min(100, (lag * 100) / LOOP_LAG_FULL_SCALE_MS),
      caption: lag === null ? "Event-loop lag" : `Event-loop lag · p95 ${Math.round(lag)} ms`,
      bands: { warn: 20, danger: 100 },
      trend: series(
        history,
        (p) => {
          const ms = num(p.loop_lag_p95_ms);
          return ms === null ? null : Math.min(100, (ms * 100) / LOOP_LAG_FULL_SCALE_MS);
        },
        (p) => `${Math.round(num(p.loop_lag_p95_ms) ?? 0)} ms`,
      ),
    },
    {
      key: "pool",
      label: "DB pool",
      value: num(pool?.in_use_percent),
      caption: pool ? `DB pool · ${pool.checked_out}/${pool.capacity} in use` : "DB pool",
      bands: { warn: 70, danger: 90 },
      trend: series(history, (p) => num(p.db_pool_percent)),
    },
    {
      key: "queries",
      label: "Chat in flight",
      value: num(queries?.in_use_percent),
      caption: queries?.capacity
        ? `Chat in flight · ${queries.active}/${queries.capacity} LLM slots`
        : "Chat in flight",
      bands: { warn: 70, danger: 90 },
      trend: series(history, (p) => num(p.queries_percent)),
    },
  ];
}
