"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * Whether a horizontally scrollable table has content hidden under its
 * right-pinned column (more to scroll to the right). Spread the result on the
 * scroll container as `data-scroll-under`; the shared sticky-cell style shows
 * its divider only then.
 */
export function useScrollUnder<E extends HTMLElement>(): [RefObject<E>, boolean] {
  const ref = useRef<E>(null);
  const [under, setUnder] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setUnder(el.scrollWidth - el.clientWidth - el.scrollLeft > 1);
    update();
    el.addEventListener("scroll", update, { passive: true });
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(update);
      ro.observe(el);
      if (el.firstElementChild) ro.observe(el.firstElementChild);
    }
    return () => {
      el.removeEventListener("scroll", update);
      ro?.disconnect();
    };
  }, []);

  return [ref, under];
}
