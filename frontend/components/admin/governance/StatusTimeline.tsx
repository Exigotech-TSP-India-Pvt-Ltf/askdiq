"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import type { GovernanceLogItem, HealthSignal, HealthState } from "@/lib/governance-api";
import { formatHour, formatPct } from "@/lib/governance-format";
import { logsHref } from "@/lib/governance-links";
import { parseServerDate } from "@/lib/time";
import { focusRing } from "@/components/ui/styles";

const HOUR_MS = 60 * 60 * 1000;

const CELL: Record<HealthState | "empty", string> = {
  Healthy: "bg-status-ok/70",
  Degraded: "bg-status-warn/80",
  Incident: "bg-status-danger/80",
  empty: "bg-surface-muted",
};

export interface TimelineHour {
  hour: string;
  state: HealthState | "empty";
  errorRate: number | null;
  requests: number | null;
  incidents: GovernanceLogItem[];
}

function classify(value: number | null, signal: HealthSignal | undefined): HealthState {
  if (value === null || !signal) return "Healthy";
  if (value >= signal.incident_at) return "Incident";
  if (value >= signal.degraded_at) return "Degraded";
  return "Healthy";
}

/** Buckets hourly error rates and incidents into timeline cells (pure, tested). */
export function buildTimeline(
  hours: readonly string[],
  errorRates: readonly (number | null)[],
  requests: readonly (number | null)[],
  incidents: readonly GovernanceLogItem[],
  signal: HealthSignal | undefined,
): TimelineHour[] {
  const starts = hours.map((h) => parseServerDate(h).getTime());
  const byHour = new Map<number, GovernanceLogItem[]>();
  for (const item of incidents) {
    const t = parseServerDate(item.created_at).getTime();
    const idx = starts.findIndex((s) => t >= s && t < s + HOUR_MS);
    if (idx >= 0) byHour.set(idx, [...(byHour.get(idx) ?? []), item]);
  }
  return hours.map((hour, i) => {
    const req = requests[i] ?? null;
    const rate = errorRates[i] ?? null;
    const hourIncidents = byHour.get(i) ?? [];
    const base: HealthState | "empty" = req === 0 && hourIncidents.length === 0 ? "empty" : classify(rate, signal);
    const state = hourIncidents.some((x) => x.level === "CRITICAL") && base !== "Incident" ? "Incident" : base;
    return { hour, state, errorRate: rate, requests: req, incidents: hourIncidents };
  });
}

const LEGEND: { state: HealthState | "empty"; label: string }[] = [
  { state: "Healthy", label: "Healthy" },
  { state: "Degraded", label: "Degraded" },
  { state: "Incident", label: "Incident" },
  { state: "empty", label: "No traffic" },
];

interface StatusTimelineProps {
  hours: readonly string[];
  errorRates: readonly (number | null)[];
  requests: readonly (number | null)[];
  incidents: readonly GovernanceLogItem[];
  signal?: HealthSignal;
}

/**
 * 24 h status strip (newplan §15.4 Governance): one cell per hour coloured by
 * the hour's error rate against the health thresholds, with a marker on hours
 * that had CRITICAL / ALERT incidents. A marker links to Logs for that hour.
 */
export function StatusTimeline({ hours, errorRates, requests, incidents, signal }: StatusTimelineProps) {
  const cells = useMemo(
    () => buildTimeline(hours, errorRates, requests, incidents, signal),
    [hours, errorRates, requests, incidents, signal],
  );
  if (cells.length === 0) return null;

  return (
    <div>
      <ol className="flex items-end gap-[3px]" aria-label="Hourly status, last 24 hours">
        {cells.map((cell) => {
          const label = `${formatHour(cell.hour)}: ${cell.state === "empty" ? "no traffic" : cell.state}, error rate ${formatPct(
            cell.errorRate,
          )}${cell.incidents.length ? `, ${cell.incidents.length} incident${cell.incidents.length === 1 ? "" : "s"}` : ""}`;
          return (
            <li key={cell.hour} className="flex min-w-0 flex-1 flex-col items-center gap-1">
              {cell.incidents.length > 0 ? (
                <Link
                  href={logsHref({ level: ["CRITICAL", "ALERT"], at: cell.hour })}
                  aria-label={`${label}. Open logs for this hour`}
                  title={label}
                  data-testid="incident-marker"
                  className={`flex h-4 w-4 items-center justify-center rounded-full bg-status-danger text-[9px] font-bold text-fg-inverse transition-transform duration-150 hover:scale-110 motion-reduce:transition-none ${focusRing}`}
                >
                  {cell.incidents.length > 9 ? "9+" : cell.incidents.length}
                </Link>
              ) : (
                <span className="h-4 w-4" aria-hidden="true" />
              )}
              <span title={label} aria-label={label} role="img" className={`h-8 w-full rounded-sm ${CELL[cell.state]}`} />
            </li>
          );
        })}
      </ol>
      <div className="mt-1.5 flex justify-between text-[10px] text-fg-subtle" aria-hidden="true">
        <span>{formatHour(cells[0].hour)}</span>
        <span>{formatHour(cells[Math.floor(cells.length / 2)].hour)}</span>
        <span>{formatHour(cells[cells.length - 1].hour)}</span>
      </div>
      <p className="mt-0.5 text-center text-[11px] font-medium text-fg-muted" aria-hidden="true">
        Hour (local time)
      </p>
      <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-fg-muted" aria-label="Legend">
        {LEGEND.map((l) => (
          <li key={l.state} className="flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-sm ${CELL[l.state]}`} aria-hidden="true" />
            {l.label}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-status-danger" aria-hidden="true" />
          Incidents (number in the hour)
        </li>
      </ul>
    </div>
  );
}
