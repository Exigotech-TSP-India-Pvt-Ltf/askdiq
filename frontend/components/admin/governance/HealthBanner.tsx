"use client";

import React from "react";
import { m } from "motion/react";
import { AlertOctagon, AlertTriangle, CheckCircle2, type LucideIcon } from "lucide-react";
import { duration, ease } from "@/lib/motion";
import { semanticTokens } from "@/lib/theme-tokens";
import { useResolvedThemeClass } from "@/lib/use-chart-theme";
import type { HealthSignal, HealthState, HealthStatus } from "@/lib/governance-api";
import { formatByUnit, humanize } from "@/lib/governance-format";

const COPY: Record<HealthState, { title: string; body: string; icon: LucideIcon }> = {
  Healthy: {
    title: "All systems healthy",
    body: "Error rate, p95 latency and Azure quota are within their thresholds.",
    icon: CheckCircle2,
  },
  Degraded: {
    title: "Service degraded",
    body: "At least one signal crossed its degraded threshold. Check the signals below.",
    icon: AlertTriangle,
  },
  Incident: {
    title: "Incident in progress",
    body: "A signal crossed its incident threshold. Recent incidents are listed below.",
    icon: AlertOctagon,
  },
};

const TONE_KEYS: Record<HealthState, { bg: "status-ok-bg" | "status-warn-bg" | "status-danger-bg"; line: "status-ok" | "status-warn" | "status-danger" }> = {
  Healthy: { bg: "status-ok-bg", line: "status-ok" },
  Degraded: { bg: "status-warn-bg", line: "status-warn" },
  Incident: { bg: "status-danger-bg", line: "status-danger" },
};

const TEXT: Record<HealthState, string> = {
  Healthy: "text-status-ok-fg",
  Degraded: "text-status-warn-fg",
  Incident: "text-status-danger-fg",
};

const SIGNAL_LABEL: Record<string, string> = {
  error_rate: "Error rate",
  p95_latency: "p95 latency",
  quota_utilization: "Azure quota used",
};

function SignalChip({ name, signal }: { name: string; signal: HealthSignal }) {
  const unit = signal.unit;
  return (
    <li className="rounded-lg border border-line bg-surface-card/70 px-3 py-2">
      <p className="text-[11px] font-medium text-fg-subtle">{SIGNAL_LABEL[name] ?? humanize(name)}</p>
      <p className={`text-sm font-semibold tabular-nums ${TEXT[signal.status] ?? "text-fg"}`}>
        {formatByUnit(signal.value, unit)}
      </p>
      <p className="text-[10px] text-fg-subtle">
        degraded ≥ {formatByUnit(signal.degraded_at, unit)} · incident ≥ {formatByUnit(signal.incident_at, unit)}
      </p>
    </li>
  );
}

/**
 * Overall health banner. The background and border animate
 * between status colours (Motion), so a change from Healthy to Degraded is
 * noticed without a jarring swap. Colours come from the theme tokens.
 */
export function HealthBanner({ health }: { health: HealthStatus }) {
  const theme = useResolvedThemeClass();
  const tokens = semanticTokens[theme];
  const status = (COPY[health.status] ? health.status : "Healthy") as HealthState;
  const { title, body, icon: Icon } = COPY[status];
  const tone = TONE_KEYS[status];

  return (
    <m.section
      role="status"
      aria-live="polite"
      aria-label={`System health: ${status}`}
      data-health={status}
      initial={false}
      animate={{ backgroundColor: tokens[tone.bg], borderColor: tokens[tone.line] }}
      transition={{ duration: duration.slow, ease }}
      className="rounded-2xl border p-5"
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-start gap-3">
          <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${TEXT[status]}`} strokeWidth={1.75} aria-hidden="true" />
          <div>
            <p className={`text-base font-semibold ${TEXT[status]}`}>{title}</p>
            <p className="mt-0.5 text-xs text-fg-muted">
              {body} Window: last {health.window_minutes} min.
            </p>
          </div>
        </div>
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-3" aria-label="Health signals">
          {Object.entries(health.signals).map(([name, signal]) => (
            <SignalChip key={name} name={name} signal={signal} />
          ))}
        </ul>
      </div>
    </m.section>
  );
}
