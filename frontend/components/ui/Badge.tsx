import React, { type HTMLAttributes } from "react";

export type BadgeTone = "brand" | "info" | "neutral";

/** Tone classes (light + dark) shared by role chips and other small pills. */
export const BADGE_TONE: Record<BadgeTone, string> = {
  brand: "border-brand-500/30 bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300",
  info: "border-status-info/30 bg-status-info-bg text-status-info-fg",
  neutral: "border-line bg-surface-muted text-fg-muted",
};

const BASE = "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider";

/** Full class list of a badge with the given tone. */
export function badgeTone(tone: BadgeTone): string {
  return `${BASE} ${BADGE_TONE[tone]}`;
}

/** Small uppercase text pill (the visible text is the accessible text). */
export function Badge({
  tone = "neutral",
  className,
  children,
  ...rest
}: { tone?: BadgeTone } & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span {...rest} className={className ? `${badgeTone(tone)} ${className}` : badgeTone(tone)}>
      {children}
    </span>
  );
}
