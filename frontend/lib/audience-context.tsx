"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import type { Audience, AudienceCounts } from "./types";
import { api } from "./api";
import { useAuth } from "./auth-context";
import { useApiQuery } from "./query-cache";
import { audienceCookie, audienceFromCookieString } from "./audience-seed";

export const AUDIENCE_COUNTS_KEY = "admin/usage/audience-counts";
const AUDIENCE_COUNTS_STALE_MS = 30_000;

const STORAGE_KEY = "askdiq.audience_scope";
const VALID_AUDIENCES: Set<Audience> = new Set(["overview", "users", "testing"]);

interface AudienceContextValue {
  audience: Audience;
  setAudience: (aud: Audience) => void;
  counts: AudienceCounts | null;
  refreshCounts: () => Promise<void>;
}

const AudienceContext = createContext<AudienceContextValue | null>(null);

/**
 * The audience the server rendered with (root layout: `?audience=` via the
 * middleware header, else the cookie). Present in the app, absent in tests
 * that mount the provider on its own.
 */
const AudienceSeedContext = createContext<{ audience: Audience } | null>(null);

/** Root-layout provider of the server-resolved starting audience (see lib/audience-seed.ts). */
export function AudienceSeedProvider({ audience, children }: { audience: Audience; children: React.ReactNode }) {
  return <AudienceSeedContext.Provider value={{ audience }}>{children}</AudienceSeedContext.Provider>;
}

/** Remembers the choice in localStorage and in the cookie the server reads on the next load. */
function persistAudience(audience: Audience): void {
  try {
    localStorage.setItem(STORAGE_KEY, audience);
  } catch {
    // localStorage unavailable
  }
  try {
    document.cookie = audienceCookie(audience, window.location.protocol === "https:");
  } catch {
    // cookies blocked
  }
}

function readStoredAudience(): Audience | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored && VALID_AUDIENCES.has(stored as Audience) ? (stored as Audience) : null;
  } catch {
    return null;
  }
}

/** Read audience from current browser URL (works with history.replaceState). */
function readAudienceFromUrl(): Audience {
  if (typeof window === "undefined") return "overview";
  const sp = new URLSearchParams(window.location.search);
  const urlAud = sp.get("audience") as Audience | null;
  return urlAud && VALID_AUDIENCES.has(urlAud) ? urlAud : "overview";
}

export function AudienceProvider({ children }: { children: React.ReactNode }) {
  // If nested inside another AudienceProvider, inherit existing context
  const parent = useContext(AudienceContext);

  const auth = useAuth();
  const token = auth?.token || "";

  const seed = useContext(AudienceSeedContext);

  // 1. Initialize audience. In the app the server-resolved seed is used, so
  //    the server HTML and the first client render agree (no hydration
  //    mismatch, VA-54). Without a seed (a provider mounted on its own):
  //    URL query -> localStorage -> default 'overview'.
  const [audience, setAudienceState] = useState<Audience>(() => {
    if (seed) return seed.audience;
    const fromUrl = readAudienceFromUrl();
    if (fromUrl !== "overview") return fromUrl;
    if (typeof window !== "undefined") {
      try {
        const stored = localStorage.getItem(STORAGE_KEY) as Audience | null;
        if (stored && VALID_AUDIENCES.has(stored)) {
          return stored;
        }
      } catch {
        // localStorage unavailable
      }
    }
    return "overview";
  });

  // One cached request however many providers are nested (and persisted with
  // the query cache); the live stream refreshes it when the counts change.
  const countsQuery = useApiQuery<AudienceCounts>(
    token ? AUDIENCE_COUNTS_KEY : null,
    (signal) => api.getAudienceCounts(token, signal),
    { staleTime: AUDIENCE_COUNTS_STALE_MS },
  );
  const counts = countsQuery.data ?? null;
  const refetchCounts = countsQuery.refetch;
  const refreshCounts = useCallback(async () => {
    await refetchCounts();
  }, [refetchCounts]);

  // Once after mount: a choice remembered only in localStorage (before the
  // cookie existed) is applied now, after hydration, and moved to the cookie.
  useEffect(() => {
    if (!seed || parent) return;
    if (readAudienceFromUrl() !== "overview") return;
    if (audienceFromCookieString(document.cookie) !== null) return;
    const stored = readStoredAudience();
    if (stored && stored !== seed.audience) setAudienceState(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the cookie and localStorage in step with the active audience.
  useEffect(() => {
    if (!parent) persistAudience(audience);
  }, [audience, parent]);

  // Listen for browser back / forward navigation (popstate)
  useEffect(() => {
    function onPopState() {
      setAudienceState(readAudienceFromUrl());
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const setAudience = useCallback((newAudience: Audience) => {
    const valid = VALID_AUDIENCES.has(newAudience) ? newAudience : "overview";
    setAudienceState(valid);

    if (typeof window !== "undefined") {
      persistAudience(valid);

      // Mirror to URL search parameter smoothly without triggering
      // Next.js router transitions or Suspense flicker
      const url = new URL(window.location.href);
      if (valid === "overview") {
        url.searchParams.delete("audience");
      } else {
        url.searchParams.set("audience", valid);
      }
      window.history.replaceState(null, "", `${url.pathname}${url.search}`);
    }
  }, []);

  if (parent) {
    return <>{children}</>;
  }

  return (
    <AudienceContext.Provider value={{ audience, setAudience, counts, refreshCounts }}>
      {children}
    </AudienceContext.Provider>
  );
}

export function useAudience(): AudienceContextValue {
  const ctx = useContext(AudienceContext);
  if (!ctx) {
    // Graceful fallback for components rendered outside AudienceProvider
    return {
      audience: "overview",
      setAudience: () => {},
      counts: null,
      refreshCounts: async () => {},
    };
  }
  return ctx;
}
