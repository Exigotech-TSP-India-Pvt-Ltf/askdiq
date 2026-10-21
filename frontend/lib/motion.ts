/**
 * Motion presets. Pages and components import these and
 * never hard-code timings; `tests/lib/motion.test.tsx` greps for violations.
 * Everything stays under 300 ms except `slow` (320 ms, drawers / count-up).
 * `MotionConfig reducedMotion="user"` (components/providers/MotionProvider)
 * removes transforms for users who ask for reduced motion; `reduceVariants`
 * does the same for variants applied outside Motion components.
 */
import { useMemo, useSyncExternalStore } from "react";
import type { TargetAndTransition, Transition, Variant, Variants } from "motion/react";

/** Durations in seconds (Motion's unit). */
export const duration = {
  fast: 0.12,
  hover: 0.15,
  base: 0.2,
  slow: 0.32,
} as const;

/** Same durations in milliseconds, for CSS / timers. */
export const durationMs = {
  fast: 120,
  hover: 150,
  base: 200,
  slow: 320,
} as const;

/** Standard ease-out curve. */
export const ease = [0.22, 1, 0.36, 1] as const;

export const spring: Transition = { type: "spring", stiffness: 420, damping: 34, mass: 0.8 };

const tween = (d: number): Transition => ({ duration: d, ease });

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: tween(duration.base) },
  exit: { opacity: 0, y: -4, transition: tween(duration.fast) },
};

export const stagger: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.04, delayChildren: 0.02 } },
};

export const drawer: Variants = {
  hidden: { x: "100%" },
  visible: { x: 0, transition: spring },
  exit: { x: "100%", transition: tween(duration.base) },
};

/** Off-canvas navigation drawer entering from the left (mobile sidebar). */
export const drawerLeft: Variants = {
  hidden: { x: "-100%" },
  visible: { x: 0, transition: spring },
  exit: { x: "-100%", transition: tween(duration.base) },
};

/** Chat composer moving from the centred new-chat stack to the bottom (shared `layoutId`). */
export const composerMove: Transition = tween(duration.base);

/** Shared-layout transition for `layoutId` indicators (active nav bar, tab pills). */
export const layoutSpring: Transition = spring;

export const toast: Variants = {
  hidden: { opacity: 0, y: 12, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, y: 8, scale: 0.98, transition: tween(duration.fast) },
};

export const pop: Variants = {
  hidden: { opacity: 0, scale: 0.9 },
  visible: { opacity: 1, scale: 1, transition: spring },
  exit: { opacity: 0, scale: 0.9, transition: tween(duration.fast) },
};

/** Card hover: lift 2 px over 150 ms. */
export const cardHover: Variants = {
  rest: { y: 0, transition: tween(duration.hover) },
  hover: { y: -2, transition: tween(duration.hover) },
};

export const crossfade: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: tween(duration.base) },
  exit: { opacity: 0, transition: tween(duration.fast) },
};

/** Popovers and menus (filters, column picker): small drop from the trigger. */
export const popover: Variants = {
  hidden: { opacity: 0, y: -4, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: tween(duration.fast) },
  exit: { opacity: 0, y: -4, scale: 0.98, transition: tween(duration.fast) },
};

/**
 * Expand / collapse (row detail, stage breakdowns). Animates height, so
 * `Collapse` (components/ui/Collapse) renders it instantly under reduced motion.
 */
export const expand: Variants = {
  hidden: { height: 0, opacity: 0, transition: tween(duration.fast) },
  visible: { height: "auto", opacity: 1, transition: tween(duration.base) },
  exit: { height: 0, opacity: 0, transition: tween(duration.fast) },
};

/** Studio card entrance: fade + 8 px rise (children of a `stagger` grid). */
export const cardEnter: Variants = fadeUp;

/** Lists longer than this render without per-row animation (performance). */
export const MAX_ANIMATED_ROWS = 50;

