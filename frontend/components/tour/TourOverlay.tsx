"use client";

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { m } from "motion/react";
import { ArrowLeft, ArrowRight, Check, Compass, X } from "lucide-react";
import type { TourStep } from "@/lib/tour/types";
import { SPOTLIGHT_PADDING, placeCard, type Box } from "@/lib/tour/dom";
import { instant, spring, tourProgress, tourScrimFade } from "@/lib/motion";
import { iconButton, primaryButton, secondaryButton } from "@/components/ui/styles";

const CARD_WIDTH = 360;
const SCRIM = "rgb(2 6 23 / 0.62)";

interface TourOverlayProps {
  tourTitle: string;
  step: TourStep;
  index: number;
  total: number;
  target: HTMLElement | null;
  reduced: boolean;
  onNext: () => void;
  onBack: () => void;
  onClose: () => void;
}

function measure(el: HTMLElement | null): Box | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    top: r.top - SPOTLIGHT_PADDING,
    left: r.left - SPOTLIGHT_PADDING,
    width: r.width + SPOTLIGHT_PADDING * 2,
    height: r.height + SPOTLIGHT_PADDING * 2,
  };
}

function viewport(): { vw: number; vh: number } {
  return { vw: window.innerWidth, vh: window.innerHeight };
}

/** Dimmed backdrop, spotlight around the target and the floating step card. */
export function TourOverlay({ tourTitle, step, index, total, target, reduced, onNext, onBack, onClose }: TourOverlayProps) {
  const titleId = useId();
  const bodyId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const [rect, setRect] = useState<Box | null>(() => measure(target));
  const [cardHeight, setCardHeight] = useState(0);
  const [vp, setVp] = useState(viewport);

  const isFirst = index === 0;
  const isLast = index === total - 1;

  // Re-measure on step change, resize and any (captured) scroll, throttled to one frame.
  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        setRect(measure(target));
        setVp(viewport());
      });
    };
    setRect(measure(target));
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [target]);

  useLayoutEffect(() => {
    setCardHeight(cardRef.current?.offsetHeight || 1);
  }, [index, step, vp]);

  // Focus the primary action on every step.
  useEffect(() => {
    primaryRef.current?.focus({ preventScroll: true });
  }, [index]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const card = cardRef.current;
      const onButton = event.target instanceof HTMLButtonElement && !!card?.contains(event.target);
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight" || (event.key === "Enter" && !onButton)) onNext();
      else if (event.key === "ArrowLeft") {
        if (!isFirst) onBack();
      } else if (event.key === "Tab" && card) {
        // Keep focus inside the card.
        const focusables = Array.from(card.querySelectorAll<HTMLElement>("button:not([disabled])"));
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !card.contains(active))) last.focus();
        else if (!event.shiftKey && (active === last || !card.contains(active))) first.focus();
        else return;
      } else return;
      event.preventDefault();
      event.stopPropagation();
    },
    [isFirst, onBack, onClose, onNext],
  );

  useEffect(() => {
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onKeyDown]);

  if (typeof document === "undefined") return null;

  const width = Math.min(CARD_WIDTH, vp.vw - 32);
  const pos = placeCard(rect, step.placement, width, cardHeight, vp.vw, vp.vh);
  const transition = reduced ? instant : spring;
  const progress = ((index + 1) / total) * 100;

  return createPortal(
    <div className="fixed inset-0 z-[120]" data-testid="tour-overlay">
      {/* Click shield: the page is inert while the tour runs. */}
      <div className="absolute inset-0" aria-hidden="true" />
      {rect ? (
        <m.div
          aria-hidden="true"
          data-testid="tour-spotlight"
          className="pointer-events-none fixed rounded-xl ring-2 ring-brand-400/80 dark:ring-brand-300/70"
          style={{ boxShadow: `0 0 0 9999px ${SCRIM}` }}
          initial={false}
          animate={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
          transition={transition}
        />
      ) : (
        <m.div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 backdrop-blur-[2px]"
          style={{ background: SCRIM }}
          initial={reduced ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={reduced ? instant : tourScrimFade}
        />
      )}

      <m.div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        data-testid="tour-card"
        className="fixed overflow-hidden rounded-2xl border border-line bg-surface-card shadow-studio"
        style={{ width }}
        initial={false}
        animate={{ top: pos.top, left: pos.left }}
        transition={transition}
      >
        <div className="h-1 w-full bg-surface-muted" aria-hidden="true">
          <m.div
            className="h-full bg-brand-600 dark:bg-brand-400"
            data-testid="tour-progress"
            initial={false}
            animate={{ width: `${progress}%` }}
            transition={reduced ? instant : tourProgress}
          />
        </div>
        <div className="p-5">
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
              <Compass className="h-3.5 w-3.5 shrink-0" strokeWidth={2} aria-hidden="true" />
              <span className="truncate">{tourTitle}</span>
            </span>
            <button type="button" onClick={onClose} aria-label="Skip tour" title="Skip tour (Esc)" className={iconButton}>
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          </div>
          <h2 id={titleId} className="text-[15px] font-semibold tracking-tight text-fg">
            {step.title}
          </h2>
          <p id={bodyId} className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">
            {step.body}
          </p>
          <div className="mt-5 flex items-center justify-between gap-3">
            <span className="text-[11px] font-medium tabular-nums text-fg-subtle">
              Step {index + 1} of {total}
            </span>
            <div className="flex items-center gap-2">
              {isFirst ? null : (
                <button type="button" onClick={onBack} className={secondaryButton}>
                  <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  Back
                </button>
              )}
              <button ref={primaryRef} type="button" onClick={onNext} className={primaryButton}>
                {isLast ? "Finish" : "Next"}
                {isLast ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                ) : (
                  <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>
        </div>
      </m.div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {`Step ${index + 1} of ${total}: ${step.title}`}
      </p>
    </div>,
    document.body,
  );
}
