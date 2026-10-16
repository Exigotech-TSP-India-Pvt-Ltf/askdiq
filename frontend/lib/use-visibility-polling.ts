"use client";

import { useEffect, useRef } from "react";

/**
 * Runs `callback` every `intervalMs` while the tab is visible. Polling pauses
 * when the document is hidden and resumes (with an immediate run) when it
 * becomes visible again.
 */
export function useVisibilityPolling(callback: () => void, intervalMs: number, enabled = true): void {
  const ref = useRef(callback);
  ref.current = callback;

  useEffect(() => {
    if (!enabled) return undefined;
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => ref.current(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        ref.current();
        start();
      }
    };
    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled]);
}
