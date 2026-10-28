"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { AnimatePresence, m } from "motion/react";
import { Compass, LayoutDashboard, MapPin, type LucideIcon } from "lucide-react";
import type { TourId } from "@/lib/tour/types";
import { TOURS } from "@/lib/tour/registry";
import { tourIdForPath } from "@/lib/tour/routes";
import { hasSeenTour, markTourSeen } from "@/lib/tour/dom";
import { pop, useMotionVariants } from "@/lib/motion";
import { focusRing, menuItem, popoverPanel } from "@/components/ui/styles";
import { useTourOptional, type TourContextValue } from "@/components/tour/TourProvider";
import { accountActionButton, accountPopoverPosition, type AccountPopoverPlacement } from "@/components/shell/account-actions";

/** How long the first-visit pulse shows. */
const HINT_MS = 12000;

export type TourLauncherVariant = "sidebar" | "drawer" | "floating";

interface TourLauncherProps {
  /**
   * `sidebar`: the desktop sidebar account corner (registers, hiding the floating one).
   * `drawer`: the mobile navigation drawer. `floating`: fallback while no sidebar one is mounted.
   */
  variant?: TourLauncherVariant;
  /** Menu position in the account corner (ignored by `floating`). */
  placement?: AccountPopoverPlacement;
  /** Runs before a tour starts (the mobile drawer closes so the tour can reach the page). */
  onBeforeStart?: () => void;
}

/** Icon-only "Take the tour" button with a small menu: this page's tour or the dashboard basics. */
export function TourLauncher({ variant = "sidebar", placement = "above", onBeforeStart }: TourLauncherProps) {
  const tour = useTourOptional();
  if (!tour) return null;
  return <TourLauncherInner tour={tour} variant={variant} placement={placement} onBeforeStart={onBeforeStart} />;
}

interface TourLauncherInnerProps extends Required<Pick<TourLauncherProps, "variant" | "placement">> {
  tour: TourContextValue;
  onBeforeStart?: () => void;
}

/** One entry of the tour menu (the sidebar profile menu reuses these). */
export interface TourChoice {
  id: TourId;
  label: string;
  description: string;
  icon: LucideIcon;
}

/**
 * The tours offered on the current route: "Tour this page" (when the page has
 * one) and "Dashboard basics". `pageId` is null on routes without a tour.
 */
export function useTourChoices(): { pageId: TourId | null; choices: TourChoice[] } {
  const pathname = usePathname() ?? "";
  // Tabbed pages pick their tour from ?tab= (read after mount: no hydration mismatch).
  // Re-checked after every render (a tab switch re-renders the header) and only set
  // when it changed, so it cannot loop; useSearchParams here would need a Suspense
  // boundary around the layout-level launcher.
  const [search, setSearch] = useState("");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (window.location.search !== search) setSearch(window.location.search);
  });
  const pageId = tourIdForPath(pathname, search);
  if (!pageId) return { pageId: null, choices: [] };
  const pageTour = TOURS[pageId];
  const choices: TourChoice[] = [];
  if (pageId !== "shell" && pageTour.steps.length > 0) {
    choices.push({ id: pageId, label: "Tour this page", description: pageTour.title, icon: MapPin });
  }
  choices.push({ id: "shell", label: "Dashboard basics", description: "Navigation, filters and tools", icon: LayoutDashboard });
  return { pageId, choices };
}

/**
 * First visit to a toured page: a subtle pulse on the visible launcher (never
 * auto-opens the tour). Only a rendered anchor claims the hint, so a hidden
 * mobile / desktop twin doesn't swallow it.
 */
export function useTourHint(pageId: TourId | null, anchorRef: React.RefObject<HTMLElement | null>, recheck: unknown) {
  const [hint, setHint] = useState(false);
  useEffect(() => {
    setHint(false);
  }, [pageId]);
  useEffect(() => {
    if (!pageId || hasSeenTour(pageId)) return;
    const el = anchorRef.current;
    if (!el || el.getClientRects().length === 0) return;
    markTourSeen(pageId);
    setHint(true);
  }, [pageId, recheck, anchorRef]);
  useEffect(() => {
    if (!hint) return undefined;
    const timer = window.setTimeout(() => setHint(false), HINT_MS);
    return () => window.clearTimeout(timer);
  }, [hint]);
  return [hint, setHint] as const;
}

function TourLauncherInner({ tour, variant, placement, onBeforeStart }: TourLauncherInnerProps) {
  const { pageId, choices } = useTourChoices();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const variants = useMotionVariants(pop);
  const { registerHeaderLauncher, headerLaunchers, start } = tour;
  const [hint, setHint] = useTourHint(pageId, rootRef, headerLaunchers);

  useEffect(() => (variant === "sidebar" ? registerHeaderLauncher() : undefined), [variant, registerHeaderLauncher]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!pageId) return null;
  if (variant === "floating" && headerLaunchers > 0) return null;

  const launch = (id: TourId) => {
    setOpen(false);
    setHint(false);
    onBeforeStart?.();
    start(id);
  };

  const floating = variant === "floating";
  // Account-corner launchers stay static so the menu anchors to the action row / rail.
  const wrapper = floating ? "fixed right-6 top-24 z-30 hidden md:block" : "";
  const triggerClass = floating
    ? `relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-card text-brand-700 shadow-xs transition-colors hover:border-brand-300 hover:bg-brand-50 dark:text-brand-300 dark:hover:border-brand-500/40 dark:hover:bg-brand-500/10 ${focusRing}`
    : accountActionButton;
  const menuPosition = floating ? "right-0 mt-2 origin-top-right" : accountPopoverPosition[placement];

  return (
    <div ref={rootRef} className={wrapper} data-tour="shell-tour-launcher">
      <div className={floating ? "relative" : "relative w-fit"}>
        <button
          ref={triggerRef}
          type="button"
          onClick={() => {
            setHint(false);
            setOpen((o) => !o);
          }}
          aria-label="Take the tour"
          title="Take the tour"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          data-testid="tour-launcher"
          className={triggerClass}
        >
          <Compass className="h-4 w-4 text-brand-700 dark:text-brand-300" strokeWidth={1.75} aria-hidden="true" />
        </button>
        {hint ? (
          <span className="pointer-events-none absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5" data-testid="tour-hint" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75 motion-reduce:animate-none" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-brand-500 ring-2 ring-surface-card" />
          </span>
        ) : null}
      </div>
      <AnimatePresence>
        {open ? (
          <m.div
            id={menuId}
            role="menu"
            aria-label="Product tours"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`absolute z-50 w-56 max-w-[calc(100vw-2rem)] ${menuPosition} ${popoverPanel}`}
          >
            <p className="px-2.5 pb-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">Product tours</p>
            {choices.map((choice) => (
              <button key={choice.id} type="button" role="menuitem" className={menuItem} onClick={() => launch(choice.id)}>
                <choice.icon className="h-4 w-4 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={1.75} aria-hidden="true" />
                <span className="min-w-0">
                  <span className="block font-medium text-fg">{choice.label}</span>
                  <span className="block truncate text-[11px] text-fg-subtle">{choice.description}</span>
                </span>
              </button>
            ))}
          </m.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
