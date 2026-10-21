"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  DARK_QUERY,
  THEME_STORAGE_KEY,
  applyTheme,
  isThemeMode,
  resolveStoredMode,
  resolveTheme,
  type ResolvedTheme,
  type ThemeMode,
} from "@/lib/theme-script";

export type { ResolvedTheme, ThemeMode } from "@/lib/theme-script";

interface ThemeContextType {
  /** The theme shown ("light" | "dark"). Kept for existing callers. */
  theme: ResolvedTheme;
  /** Same as `theme`. */
  resolvedTheme: ResolvedTheme;
  /** The user's choice: "light", "dark" or "system" (follows the OS). */
  mode: ThemeMode;
  /** Switches to the opposite of the theme shown (an explicit light / dark choice). */
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

/** Reads the stored choice; a missing or unknown value is System. */
function readStoredMode(): ThemeMode {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // storage blocked
  }
  return resolveStoredMode(stored);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("light");
  const [osDark, setOsDark] = useState(false);
  // Until storage has been read, the pre-paint script (app/layout.tsx) owns the
  // <html> class; applying our SSR default here would flash the wrong theme.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setMode(readStoredMode());
    setOsDark(systemPrefersDark());
    setReady(true);

    // T4: keep every open tab on the same theme.
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      if (isThemeMode(event.newValue)) setMode(event.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // System mode follows OS changes live.
  useEffect(() => {
    if (mode !== "system" || typeof window.matchMedia !== "function") return undefined;
    const mql = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setOsDark(event.matches);
    setOsDark(mql.matches);
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, [mode]);

  const resolved = resolveTheme(mode, osDark);

  useEffect(() => {
    if (ready) applyTheme(resolved);
  }, [ready, resolved]);

  const setTheme = useCallback((next: ThemeMode) => {
    if (!isThemeMode(next)) return;
    setMode(next);
    persist(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(resolved === "dark" ? "light" : "dark");
  }, [resolved, setTheme]);

  const value = useMemo<ThemeContextType>(
    () => ({ theme: resolved, resolvedTheme: resolved, mode, toggleTheme, setTheme }),
    [resolved, mode, toggleTheme, setTheme],
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
