"use client";

import React from "react";
import type { HealthState, LogLevel, TraceStatus } from "@/lib/governance-api";
import type { StreamStatus } from "@/lib/use-governance-stream";
import { humanize } from "@/lib/governance-format";

const PILL = "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold";

const DANGER = "border-status-danger/40 bg-status-danger-bg text-status-danger-fg";
const WARN = "border-status-warn/40 bg-status-warn-bg text-status-warn-fg";
const OK = "border-status-ok/40 bg-status-ok-bg text-status-ok-fg";
const INFO = "border-status-info/40 bg-status-info-bg text-status-info-fg";
const NEUTRAL = "border-line bg-surface-muted text-fg-muted";

export const LEVEL_TONE: Record<LogLevel, string> = {
  CRITICAL: DANGER,
  ALERT: WARN,
  ERROR: DANGER,
  WARNING: WARN,
  INFO: INFO,
};

export function LevelBadge({ level }: { level: LogLevel }) {
  return <span className={`${PILL} ${LEVEL_TONE[level] ?? NEUTRAL}`}>{level}</span>;
}

const STATUS_TONE: Record<TraceStatus, string> = {
  ok: OK,
  ungrounded: WARN,
  blocked: WARN,
  failed: DANGER,
  unknown: NEUTRAL,
};

export function TraceStatusBadge({ status }: { status: TraceStatus }) {
  return <span className={`${PILL} ${STATUS_TONE[status] ?? NEUTRAL}`}>{humanize(status)}</span>;
}

export const HEALTH_TONE: Record<HealthState, string> = { Healthy: OK, Degraded: WARN, Incident: DANGER };

export function HealthPill({ status }: { status: HealthState }) {
  return <span className={`${PILL} ${HEALTH_TONE[status] ?? NEUTRAL}`}>{status}</span>;
}

const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  paused: "Paused (tab hidden)",
  unsupported: "Live updates unavailable",
};

const STREAM_DOT: Record<StreamStatus, string> = {
  connecting: "bg-status-warn",
  live: "bg-status-ok",
  reconnecting: "bg-status-warn",
  paused: "bg-fg-subtle",
  unsupported: "bg-fg-subtle",
};

/** Small "Live / Reconnecting / Paused" chip for SSE-driven views. */
export function LiveIndicator({ status, updatedAt }: { status: StreamStatus; updatedAt?: number }) {
  return (
    <span
      role="status"
      aria-live="polite"
      data-stream-status={status}
      className="inline-flex items-center gap-2 rounded-full border border-line bg-surface-card px-3 py-1 text-[11px] font-medium text-fg-muted"
    >
      <span className="relative flex h-2 w-2" aria-hidden="true">
        {status === "live" ? (
          <span className="absolute inline-flex h-full w-full rounded-full bg-status-ok opacity-60 motion-safe:animate-ping" />
        ) : null}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${STREAM_DOT[status]}`} />
      </span>
      {STREAM_LABEL[status]}
      {updatedAt ? (
        <span className="text-fg-subtle">· {new Date(updatedAt).toLocaleTimeString("en-AU")}</span>
      ) : null}
    </span>
  );
}
