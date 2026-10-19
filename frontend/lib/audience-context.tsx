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

  // 1. Initialize audience from URL query -> localStorage -> default 'overview'
  const [audience, setAudienceState] = useState<Audience>(() => {
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

    // Persist to localStorage
    if (typeof window !== "undefined") {
      try {
        localStorage.setItem(STORAGE_KEY, valid);
      } catch {
        // ignore
      }

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
