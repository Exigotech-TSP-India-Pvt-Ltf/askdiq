"use client";

import React from "react";
import { AnimatePresence, m } from "motion/react";
import { expand, usePrefersReducedMotion } from "@/lib/motion";

/**
 * Height expand / collapse (row details, stage breakdowns) with the `expand`
 * preset. Reduced motion: content appears and disappears instantly.
 */
export function Collapse({
  open,
  children,
  className = "",
  id,
}: {
  open: boolean;
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  const reduced = usePrefersReducedMotion();
  if (reduced) {
    return open ? (
      <div id={id} className={className} data-testid="collapse">
        {children}
      </div>
    ) : null;
  }
  return (
    <AnimatePresence initial={false}>
      {open ? (
        <m.div
          key="collapse"
          id={id}
          variants={expand}
          initial="hidden"
          animate="visible"
          exit="exit"
          className={`overflow-hidden ${className}`}
          data-testid="collapse"
        >
          {children}
        </m.div>
      ) : null}
    </AnimatePresence>
  );
}
