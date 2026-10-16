"use client";

import React, { useEffect, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { AskDiqMascot } from "@/components/askdiq/AskDiqMascot";
import {
  loaderBob,
  loaderDot,
  loaderOrbit,
  loaderPresence,
  loaderRing,
  loaderScan,
  loaderShadow,
  loaderShowDelayMs,
  loaderStatus,
  loaderStatusRotateMs,
  routeProgress,
  useMotionVariants,
  usePrefersReducedMotion,
} from "@/lib/motion";

export const LOADER_MESSAGES = ["Fetching your insights…", "Crunching the numbers…", "Almost there…"] as const;

export type MascotLoaderVariant = "page" | "inline";

interface MascotLoaderProps {
  /** `page`: centred, full content height. `inline`: compact, for a card or panel. */
  variant?: MascotLoaderVariant;
  /** While true the loader is mounted; set false to fade it out the instant data arrives. */
  show?: boolean;
  /** Accessible name of the status region. */
  label?: string;
  /** Visual appears only after this (ms) so fast loads never flash it. */
  delayMs?: number;
  /** Status lines, rotated while waiting. */
  messages?: readonly string[];
  className?: string;
}

/** True once `active` has stayed true for `delayMs`; false again as soon as it drops. */
export function useDelayedFlag(active: boolean, delayMs: number): boolean {
  const [ready, setReady] = useState(active && delayMs <= 0);
  useEffect(() => {
    if (!active) {
      setReady(false);
      return undefined;
    }
    if (delayMs <= 0) {
      setReady(true);
      return undefined;
    }
    const t = window.setTimeout(() => setReady(true), delayMs);
    return () => window.clearTimeout(t);
  }, [active, delayMs]);
  return ready;
}

function useRotatingIndex(count: number, running: boolean): number {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!running || count < 2) return undefined;
    const t = window.setInterval(() => setIndex((i) => (i + 1) % count), loaderStatusRotateMs);
    return () => window.clearInterval(t);
  }, [count, running]);
  return index;
}

const SIZES: Record<MascotLoaderVariant, { mascot: number; stage: string; orbit: string; text: string; wrap: string }> = {
  page: {
    mascot: 112,
    stage: "h-44 w-44",
    orbit: "inset-0",
    text: "text-sm",
    wrap: "flex h-full min-h-[60vh] w-full flex-col items-center justify-center gap-4 p-8",
  },
  inline: {
    mascot: 56,
    stage: "h-24 w-24",
    orbit: "inset-0",
    text: "text-xs",
    wrap: "flex w-full flex-col items-center justify-center gap-2 px-4 py-8",
  },
};

const ORBIT_DOTS = [0, 120, 240] as const;

function MascotStage({ variant, reduced }: { variant: MascotLoaderVariant; reduced: boolean }) {
  const size = SIZES[variant];
  const loop = <T,>(preset: T): T | undefined => (reduced ? undefined : preset);
  return (
    <div className={`relative flex items-center justify-center ${size.stage}`} aria-hidden="true">
      {/* Soft pulse ring */}
      <m.span
        className="absolute inset-[18%] rounded-full border-2 border-brand-400/50 bg-brand-400/10"
        animate={loop(loaderRing)?.animate}
        transition={loop(loaderRing)?.transition}
      />
      {/* Orbiting dots */}
      <m.span
        data-testid="mascot-loader-orbit"
        className={`absolute ${size.orbit}`}
        animate={loop(loaderOrbit)?.animate}
        transition={loop(loaderOrbit)?.transition}
      >
        {ORBIT_DOTS.map((deg, i) => (
          <span key={deg} className="absolute inset-0" style={{ transform: `rotate(${deg}deg)` }}>
            <span
              className={`absolute left-1/2 top-0 -translate-x-1/2 rounded-full bg-brand-500 ${i === 0 ? "h-2 w-2" : "h-1.5 w-1.5 opacity-60"}`}
            />
          </span>
        ))}
      </m.span>
      {/* Breathing ground shadow */}
      <m.span
        className="absolute bottom-[8%] left-1/2 h-2 w-1/3 -translate-x-1/2 rounded-full bg-fg/20 blur-sm"
        animate={loop(loaderShadow)?.animate}
        transition={loop(loaderShadow)?.transition}
      />
      {/* Mascot with a scan line across the visor */}
      <m.span
        data-testid="mascot-loader-mascot"
        data-motion={reduced ? "static" : "animated"}
        className="relative flex"
        animate={loop(loaderBob)?.animate}
        transition={loop(loaderBob)?.transition}
      >
        <AskDiqMascot size={size.mascot} priority />
        {reduced ? null : (
          <span className="pointer-events-none absolute left-[24%] right-[24%] top-[30%] h-[26%] overflow-hidden rounded-full">
            <m.span
              className="absolute inset-x-0 top-1/2 h-0.5 rounded-full bg-brand-200 shadow-brand-glow"
              animate={loaderScan.animate}
              transition={loaderScan.transition}
            />
          </span>
        )}
      </m.span>
    </div>
  );
}

