import type { TourPlacement } from "./types";

/** First matching element that is actually rendered (skips `display:none` duplicates). */
export function findTarget(selector: string): HTMLElement | null {
  let nodes: NodeListOf<HTMLElement>;
  try {
    nodes = document.querySelectorAll<HTMLElement>(selector);
  } catch {
    return null;
  }
  for (const node of Array.from(nodes)) {
    if (node.getClientRects().length > 0) return node;
  }
  return null;
}

/** Polls for `selector` until it renders or `timeoutMs` passes. */
export function waitForTarget(selector: string, timeoutMs: number, isCancelled: () => boolean): Promise<HTMLElement | null> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tick = () => {
      if (isCancelled()) return resolve(null);
      const el = findTarget(selector);
      if (el || Date.now() >= deadline) return resolve(el);
      window.setTimeout(tick, 50);
    };
    tick();
  });
}

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

export const SPOTLIGHT_PADDING = 6;

interface Rect {
  top: number;
  left: number;
  right: number;
  bottom: number;
}

/**
 * Spotlight around a target (VA-50): the target rect with uniform padding,
 * clamped to its column (the main scroll area, or the viewport), so a
 * full-width header never spills over the sidebar edge or past the page.
 */
export function spotlightBox(target: Rect, bounds: Rect, padding: number = SPOTLIGHT_PADDING): Box {
  const left = Math.max(target.left - padding, bounds.left);
  const top = Math.max(target.top - padding, bounds.top);
  const right = Math.min(target.right + padding, bounds.right);
  const bottom = Math.min(target.bottom + padding, bounds.bottom);
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}
const GAP = 14;
const MARGIN = 16;

const OPPOSITE: Record<Exclude<TourPlacement, "center">, Exclude<TourPlacement, "center">> = {
  top: "bottom",
  bottom: "top",
  left: "right",
  right: "left",
};

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, Math.max(min, max)));
}

function fits(side: Exclude<TourPlacement, "center">, rect: Box, w: number, h: number, vw: number, vh: number): boolean {
  if (side === "bottom") return rect.top + rect.height + GAP + h <= vh - MARGIN;
  if (side === "top") return rect.top - GAP - h >= MARGIN;
  if (side === "right") return rect.left + rect.width + GAP + w <= vw - MARGIN;
  return rect.left - GAP - w >= MARGIN;
}

/** Card position next to `rect`, flipped if it doesn't fit, clamped to the viewport; centered without a rect. */
export function placeCard(
  rect: Box | null,
  placement: TourPlacement | undefined,
  w: number,
  h: number,
  vw: number,
  vh: number,
): { top: number; left: number } {
  const centered = { top: Math.max(MARGIN, (vh - h) / 2), left: Math.max(MARGIN, (vw - w) / 2) };
  if (!rect || placement === "center") return centered;
  const preferred = placement ?? "bottom";
  const order = [preferred, OPPOSITE[preferred], "bottom", "top", "right", "left"] as const;
  const side = order.find((s) => fits(s, rect, w, h, vw, vh));
  if (!side) return centered;
  let top: number;
  let left: number;
  if (side === "bottom" || side === "top") {
    top = side === "bottom" ? rect.top + rect.height + GAP : rect.top - GAP - h;
    left = rect.left + rect.width / 2 - w / 2;
  } else {
    left = side === "right" ? rect.left + rect.width + GAP : rect.left - GAP - w;
    top = rect.top + rect.height / 2 - h / 2;
  }
  return { top: clamp(top, MARGIN, vh - h - MARGIN), left: clamp(left, MARGIN, vw - w - MARGIN) };
}

const SEEN_PREFIX = "askdiq.tour.seen.";

export function hasSeenTour(id: string): boolean {
  try {
    return window.localStorage.getItem(SEEN_PREFIX + id) === "1";
  } catch {
    return true;
  }
}

export function markTourSeen(id: string): void {
  try {
    window.localStorage.setItem(SEEN_PREFIX + id, "1");
  } catch {
    /* storage unavailable: the hint simply shows again next time */
  }
}
