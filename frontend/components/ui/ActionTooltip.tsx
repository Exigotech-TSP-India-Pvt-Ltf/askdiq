"use client";

import type { ReactNode } from "react";

export interface ActionTooltipProps {
  /** Visible name of the wrapped icon-only control (it keeps its own aria-label). */
  label: string;
  children: ReactNode;
  /** Bubble anchor: centred on the control, or its right edge (for controls near the right edge). */
  align?: "center" | "end";
  className?: string;
}

/**
 * Name bubble for icon-only actions: shows on hover and on keyboard focus
 * (a tap focuses the control on touch screens, which shows it too). It is
 * visual only (`aria-hidden`): screen readers already get the control's
 * aria-label, so announcing the bubble would read the name twice.
 */
export function ActionTooltip({ label, children, align = "center", className = "" }: ActionTooltipProps) {
  return (
    <span className={`group/tip relative inline-flex ${className}`}>
      {children}
      <span
        aria-hidden="true"
        data-tooltip
        className={`pointer-events-none absolute bottom-full z-30 mb-1.5 whitespace-nowrap rounded-md bg-surface-inverse px-2 py-0.5 text-[11px] font-medium text-fg-inverse opacity-0 shadow-md transition-opacity duration-100 group-focus-within/tip:opacity-100 group-hover/tip:opacity-100 motion-reduce:transition-none ${
          align === "end" ? "right-0" : "left-1/2 -translate-x-1/2"
        }`}
      >
        {label}
      </span>
    </span>
  );
}
