/**
 * Gauge data for Governance > Monitoring "Resources" (backend
 * `app/governance/resource_store.py`). Every gauge names its scope: the
 * server (host), the container (cgroup limit) or the API workers (process).
 */
import type { ResourcesSnapshot } from "@/lib/governance-api";

export interface ResourceGauge {
  key: string;
  label: string;
  /** 0-100 for the dial; null = no data. */
  value: number | null;
  caption: string;
  bands: { warn: number; danger: number };
}

/** Event-loop lag shown on a 0-1 s dial (1 s = 100%). */
export const LOOP_LAG_FULL_SCALE_MS = 1000;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function scopeName(scope: string | undefined): string {
  return scope === "container" ? "Container" : "Server";
}

export function resourceGauges(r: Partial<ResourcesSnapshot> | undefined): ResourceGauge[] {
  const effective = r?.effective;
  const rss = num(r?.process?.rss_mb);
  const memoryBase = num(r?.container?.memory_limit_mb) ?? num(r?.host?.memory_total_mb);
  const lag = num(r?.event_loop?.lag_p95_ms);
  const workers = num(r?.workers_live) ?? 0;
  const pool = r?.db_pool;
  return [
    {
      key: "cpu",
      label: `${scopeName(effective?.cpu_scope)} CPU`,
      value: num(effective?.cpu_percent),
      caption: `${scopeName(effective?.cpu_scope)} CPU`,
      bands: { warn: 80, danger: 95 },
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
    },
    {
      key: "rss",
      label: "API workers RSS",
      value: rss !== null && memoryBase ? Math.min(100, (rss * 100) / memoryBase) : null,
      caption: rss === null ? "API workers RSS" : `API workers RSS · ${Math.round(rss)} MB · ${workers} worker${workers === 1 ? "" : "s"}`,
      bands: { warn: 75, danger: 90 },
    },
    {
      key: "loop",
      label: "Event-loop lag",
      value: lag === null ? null : Math.min(100, (lag * 100) / LOOP_LAG_FULL_SCALE_MS),
      caption: lag === null ? "Event-loop lag" : `Event-loop lag · p95 ${Math.round(lag)} ms`,
      bands: { warn: 20, danger: 100 },
    },
    {
      key: "pool",
      label: "DB pool",
      value: num(pool?.in_use_percent),
      caption: pool ? `DB pool · ${pool.checked_out}/${pool.capacity} in use` : "DB pool",
      bands: { warn: 70, danger: 90 },
    },
  ];
}
