"use client";

import { useEffect, useRef, useState } from "react";
import { animate } from "motion/react";
import { countUp, usePrefersReducedMotion } from "@/lib/motion";

export interface CountUpProps {
  /** `null` renders `fallback` (no data yet). */
  value: number | null;
  format: (value: number) => string;
  fallback?: string;
  className?: string;
}

/**
 * Animates a number from its previous value (0 on mount) to `value` with the
 * `countUp` preset. The final value is always in the accessible text, so
 * screen readers and tests never read an intermediate number; reduced motion
 * shows the final value at once.
 */
export function CountUp({ value, format, fallback = "—", className = "" }: CountUpProps) {
  const reduced = usePrefersReducedMotion();
  const from = useRef(0);
  const [shown, setShown] = useState(reduced ? (value ?? 0) : 0);

  useEffect(() => {
    if (value === null) return undefined;
    if (reduced) {
      from.current = value;
      setShown(value);
      return undefined;
    }
    const controls = animate(from.current, value, { ...countUp, onUpdate: (v) => setShown(v) });
    from.current = value;
    return () => controls.stop();
  }, [reduced, value]);

  if (value === null) return <span className={className}>{fallback}</span>;
  return (
    <span className={className}>
      <span aria-hidden="true">{format(shown)}</span>
      <span className="sr-only" data-testid="count-final">
        {format(value)}
      </span>
    </span>
  );
}
