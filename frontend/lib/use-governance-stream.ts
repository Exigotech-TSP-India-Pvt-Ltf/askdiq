"use client";

/**
 * Live Governance updates over SSE (planchanges4 F4.2, G13).
 *
 * - One `EventSource` on `/admin/governance/stream` per mounted view; it
 *   replaces the old 10 s polling.
 * - On error the source is closed and reopened with exponential backoff
 *   (1 s, 2 s, 4 s ... capped at 30 s); a successful open resets it.
 * - While the tab is hidden the stream is closed (no server work for a tab
 *   nobody is looking at) and reopened as soon as it is visible again.
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  STREAM_EVENTS,
  governanceStreamUrl,
  type GovernanceStreamEvent,
  type StreamEventType,
} from "@/lib/governance-api";
import type { Audience } from "@/lib/types";

export const STREAM_BACKOFF = { initialMs: 1_000, factor: 2, maxMs: 30_000 } as const;

/** Delay before reconnect attempt `attempt` (0-based). */
export function backoffDelay(attempt: number): number {
  const delay = STREAM_BACKOFF.initialMs * STREAM_BACKOFF.factor ** Math.max(0, attempt);
  return Math.min(STREAM_BACKOFF.maxMs, delay);
}

export type StreamStatus = "connecting" | "live" | "reconnecting" | "paused" | "unsupported";

// --- document visibility ----------------------------------------------------

function subscribeVisibility(onChange: () => void): () => void {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

/** true while the page is visible (always true on the server). */
export function usePageVisible(): boolean {
  return useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState !== "hidden",
    () => true,
  );
}

// --- SSE --------------------------------------------------------------------

export interface GovernanceStreamOptions {
  audience: Audience;
  onEvent: (event: GovernanceStreamEvent) => void;
  enabled?: boolean;
}

export interface GovernanceStreamState {
  status: StreamStatus;
  /** Epoch ms of the last event received (0 = none yet). */
  lastEventAt: number;
  /** Reconnect attempts since the last successful open. */
  attempt: number;
}

export function useGovernanceStream({ audience, onEvent, enabled = true }: GovernanceStreamOptions): GovernanceStreamState {
  const visible = usePageVisible();
  const supported = typeof EventSource !== "undefined";
  const [status, setStatus] = useState<StreamStatus>(supported ? "connecting" : "unsupported");
  const [lastEventAt, setLastEventAt] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const dispatch = useCallback((type: StreamEventType, raw: string) => {
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }
    setLastEventAt(Date.now());
    onEventRef.current({ type, data } as GovernanceStreamEvent);
  }, []);

  useEffect(() => {
    if (!supported) {
      setStatus("unsupported");
      return undefined;
    }
    if (!enabled || !visible) {
      setStatus("paused");
      return undefined;
    }

    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let tries = 0;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setStatus(tries === 0 ? "connecting" : "reconnecting");
      const es = new EventSource(governanceStreamUrl(audience));
      source = es;
      es.onopen = () => {
        tries = 0;
        setAttempt(0);
        setStatus("live");
      };
      es.onerror = () => {
        es.close();
        if (disposed || source !== es) return;
        source = null;
        const delay = backoffDelay(tries);
        tries += 1;
        setAttempt(tries);
        setStatus("reconnecting");
        timer = setTimeout(connect, delay);
      };
      for (const type of STREAM_EVENTS) {
        es.addEventListener(type, (event) => dispatch(type, (event as MessageEvent<string>).data));
      }
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      source?.close();
      source = null;
    };
  }, [audience, dispatch, enabled, supported, visible]);

  return { status, lastEventAt, attempt };
}

// --- auto-refresh (Traces / Logs) -------------------------------------------

export const AUTO_REFRESH_MS = 15_000;

/** Calls `refresh` every `intervalMs` while enabled and the tab is visible (G13). */
export function useAutoRefresh(enabled: boolean, refresh: () => void, intervalMs: number = AUTO_REFRESH_MS): boolean {
  const visible = usePageVisible();
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const running = enabled && visible;

  useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(() => refreshRef.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, running]);

  return running;
}