/** `stagger` for short lists; no stagger for long ones. */
export function staggerFor(count: number): Variants {
  return count > MAX_ANIMATED_ROWS ? {} : stagger;
}

/**
 * Ambient decoration loops (login brand panel): very slow,
 * transform-only drift between 20 and 40 s. Not UI feedback, so the 300 ms
 * cap does not apply; callers render them static under reduced motion.
 */
export const ambientLoopSeconds = { short: 22, medium: 30, long: 38 } as const;

export function ambientLoop(loop: keyof typeof ambientLoopSeconds): Transition {
  return { duration: ambientLoopSeconds[loop], ease: "easeInOut", repeat: Infinity, repeatType: "mirror" };
}

/** Pointer parallax smoothing for decoration layers (`useSpring`). */
export const parallaxSpring = { stiffness: 60, damping: 20, mass: 0.6 } as const;

/** Sign-in step indicator: the progress rail fills over `slow`. */
export const progressFill: Transition = { duration: duration.slow, ease };

/** No animation: used under reduced motion where a Transition is still required. */
export const instant: Transition = { duration: 0 };

/** Guided tour: scrim fades over `base`; the step progress bar fills over 0.3s. */
export const tourScrimFade: Transition = { duration: duration.base, ease };
export const tourProgress: Transition = { duration: 0.3, ease };

/** Transition for number count-ups (`animate(from, to, countUp)`). */
export const countUp: Transition = { duration: duration.slow, ease };

const TRANSFORM_KEYS = new Set([
  "x",
  "y",
  "z",
  "scale",
  "scaleX",
  "scaleY",
  "rotate",
  "rotateX",
  "rotateY",
  "skewX",
  "skewY",
  "translateX",
  "translateY",
  "transform",
]);

/** Strips transform properties so reduced-motion users get opacity-only changes. */
export function reduceVariants(variants: Variants): Variants {
  const out: Variants = {};
  for (const [name, variant] of Object.entries(variants)) {
    if (typeof variant === "function") {
      out[name] = variant;
      continue;
    }
    const kept: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(variant as Record<string, unknown>)) {
      if (!TRANSFORM_KEYS.has(key)) kept[key] = value;
    }
    out[name] = kept as Variant;
  }
  return out;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function reducedMotionQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(REDUCED_MOTION_QUERY);
}

function subscribeReducedMotion(onChange: () => void): () => void {
  const mq = reducedMotionQuery();
  mq?.addEventListener?.("change", onChange);
  return () => mq?.removeEventListener?.("change", onChange);
}

/** Live OS "reduce motion" preference (false on the server). */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => reducedMotionQuery()?.matches ?? false,
    () => false,
  );
}

/** Returns the preset, minus transforms when the user prefers reduced motion. */
export function useMotionVariants(variants: Variants): Variants {
  const reduced = usePrefersReducedMotion();
  return useMemo(() => (reduced ? reduceVariants(variants) : variants), [reduced, variants]);
}

/* ------------------------------------------------------------------------ */
/* AskDIQ launcher (components/askdiq). Idle loops are decoration, not UI     */
/* feedback, so the 300 ms cap does not apply; callers render them static    */
/* when the user prefers reduced motion.                                      */
/* ------------------------------------------------------------------------ */

/** Mascot "breathing": a slow 4 px float with a hint of scale. */
export const mascotFloat: { animate: TargetAndTransition; transition: Transition } = {
  animate: { y: [0, -4, 0], scale: [1, 1.02, 1] },
  transition: { duration: 3.6, ease: "easeInOut", repeat: Infinity },
};

/** Soft brand pulse ring behind the launcher. */
export const pulseRing: { animate: TargetAndTransition; transition: Transition } = {
  animate: { scale: [1, 1.45], opacity: [0.45, 0] },
  transition: { duration: 2.4, ease: "easeOut", repeat: Infinity, repeatDelay: 0.6 },
};

