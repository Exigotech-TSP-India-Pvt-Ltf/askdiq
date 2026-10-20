"use client";

import React, { useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, Copy } from "lucide-react";
import { pop } from "@/lib/motion";
import { iconButton } from "@/components/ui/styles";

/** How long the "Copied" tooltip stays up. */
export const COPIED_RESET_MS = 1500;

interface CopyButtonProps {
  value: string;
  /** Accessible label, e.g. "Copy trace id". */
  label?: string;
  className?: string;
}

/** Copy-to-clipboard icon button with a "Copied" tooltip. */
export function CopyButton({ value, label = "Copy", className = "" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      return; // clipboard blocked (insecure context or permission denied)
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
  }

  return (
    <span className={`relative inline-flex ${className}`}>
      <button
        type="button"
        onClick={copy}
        aria-label={label}
        title={label}
        className={`${iconButton} h-6 w-6 rounded-md`}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-status-ok" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        )}
      </button>
      <AnimatePresence>
        {copied ? (
          <m.span
            key="copied"
            role="status"
            variants={pop}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-surface-inverse px-2 py-0.5 text-[11px] font-medium text-fg-inverse shadow-md"
          >
            Copied
          </m.span>
        ) : null}
      </AnimatePresence>
    </span>
  );
}
