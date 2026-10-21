"use client";

import React, { useCallback, useEffect, useRef } from "react";
import { usePopover } from "@/lib/use-popover";

/** Items the roving focus moves between (menus and listboxes). */
export const MENU_ITEM_SELECTOR = '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="option"]';
const TYPEAHEAD_RESET_MS = 500;

/** Enabled items that belong to `panel` itself (not to a nested panel). */
export function menuItems(panel: HTMLElement | null): HTMLElement[] {
  if (!panel) return [];
  return Array.from(panel.querySelectorAll<HTMLElement>(MENU_ITEM_SELECTOR)).filter(
    (el) =>
      el.closest("[data-menu-panel]") === panel &&
      !el.hasAttribute("disabled") &&
      el.getAttribute("aria-disabled") !== "true",
  );
}

/** The item a panel focuses first: the selected / checked one, else the first. */
export function initialItem(panel: HTMLElement | null): HTMLElement | null {
  const items = menuItems(panel);
  return (
    items.find((el) => el.getAttribute("aria-selected") === "true" || el.getAttribute("aria-checked") === "true") ??
    items[0] ??
    null
  );
}

/**
 * Roving focus over a panel's items: ↑ / ↓ (wrapping), Home / End and
 * one-letter typeahead. Returns true when it handled the key. Linear scans of
 * the item list only (no regex on user input).
 */
export function moveMenuFocus(panel: HTMLElement | null, event: React.KeyboardEvent, typeahead: { text: string; at: number }): boolean {
  const items = menuItems(panel);
  if (items.length === 0) return false;
  const current = items.indexOf(document.activeElement as HTMLElement);
  let next = -1;
  if (event.key === "ArrowDown") next = current < 0 ? 0 : (current + 1) % items.length;
  else if (event.key === "ArrowUp") next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = items.length - 1;
  else if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const now = Date.now();
    typeahead.text = (now - typeahead.at > TYPEAHEAD_RESET_MS ? "" : typeahead.text) + event.key.toLowerCase();
    typeahead.at = now;
    const query = typeahead.text.slice(0, 32);
    // A repeated / new first letter moves on from the current item; a longer
    // query keeps matching from it.
    const start = query.length > 1 ? Math.max(current, 0) : current + 1;
    for (let step = 0; step < items.length; step += 1) {
      const index = (start + step) % items.length;
      if ((items[index].textContent ?? "").trim().toLowerCase().startsWith(query)) {
        next = index;
        break;
      }
    }
    if (next < 0) return true;
  } else return false;
  items[next]?.focus();
  return true;
}

export interface UseMenuOptions {
  /** Where focus lands when the panel opens (default: the selected item, else the first). */
  focusOnOpen?: "initial" | "first" | "last" | "none";
}

/**
 * WAI-ARIA APG menu button / listbox-in-popover state on top of `usePopover`
 * (outside click and Esc close, focus returns to the trigger): moves focus
 * into the panel on open, roving focus with ↑ / ↓ / Home / End / typeahead,
 * and Tab closes. Mark the panel element with `data-menu-panel` and spread
 * `panelProps` on it; spread `triggerProps` on the trigger.
 */
export function useMenu<T extends HTMLElement = HTMLButtonElement>({ focusOnOpen = "initial" }: UseMenuOptions = {}) {
  const popover = usePopover<T>();
  const { open, setOpen, close, panelRef } = popover;
  const typeahead = useRef({ text: "", at: 0 });
  const openWith = useRef<"initial" | "first" | "last" | "none">(focusOnOpen);

  useEffect(() => {
    if (!open || openWith.current === "none") return;
    const panel = panelRef.current;
    const items = menuItems(panel);
    const target =
      openWith.current === "last" ? items[items.length - 1] : openWith.current === "first" ? items[0] : initialItem(panel);
    (target ?? panel)?.focus({ preventScroll: true });
    openWith.current = focusOnOpen;
  }, [open, panelRef, focusOnOpen]);

  const openMenu = useCallback(
    (focus: "initial" | "first" | "last" = "initial") => {
      openWith.current = focus;
      setOpen(true);
    },
    [setOpen],
  );

  const onTriggerKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openMenu(event.key === "ArrowUp" ? "last" : "initial");
      }
    },
    [openMenu],
  );

  const onPanelKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "Tab") {
        // APG: Tab leaves the menu and closes it; focus moves on naturally.
        close(false);
        return;
      }
      if (moveMenuFocus(panelRef.current, event, typeahead.current)) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    [close, panelRef],
  );

  return {
    ...popover,
    openMenu,
    triggerProps: { onKeyDown: onTriggerKeyDown, "aria-expanded": open },
    panelProps: { onKeyDown: onPanelKeyDown, tabIndex: -1, "data-menu-panel": "" },
  };
}
