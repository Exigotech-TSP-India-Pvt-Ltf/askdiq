"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { ArrowUpRight, ChevronDown, ChevronUp, X } from "lucide-react";
import { crossfade, drawer as drawerVariants, useMotionVariants } from "@/lib/motion";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { focusRing, iconButton, overlayBackdrop, overlayPanel } from "@/components/ui/styles";

export interface DrawerShellProps {
  open: boolean;
  onClose: () => void;
  /** Accessible name of the dialog; rendered as the drawer heading. */
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Extra controls in the header (badges, actions), left of the nav buttons. */
  headerExtra?: React.ReactNode;
  /** Fixed strip between the header and the scrolling body (sub-tabs, filters). */
  toolbar?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** Previous / next record navigation (buttons hidden when both are absent). */
  onPrev?: () => void;
  onNext?: () => void;
  /** Link to the record's full page ("Open full view"). */
  fullViewHref?: string;
  closeLabel?: string;
  /** Tailwind max-width class for the panel. */
  widthClassName?: string;
  /** Element to focus on open (defaults to the close button). */
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  bodyClassName?: string;
}

/**
 * The one drawer used by every inspector: portal, Motion
 * slide-in with a fading backdrop, focus trap with focus returned to the
 * trigger, Esc / backdrop click to close, previous / next navigation and an
 * "Open full view" link.
 */
export function DrawerShell({
  open,
  onClose,
  title,
  description,
  headerExtra,
  toolbar,
  children,
  footer,
  onPrev,
  onNext,
  fullViewHref,
  closeLabel = "Close",
  widthClassName = "max-w-2xl",
  initialFocusRef,
  bodyClassName = "p-6 space-y-6",
}: DrawerShellProps) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const panel = useMotionVariants(drawerVariants);

  useFocusTrap(panelRef, open && mounted, {
    onEscape: onClose,
    initialFocusRef: initialFocusRef ?? closeRef,
  });

  if (!mounted) return null;

  const hasNav = Boolean(onPrev || onNext);

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div key="drawer" className="fixed inset-0 z-[100] flex justify-end">
          <m.div
            data-testid="drawer-backdrop"
            aria-hidden="true"
            className={overlayBackdrop}
            variants={crossfade}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={onClose}
          />
          <m.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            tabIndex={-1}
            variants={panel}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`relative flex h-full w-full flex-col overflow-hidden border-y-0 border-r-0 ${overlayPanel} ${widthClassName}`}
          >
            <div className="flex items-start justify-between gap-3 border-b border-line-brand/70 bg-surface-studio/70 px-6 py-4 dark:border-brand-500/20">
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="flex min-w-0 items-center gap-2 text-sm font-semibold text-fg">
                  {title}
                </h2>
                {description ? (
                  <div id={descriptionId} className="mt-0.5 truncate text-xs text-fg-subtle">
                    {description}
                  </div>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {headerExtra}
                {hasNav ? (
                  <>
                    <button
                      type="button"
                      onClick={onPrev}
                      disabled={!onPrev}
                      aria-label="Previous record"
                      title="Previous record"
                      className={iconButton}
                    >
                      <ChevronUp className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={onNext}
                      disabled={!onNext}
                      aria-label="Next record"
                      title="Next record"
                      className={iconButton}
                    >
                      <ChevronDown className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  </>
                ) : null}
                {fullViewHref ? (
                  <Link
                    href={fullViewHref}
                    title="Open full view"
                    className={`group inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-brand-700 transition-colors hover:bg-surface-muted dark:text-brand-300 ${focusRing}`}
                  >
                    {/* Icon only on phones, where the header is tight. */}
                    <span className="sr-only sm:not-sr-only">Open full view</span>
                    <ArrowUpRight
                      className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                      strokeWidth={1.75}
                      aria-hidden="true"
                    />
                  </Link>
                ) : null}
                <button
                  ref={closeRef}
                  type="button"
                  onClick={onClose}
                  aria-label={closeLabel}
                  title={closeLabel}
                  className={iconButton}
                >
                  <X className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
                </button>
              </div>
            </div>
            {toolbar}
            <div className={`flex-1 overflow-y-auto ${bodyClassName}`}>{children}</div>
            {footer ? <div className="border-t border-line-brand/70 bg-surface-studio/50 px-6 py-3.5 dark:border-brand-500/20">{footer}</div> : null}
          </m.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
