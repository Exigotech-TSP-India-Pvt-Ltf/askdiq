"use client";

import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Keyboard-focusable descendants of `root`, in DOM order. */
export function focusableWithin(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Stack of open overlays: only the topmost one traps Tab and handles Esc, so
 * a ConfirmDialog opened from a drawer does not fight the drawer for focus.
 */
const overlayStack: symbol[] = [];

function isTopmost(id: symbol): boolean {
  return overlayStack[overlayStack.length - 1] === id;
}

export interface FocusTrapOptions {
  /** Called on Escape when this overlay is the topmost one. */
  onEscape?: () => void;
  /** Element to focus on open (defaults to the first focusable element). */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Return focus to the element that had it before opening (default true). */
  restoreFocus?: boolean;
}

/**
 * Modal focus management for drawers, dialogs and sheets: moves focus inside
 * on open, keeps Tab / Shift+Tab inside, closes on Esc and returns focus to
 * the trigger on close.
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  { onEscape, initialFocusRef, restoreFocus = true }: FocusTrapOptions = {},
): void {
  const onEscapeRef = useRef(onEscape);
  onEscapeRef.current = onEscape;

  useEffect(() => {
    if (!active) return undefined;
    const id = Symbol("overlay");
    overlayStack.push(id);
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = ref.current;
    const target = initialFocusRef?.current ?? focusableWithin(root)[0] ?? root;
    target?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (!isTopmost(id)) return;
      const container = ref.current;
      if (event.key === "Escape" && onEscapeRef.current) {
        event.preventDefault();
        event.stopPropagation();
        onEscapeRef.current();
        return;
      }
      if (event.key !== "Tab" || !container) return;
      const items = focusableWithin(container);
      if (items.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      const outside = !container.contains(current);
      if (event.shiftKey && (current === first || outside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (current === last || outside)) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const index = overlayStack.indexOf(id);
      if (index >= 0) overlayStack.splice(index, 1);
      if (restoreFocus && previous && document.contains(previous)) previous.focus();
    };
    // The trap is (re)armed only when `active` flips; refs are read at that time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
}
