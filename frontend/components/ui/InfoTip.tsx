"use client";

import { useId, useState, type ReactNode } from "react";
import { Info } from "lucide-react";

export interface InfoTipProps {
  /** What the tip explains, e.g. "Active visitors" (button name: "About Active visitors"). */
  label: string;
  /** The explanation (also the button's accessible description). */
  children: ReactNode;
  /** Horizontal anchor of the bubble (default: right edge, so it stays inside cards). */
  align?: "left" | "right";
  className?: string;
}

/**
 * Small "i" button with a definition bubble. Opens on hover, keyboard focus or
 * tap; Escape closes it. Never place it inside a link (interactive nesting).
 */
export function InfoTip({ label, children, align = "right", className = "" }: InfoTipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className={`relative inline-flex ${className}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`About ${label}`}
        aria-describedby={id}
        aria-expanded={open}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        className="rounded-full p-0.5 text-fg-subtle transition-colors hover:text-accent-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <Info className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      </button>
      <span
        id={id}
        role="tooltip"
        data-state={open ? "open" : "closed"}
        className={`absolute top-full z-30 mt-1.5 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface-card p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-fg shadow-lg transition-opacity duration-100 motion-reduce:transition-none ${
          align === "right" ? "right-0" : "left-0"
        } ${open ? "visible opacity-100" : "invisible opacity-0"}`}
      >
        {children}
      </span>
    </span>
  );
}
