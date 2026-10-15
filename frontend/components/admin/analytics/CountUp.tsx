"use client";

import { useEffect, useRef, useState } from "react";
import { animate } from "motion/react";
import { countUp, usePrefersReducedMotion } from "@/lib/motion";

export interface CountUpProps {
  value: number | null;
  format: (value: number) => string;
  /** Shown when `value` is null. */
  fallback?: string;
}

/**
 * Number that counts from its previous value to the new one (`countUp`
 * preset). Screen readers get the final value only; reduced motion jumps.
 */
export function CountUp({ value, format, fallback = "n/a" }: CountUpProps) {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState<number>(value ?? 0);
  const from = useRef<number>(0);

  useEffect(() => {
    if (value === null) return undefined;
    if (reduced) {
      setShown(value);
      from.current = value;
      return undefined;
    }
    const controls = animate(from.current, value, {
      ...countUp,
      onUpdate: (v) => {
        from.current = v;
        setShown(v);
      },
      onComplete: () => {
        from.current = value;
        setShown(value);
      },
    });
    return () => controls.stop();
  }, [value, reduced]);

  if (value === null) return <span>{fallback}</span>;
  return (
    <>
      <span aria-hidden="true">{format(shown)}</span>
      <span className="sr-only" data-testid="count-final">
        {format(value)}
      </span>
    </>
  );
}
