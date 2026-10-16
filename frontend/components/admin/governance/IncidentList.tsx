"use client";

import React from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import type { GovernanceLogItem } from "@/lib/governance-api";
import { hourStart } from "@/lib/governance-format";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { logsHref } from "@/lib/governance-links";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { EmptyState } from "@/components/ui/EmptyState";
import { focusRing } from "@/components/ui/styles";
import { LevelBadge } from "./badges";

export function incidentLogsHref(item: GovernanceLogItem): string {
  return logsHref({
    level: [item.level],
    q: item.request_id ?? item.action,
    at: hourStart(item.created_at).toISOString(),
  });
}

/**
 * Recent CRITICAL / ALERT rows (newplan §4.2). Rows that arrive over SSE are
 * in `freshIds` and highlight briefly; each row links to Logs pre-filtered.
 */
export function IncidentList({
  items,
  freshIds,
}: {
  items: readonly GovernanceLogItem[];
  freshIds: ReadonlySet<string>;
}) {
  const variants = useMotionVariants(fadeUp);

  if (items.length === 0) {
    return (
      <EmptyState
        compact
        icon={ShieldCheck}
        title="No incidents"
        description="No CRITICAL or ALERT events in the incident window."
      />
    );
  }

  return (
    <ul className="divide-y divide-line" aria-label="Recent incidents">
      <AnimatePresence initial={false}>
        {items.map((item) => {
          const fresh = freshIds.has(item.id);
          return (
            <m.li
              key={item.id}
              layout="position"
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              data-fresh={fresh || undefined}
              className={`rounded-lg transition-colors duration-700 motion-reduce:transition-none ${
                fresh ? "bg-status-warn-bg" : "bg-transparent"
              }`}
            >
              <Link
                href={incidentLogsHref(item)}
                className={`group flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-surface-muted ${focusRing}`}
              >
                <LevelBadge level={item.level} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-fg">{item.summary}</span>
                  <span className="block truncate text-[11px] text-fg-subtle">
                    {item.service} · {item.action} · <RelativeTime value={item.created_at} />
                  </span>
                </span>
                <span className="sr-only">Open in Logs</span>
                <ArrowUpRight
                  className="h-4 w-4 shrink-0 text-fg-subtle transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-fg motion-reduce:transition-none"
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </Link>
            </m.li>
          );
        })}
      </AnimatePresence>
    </ul>
  );
}