/**
 * AskDIQ mascot loader for "waiting for data" moments. Mount it while loading
 * (or pass `show`); the visual only appears after `delayMs`, so fast loads
 * never flash it, and fades out the moment `show` turns false.
 */
export function MascotLoader({
  variant = "page",
  show = true,
  label = "Loading",
  delayMs = loaderShowDelayMs,
  messages = LOADER_MESSAGES,
  className = "",
}: MascotLoaderProps) {
  const visible = useDelayedFlag(show, delayMs);
  const reduced = usePrefersReducedMotion();
  const presence = useMotionVariants(loaderPresence);
  const statusVariants = useMotionVariants(loaderStatus);
  const index = useRotatingIndex(messages.length, visible);
  const size = SIZES[variant];
  const message = messages[index] ?? label;

  return (
    <AnimatePresence initial={false}>
      {show ? (
        <m.div
          key="mascot-loader"
          role="status"
          aria-live="polite"
          aria-busy="true"
          aria-label={label}
          data-variant={variant}
          data-testid="mascot-loader"
          className={`${size.wrap} ${className}`}
          variants={presence}
          exit="exit"
        >
          <span className="sr-only">{label}</span>
          <AnimatePresence>
            {visible ? (
              <m.div
                key="visual"
                data-testid="mascot-loader-visual"
                className="flex flex-col items-center gap-3"
                variants={presence}
                initial="hidden"
                animate="visible"
                exit="exit"
              >
                <MascotStage variant={variant} reduced={reduced} />
                <div className={`flex h-5 items-center gap-2 font-medium text-fg-muted ${size.text}`}>
                  <AnimatePresence mode="wait" initial={false}>
                    <m.span key={message} variants={statusVariants} initial="hidden" animate="visible" exit="exit">
                      {message}
                    </m.span>
                  </AnimatePresence>
                  <span className="flex gap-1" aria-hidden="true">
                    {[0, 1, 2].map((i) => (
                      <m.span
                        key={i}
                        className="h-1 w-1 rounded-full bg-brand-500"
                        animate={reduced ? undefined : loaderDot(i).animate}
                        transition={reduced ? undefined : loaderDot(i).transition}
                      />
                    ))}
                  </span>
                </div>
              </m.div>
            ) : null}
          </AnimatePresence>
        </m.div>
      ) : null}
    </AnimatePresence>
  );
}

/** Thin teal bar across the top of the content area during route transitions. */
export function RouteProgressBar() {
  const reduced = usePrefersReducedMotion();
  return (
    <div className="pointer-events-none absolute inset-x-0 top-0 z-30 h-0.5 overflow-hidden" aria-hidden="true">
      <m.div
        data-testid="route-progress"
        className="h-full origin-left bg-brand-500 shadow-brand-glow"
        initial={reduced ? false : routeProgress.initial}
        animate={reduced ? { scaleX: 0.6 } : routeProgress.animate}
        transition={reduced ? undefined : routeProgress.transition}
      />
    </div>
  );
}
