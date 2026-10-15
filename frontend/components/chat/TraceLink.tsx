"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { entityHref } from "@/lib/entity-links";
import { useCan } from "@/lib/use-can";

/**
 * Admin-only "View trace" link under an answer (newplan §1 Chat row). Shown to
 * console roles only (`dashboard:read`, the same guard as the Governance trace
 * API); chat-only users and users without a role never see it.
 */
export function TraceLink({ requestId }: { requestId: string | null | undefined }) {
  const allowed = useCan("dashboard:read");
  if (!allowed || !requestId) return null;
  return (
    <Link
      href={entityHref({ kind: "trace", id: requestId })}
      className="group inline-flex items-center gap-1 rounded-md text-[11px] font-medium text-fg-subtle transition-colors duration-150 hover:text-accent-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
    >
      View trace
      <ArrowUpRight
        className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none"
        strokeWidth={1.75}
        aria-hidden="true"
      />
    </Link>
  );
}
