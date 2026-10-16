"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { TOURS } from "@/lib/tour/registry";
import type { PageTour, TourId } from "@/lib/tour/types";
import { markTourSeen, waitForTarget } from "@/lib/tour/dom";
import { usePrefersReducedMotion } from "@/lib/motion";
import { TourOverlay } from "@/components/tour/TourOverlay";

/** How long a `tab` step waits for its target after switching tabs. */
export const TAB_TARGET_TIMEOUT_MS = 1500;
/** How long a plain step waits for late-rendering content before it is skipped. */
export const TARGET_TIMEOUT_MS = 400;

export interface TourContextValue {
  start: (id: TourId) => void;
  stop: () => void;
  isActive: boolean;
  activeId: TourId | null;
  /** Desktop header launchers mounted; the floating launcher hides while any is. */
  headerLaunchers: number;
  registerHeaderLauncher: () => () => void;
}

const TourContext = createContext<TourContextValue | null>(null);

const NOOP: TourContextValue = {
  start: () => undefined,
  stop: () => undefined,
  isActive: false,
  activeId: null,
  headerLaunchers: 0,
  registerHeaderLauncher: () => () => undefined,
};

/** Tour controls; a no-op outside `TourProvider`. */
export function useTour(): TourContextValue {
  return useContext(TourContext) ?? NOOP;
}

/** Null outside `TourProvider` (launchers render nothing there). */
export function useTourOptional(): TourContextValue | null {
  return useContext(TourContext);
}

interface Position {
  id: TourId;
  index: number;
}

export interface ResolvedStep {
  index: number;
  target: HTMLElement | null;
}

function setTabParam(router: ReturnType<typeof useRouter>, tab: string): void {
  const url = new URL(window.location.href);
  if (url.searchParams.get("tab") === tab) return;
  url.searchParams.set("tab", tab);
  router.replace(`${url.pathname}${url.search}${url.hash}`, { scroll: false });
}

interface TourProviderProps {
  children: React.ReactNode;
  /** Override the registry (tests). */
  tours?: Partial<Record<TourId, PageTour>>;
}

export function TourProvider({ children, tours }: TourProviderProps) {
  const router = useRouter();
  // Ref, not a dependency: the step resolver must not re-run when the router identity changes.
  const routerRef = useRef(router);
  routerRef.current = router;
  const reduced = usePrefersReducedMotion();
  const [position, setPosition] = useState<Position | null>(null);
  const [resolved, setResolved] = useState<ResolvedStep | null>(null);
  const [headerLaunchers, setHeaderLaunchers] = useState(0);
  const direction = useRef<1 | -1>(1);
  const returnFocus = useRef<HTMLElement | null>(null);

  const tour: PageTour | null = position ? ((tours?.[position.id] ?? TOURS[position.id]) || null) : null;

  const stop = useCallback(() => {
    setPosition(null);
    setResolved(null);
    const el = returnFocus.current;
    returnFocus.current = null;
    if (el && typeof el.focus === "function") window.setTimeout(() => el.focus(), 0);
  }, []);

  const start = useCallback(
    (id: TourId) => {
      const t = tours?.[id] ?? TOURS[id];
      if (!t || t.steps.length === 0) return;
      markTourSeen(id);
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      direction.current = 1;
      setResolved(null);
      setPosition({ id, index: 0 });
    },
    [tours],
  );

  const go = useCallback((delta: 1 | -1) => {
    direction.current = delta;
    setPosition((p) => (p ? { ...p, index: p.index + delta } : p));
  }, []);
  const next = useCallback(() => go(1), [go]);
  const back = useCallback(() => go(-1), [go]);

  // Resolve the current step: switch tab, wait for the target, skip it if it never renders.
  useEffect(() => {
    if (!position || !tour) return;
    const step = tour.steps[position.index];
    if (!step) {
      stop();
      return;
    }
    let cancelled = false;
    const skip = () => {
      const nextIndex = position.index + direction.current;
      if (nextIndex >= tour.steps.length) stop();
      else if (nextIndex < 0) {
        direction.current = 1;
        setPosition({ id: position.id, index: position.index + 1 });
      } else setPosition({ id: position.id, index: nextIndex });
    };
    void (async () => {
      if (step.tab) setTabParam(routerRef.current, step.tab);
      if (!step.target || step.placement === "center") {
        setResolved({ index: position.index, target: null });
        return;
      }
      const el = await waitForTarget(step.target, step.tab ? TAB_TARGET_TIMEOUT_MS : TARGET_TIMEOUT_MS, () => cancelled);
      if (cancelled) return;
      if (!el) {
        skip();
        return;
      }
      if (typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: reduced ? "auto" : "smooth" });
      }
      setResolved({ index: position.index, target: el });
    })();
    return () => {
      cancelled = true;
    };
    // `reduced` only changes scroll behaviour; re-resolving on it is unnecessary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position, tour, stop]);

  const registerHeaderLauncher = useCallback(() => {
    setHeaderLaunchers((n) => n + 1);
    return () => setHeaderLaunchers((n) => n - 1);
  }, []);

  const value = useMemo<TourContextValue>(
    () => ({
      start,
      stop,
      isActive: position !== null,
      activeId: position?.id ?? null,
      headerLaunchers,
      registerHeaderLauncher,
    }),
    [start, stop, position, headerLaunchers, registerHeaderLauncher],
  );

  const step = tour && resolved ? tour.steps[resolved.index] : null;

  return (
    <TourContext.Provider value={value}>
      {children}
      {tour && resolved && step ? (
        <TourOverlay
          tourTitle={tour.title}
          step={step}
          index={resolved.index}
          total={tour.steps.length}
          target={resolved.target}
          reduced={reduced}
          onNext={next}
          onBack={back}
          onClose={stop}
        />
      ) : null}
    </TourContext.Provider>
  );
}
