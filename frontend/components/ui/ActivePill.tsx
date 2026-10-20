"use client";

import React from "react";
import { m } from "motion/react";
import { layoutSpring } from "@/lib/motion";

/**
 * The brand-gradient pill behind the active tab. Shared `layoutId` makes it
 * slide between tabs instead of jumping; the button content must
 * sit above it (`relative`).
 */
export function ActivePill({ layoutId }: { layoutId: string }) {
  return (
    <m.span
      layoutId={layoutId}
      transition={layoutSpring}
      aria-hidden="true"
      data-testid="tab-active-pill"
      className="absolute inset-0 rounded-lg bg-brand-gradient shadow-brand-glow"
    />
  );
}
