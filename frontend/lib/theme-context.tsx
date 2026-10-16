"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  DARK_QUERY,
  THEME_STORAGE_KEY,
  applyTheme,
  isThemeMode,
  resolveStoredMode,
  type ResolvedTheme,
  type ThemeMode,
} from "@/lib/theme-script";

export type { ResolvedTheme, ThemeMode } from "@/lib/theme-script";

interface ThemeContextType {
  /** The theme shown ("light" | "dark"). Kept for existing callers. */
  theme: ResolvedTheme;
  /** Same as `theme`. */
  resolvedTheme: ResolvedTheme;
  /** The user's choice; always "light" or "dark" (there is no "system" mode). */
  mode: ThemeMode;
  toggleTheme: () => void;
  setTheme: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(DARK_QUERY).matches;
}

function persist(mode: ThemeMode): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
  } catch {
    // Storage unavailable (private mode, sandboxed iframe): applies to this tab only.
  }
}

/**
 * Reads the stored choice; a missing or legacy "system" value is resolved once
 * to the OS preference and persisted (see lib/theme-script.ts).
 */
function readStoredMode(): ThemeMode {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // storage blocked
  }
  const mode = resolveStoredMode(stored, systemPrefersDark());
  if (stored !== mode) persist(mode);
  return mode;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("light");
  // Until storage has been read, the pre-paint script (app/layout.tsx) owns the
  // <html> class; applying our SSR default here would flash the wrong theme.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setMode(readStoredMode());
    setReady(true);

    // T4: keep every open tab on the same theme.
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      if (isThemeMode(event.newValue)) setMode(event.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (ready) applyTheme(mode);
  }, [ready, mode]);

  const setTheme = useCallback((next: ThemeMode) => {
    if (!isThemeMode(next)) return;
    setMode(next);
    persist(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(mode === "dark" ? "light" : "dark");
  }, [mode, setTheme]);

  const value = useMemo<ThemeContextType>(
    () => ({ theme: mode, resolvedTheme: mode, mode, toggleTheme, setTheme }),
    [mode, toggleTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextType {
  const context = useContext(ThemeContext);
  if (!context) {
    return {
      theme: "light",
      resolvedTheme: "light",
      mode: "light",
      toggleTheme: () => {},
      setTheme: () => {},
    };
  }
  return context;
}
