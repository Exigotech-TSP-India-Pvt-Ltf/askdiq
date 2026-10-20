"use client";

import Link from "next/link";
import { Activity } from "lucide-react";
import { entityHref } from "@/lib/entity-links";
import { useCan } from "@/lib/use-can";
import { ActionTooltip } from "@/components/ui/ActionTooltip";
import { focusRing } from "@/components/ui/styles";

/**
 * Admin-only "View trace" icon action in an answer's action row.
 * Shown to console roles only (`dashboard:read`, the same guard as
 * the Governance trace API); chat-only users and users without a role never
 * see it. Icon only: the name shows on hover / focus and is the aria-label.
 */
export function TraceLink({ requestId }: { requestId: string | null | undefined }) {
  const allowed = useCan("dashboard:read");
  if (!allowed || !requestId) return null;
  return (
    <ActionTooltip label="View trace" align="end">
      <Link
        href={entityHref({ kind: "trace", id: requestId })}
        aria-label="View trace"
        className={`inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-surface-muted hover:text-accent-fg dark:text-slate-400 dark:hover:bg-white/[0.06] sm:h-7 sm:w-7 ${focusRing}`}
      >
        <Activity className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
      </Link>
    </ActionTooltip>
  );
}
