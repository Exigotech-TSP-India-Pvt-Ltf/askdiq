"use client";

import { Link2 } from "lucide-react";
import type { Citation } from "@/lib/types";

/** First letter or digit of the label, upper-cased (`null`: no usable character). */
export function monogram(label: string): string | null {
  for (const ch of label) {
    // A cased letter (any script with case) or an ASCII digit.
    if (ch.toLowerCase() !== ch.toUpperCase() || (ch >= "0" && ch <= "9")) return ch.toUpperCase();
  }
  return null;
}

export interface SourcePillCardProps {
  /** Referenced by the pill's `aria-describedby`. */
  id: string;
  open: boolean;
  citation: Citation;
}

/**
 * Hover / focus card of a source pill: a monogram of the source label (no
 * third-party favicon is ever loaded), the page title on one line and the
 * host. Non-interactive; it sits inside the pill's hover area so moving the
 * pointer onto it keeps it open (WCAG 1.4.13). Always mounted so the
 * description is available while it is closed.
 */
export function SourcePillCard({ id, open, citation }: SourcePillCardProps) {
  const letter = monogram(citation.label);
  return (
    <span
      id={id}
      role="tooltip"
      hidden={!open}
      data-testid="source-pill-card"
      className="absolute bottom-full left-0 z-30 w-64 max-w-[calc(100vw-2rem)] pb-1.5 text-left"
    >
      <span className="flex items-center gap-2.5 rounded-xl border border-line bg-surface-elevated px-3 py-2 shadow-lg dark:border-white/10">
        <span
          aria-hidden="true"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"
        >
          {letter ?? <Link2 className="h-3.5 w-3.5" strokeWidth={2} />}
        </span>
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