/** Hover tilt / press for the launcher. */
export const mascotHover: TargetAndTransition = { rotate: -8, scale: 1.06, transition: spring };
export const mascotTap: TargetAndTransition = { scale: 0.94, transition: spring };

/** Greeting bubble: pops in from the launcher corner. */
export const greeting: Variants = {
  hidden: { opacity: 0, y: 8, scale: 0.92 },
  visible: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, y: 4, scale: 0.96, transition: tween(duration.fast) },
};

/** Delay before the once-per-session greeting appears (ms). */
export const greetingDelayMs = 1200;

/* ------------------------------------------------------------------------ */
/* Mascot loader (components/ui/MascotLoader). Idle loops are decoration;    */
/* callers render them static when the user prefers reduced motion.         */
/* ------------------------------------------------------------------------ */

/** Loads faster than this never show the mascot (ms). */
export const loaderShowDelayMs = 300;

/** How often the friendly status line changes (ms). */
export const loaderStatusRotateMs = 2400;

const loaderBobSeconds = 2.8;

/** Gentle float / bob of the mascot. */
export const loaderBob: { animate: TargetAndTransition; transition: Transition } = {
  animate: { y: [0, -8, 0], rotate: [0, -2, 0, 2, 0] },
  transition: { duration: loaderBobSeconds, ease: "easeInOut", repeat: Infinity },
};

/** Ground shadow that breathes in time with the bob (smaller when the mascot is high). */
export const loaderShadow: { animate: TargetAndTransition; transition: Transition } = {
  animate: { scaleX: [1, 0.72, 1], opacity: [0.5, 0.25, 0.5] },
  transition: { duration: loaderBobSeconds, ease: "easeInOut", repeat: Infinity },
};

/** Soft pulse ring behind the mascot. */
export const loaderRing: { animate: TargetAndTransition; transition: Transition } = {
  animate: { scale: [0.85, 1.35], opacity: [0.5, 0] },
  transition: { duration: 2, ease: "easeOut", repeat: Infinity, repeatDelay: 0.3 },
};

/** Orbiting dots around the mascot: one full turn. */
export const loaderOrbit: { animate: TargetAndTransition; transition: Transition } = {
  animate: { rotate: 360 },
  transition: { duration: 4.8, ease: "linear", repeat: Infinity },
};

/** "Thinking" scan line sweeping across the visor. */
export const loaderScan: { animate: TargetAndTransition; transition: Transition } = {
  animate: { y: [-10, 10], opacity: [0, 0.9, 0] },
  transition: { duration: 1.6, ease: "easeInOut", repeat: Infinity, repeatDelay: 0.5 },
};

/** Typing dots under the status line; `i` staggers each dot. */
export function loaderDot(i: number): { animate: TargetAndTransition; transition: Transition } {
  return {
    animate: { opacity: [0.3, 1, 0.3], y: [0, -3, 0] },
    transition: { duration: 1.1, ease: "easeInOut", repeat: Infinity, delay: i * 0.18 },
  };
}

/** Status line swap. */
export const loaderStatus: Variants = {
  hidden: { opacity: 0, y: 4 },
  visible: { opacity: 1, y: 0, transition: tween(duration.base) },
  exit: { opacity: 0, y: -4, transition: tween(duration.fast) },
};

/** Loader in / out: soft scale-fade in, instant-feeling fade out. */
export const loaderPresence: Variants = {
  hidden: { opacity: 0, scale: 0.94 },
  visible: { opacity: 1, scale: 1, transition: tween(duration.base) },
  exit: { opacity: 0, scale: 0.96, transition: tween(duration.fast) },
};

/** Top route-transition bar: creeps toward the end while the next page loads. */
export const routeProgress: { initial: TargetAndTransition; animate: TargetAndTransition; transition: Transition } = {
  initial: { scaleX: 0.05 },
  animate: { scaleX: [0.05, 0.6, 0.85, 0.95] },
  transition: { duration: 6, ease: "easeOut", times: [0, 0.15, 0.5, 1] },
};
