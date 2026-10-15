"use client";

import React, { createContext, useContext, useEffect } from "react";
import { DEFAULT_BRAND, brandCssVariables, type Brand } from "@/lib/brand";

const BrandContext = createContext<Brand>(DEFAULT_BRAND);

/**
 * Provides the resolved brand (fetched by the server layout) to client
 * components and writes the tenant's `--brand-*` palette overrides onto <html>,
 * so every `brand-*` Tailwind class follows the white-label colours.
 */
export function BrandProvider({
  brand = DEFAULT_BRAND,
  children,
}: {
  brand?: Brand;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const root = document.documentElement;
    const vars = brandCssVariables(brand);
    for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
    return () => {
      for (const name of Object.keys(vars)) root.style.removeProperty(name);
    };
  }, [brand]);

  return <BrandContext.Provider value={brand}>{children}</BrandContext.Provider>;
}

/** The active brand; the seeded defaults when rendered outside BrandProvider. */
export function useBrand(): Brand {
  return useContext(BrandContext);
}
