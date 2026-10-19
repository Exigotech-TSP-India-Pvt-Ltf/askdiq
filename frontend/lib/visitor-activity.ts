"use client";

import type { SparkPoint } from "@/components/charts/Sparkline";
import { formatDay } from "@/components/admin/entity/shared";
import type { VisitorTimeline } from "./entity-stats-api";

export interface ActivitySparks {
  sessions: SparkPoint[];
  queries: SparkPoint[];
  feedback: SparkPoint[];
  active: SparkPoint[];
  cost: SparkPoint[];
  totals: { sessions: number; queries: number; feedback: number; activeDays: number; days: number };
}

/** Per-day series for the KPI sparklines (B7.2 `activity_series`, else `cost_series` for queries). */
export function activitySparks(tl: VisitorTimeline | null): ActivitySparks | null {
  if (!tl) return null;
  const byDay = new Map((tl.activity_series ?? []).map((d) => [d.day, d]));
  const days = tl.cost_series.map((c) => {
    const a = byDay.get(c.day);
    return { day: c.day, sessions: a?.sessions ?? 0, queries: a?.queries ?? c.queries, feedback: a?.feedback ?? 0, cost: c.cost_usd };
  });
  const pt = (day: string, value: number): SparkPoint => ({ label: formatDay(day), value });
  const sum = (key: "sessions" | "queries" | "feedback") => days.reduce((s, d) => s + d[key], 0);
  return {
    sessions: days.map((d) => pt(d.day, d.sessions)),
    queries: days.map((d) => pt(d.day, d.queries)),
    feedback: days.map((d) => pt(d.day, d.feedback)),
    active: days.map((d) => pt(d.day, d.sessions + d.queries + d.feedback > 0 ? 1 : 0)),
    cost: days.map((d) => pt(d.day, d.cost)),
    totals: {
      sessions: sum("sessions"),
      queries: sum("queries"),
      feedback: sum("feedback"),
      activeDays: days.filter((d) => d.sessions + d.queries + d.feedback > 0).length,
      days: days.length,
    },
  };
}

