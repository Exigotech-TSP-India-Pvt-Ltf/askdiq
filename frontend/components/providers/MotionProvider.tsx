"use client";

import type { ReactNode } from "react";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

/**
 * App-wide Motion setup: `LazyMotion` + `domAnimation` keeps the bundle small
 * (only `m.*` components are allowed; `strict` throws on `motion.*`), and
 * `reducedMotion="user"` honours the OS "reduce motion" setting everywhere.
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
