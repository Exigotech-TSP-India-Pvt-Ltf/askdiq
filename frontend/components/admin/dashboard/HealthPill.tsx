"use client";

import Link from "next/link";
import { isHealthState, type HealthState } from "@/lib/dashboard-api";
import { useDashboardHealth } from "@/lib/use-dashboard";

const TONE: Record<HealthState, { pill: string; dot: string }> = {
  Healthy: { pill: "bg-status-ok-bg text-status-ok-fg", dot: "bg-status-ok" },
  Degraded: { pill: "bg-status-warn-bg text-status-warn-fg", dot: "bg-status-warn" },
  Incident: { pill: "bg-status-danger-bg text-status-danger-fg", dot: "bg-status-danger" },
};

/**
 * System health from `GET /admin/governance/overview` (PR-08). Renders
 * nothing while loading or when the endpoint is unavailable, so it never
 * blocks or misreports.
 */
export function HealthPill() {
  const { data } = useDashboardHealth();
  const status = data?.health?.status;
  if (!isHealthState(status)) return null;
  const tone = TONE[status];
  return (
    <Link
      href="/admin/governance?tab=Overall"
      aria-label={`System health: ${status}. Open the governance overview`}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold transition duration-150 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${tone.pill}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden="true" />
      {status}
    </Link>
  );
}
