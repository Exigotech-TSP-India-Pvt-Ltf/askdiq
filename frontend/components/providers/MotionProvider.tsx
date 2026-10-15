"use client";

import type { ReactNode } from "react";
import { LazyMotion, MotionConfig } from "motion/react";

/** Loads the feature bundle after hydration (keeps it out of the route JS). */
const loadFeatures = () => import("@/lib/motion-features").then((mod) => mod.default);

/**
 * App-wide Motion setup: `LazyMotion` with lazily loaded features keeps the
 * bundle small (only `m.*` components are allowed; `strict` throws on
 * `motion.*`), and `reducedMotion="user"` honours the OS "reduce motion"
 * setting everywhere.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
