"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { DEFAULT_BRAND, brandCssVariables, brandCssVariablesComplete, normalizeBrand, type Brand } from "@/lib/brand";

const BrandContext = createContext<Brand>(DEFAULT_BRAND);

export interface BrandUpdater {
  /** Replaces the brand app-wide (names, logos, --brand-* variables) without a reload. */
  setBrand: (brand: Brand) => void;
  /** Re-reads `GET /branding` through the proxy and applies it. Resolves to the new brand, or null on failure. */
  refresh: () => Promise<Brand | null>;
}

const noopUpdater: BrandUpdater = { setBrand: () => undefined, refresh: async () => null };
const BrandUpdaterContext = createContext<BrandUpdater>(noopUpdater);

/**
 * Provides the resolved brand (fetched by the server layout) to client
 * components and writes the tenant's `--brand-*` palette overrides onto <html>,
 * so every `brand-*` Tailwind class follows the white-label colours.
 *
 * Settings → Branding calls `useBrandUpdater().setBrand()` after a save: the
 * new brand then writes every `--brand-*` variable (so it also wins over the
 * server-rendered palette <style>), and every `useBrand()` consumer re-renders.
 */
export function BrandProvider({
  brand = DEFAULT_BRAND,
  children,
}: {
  brand?: Brand;
  children: React.ReactNode;
}) {
  const [override, setOverride] = useState<Brand | null>(null);

  // A new server brand (navigation / ISR refresh) supersedes a local update.
  useEffect(() => setOverride(null), [brand]);

  const active = override ?? brand;

  useEffect(() => {
    const root = document.documentElement;
    const vars = override ? brandCssVariablesComplete(override) : brandCssVariables(brand);
    for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
    return () => {
      for (const name of Object.keys(vars)) root.style.removeProperty(name);
    };
  }, [brand, override]);

  const refresh = useCallback(async (): Promise<Brand | null> => {
    try {
      const res = await fetch("/api/proxy/branding", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!res.ok) return null;
      const next = normalizeBrand(await res.json());
      setOverride(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  const updater = useMemo<BrandUpdater>(() => ({ setBrand: setOverride, refresh }), [refresh]);

  return (
    <BrandUpdaterContext.Provider value={updater}>
      <BrandContext.Provider value={active}>{children}</BrandContext.Provider>
    </BrandUpdaterContext.Provider>
  );
}

/**
 * Supplies a brand to a subtree WITHOUT touching the document's CSS variables
 * (Settings live preview of an unsaved draft).
 */
export function BrandScope({ brand, children }: { brand: Brand; children: React.ReactNode }) {
  return <BrandContext.Provider value={brand}>{children}</BrandContext.Provider>;
}

/** The active brand; the seeded defaults when rendered outside BrandProvider. */
export function useBrand(): Brand {
  return useContext(BrandContext);
}

/** Runtime brand updates (a no-op outside BrandProvider). */
export function useBrandUpdater(): BrandUpdater {
  return useContext(BrandUpdaterContext);
}
