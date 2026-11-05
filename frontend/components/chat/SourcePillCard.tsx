"use client";

import type { Citation } from "@/lib/types";

/** First-party DeployIQ icon shown on citations (never a third-party favicon). */
export const CITATION_ICON_SRC = "/DEPLOYIQ_Icon_Master.svg";

/** Decorative citation icon; the pill / card text carries the source name. */
export function CitationIcon({ className }: { className: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- small static SVG: next/image adds nothing here.
    <img
      src={CITATION_ICON_SRC}
      alt=""
      aria-hidden="true"
      data-citation-icon
      draggable={false}
      className={`shrink-0 rounded-full object-contain ${className}`}
    />
  );
}

export interface SourcePillCardProps {
  /** Referenced by the pill's `aria-describedby`. */
  id: string;
  open: boolean;
  citation: Citation;
}

/**
 * Hover / focus card of a source pill: the DeployIQ icon (no third-party
 * favicon is ever loaded), the page title on one line and the host.
 * Non-interactive; it sits inside the pill's hover area so moving the pointer
 * onto it keeps it open (WCAG 1.4.13). Always mounted so the description is
 * available while it is closed.
 */
export function SourcePillCard({ id, open, citation }: SourcePillCardProps) {
  return (
    <span
      id={id}
      role="tooltip"
      hidden={!open}
      data-testid="source-pill-card"
      className="absolute bottom-full left-0 z-30 w-64 max-w-[calc(100vw-2rem)] pb-1.5 text-left"
    >
      <span className="flex items-center gap-2.5 rounded-xl border border-line bg-surface-elevated px-3 py-2 shadow-lg dark:border-white/10">
        <CitationIcon className="h-6 w-6" />
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-xs font-medium text-fg" title={citation.title}>
            {citation.title}
          </span>
          <span className="truncate text-[11px] text-fg-subtle">{citation.host ?? "No link for this source"}</span>
        </span>
      </span>
    </span>
  );
}
