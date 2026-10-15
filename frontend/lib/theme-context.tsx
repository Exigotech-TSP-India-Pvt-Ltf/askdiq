"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  DARK_QUERY,
  THEME_STORAGE_KEY,
  applyTheme,
  isThemeMode,
  resolveTheme,
  type ResolvedTheme,
  type ThemeMode,
} from "@/lib/theme-script";

export type { ResolvedTheme, ThemeMode } from "@/lib/theme-script";

interface ThemeContextType {
  /** The theme actually shown ("light" | "dark"). Kept for existing callers. */
  theme: ResolvedTheme;
  /** Same as `theme`; explicit name for new code. */
  resolvedTheme: ResolvedTheme;
  /** The user's preference, including "system" (follows the OS live). */
  mode: ThemeMode;
  toggleTheme: () => void;
  setTheme: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function readStoredMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeMode(stored) ? stored : "system";
  } catch {
    return "system"; // storage blocked (private mode, sandboxed iframe)
  }
}

function getMediaQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(DARK_QUERY);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("system");
  const [systemDark, setSystemDark] = useState(false);
  // Until storage has been read, the pre-paint script (app/layout.tsx) owns the
  // <html> class; applying our SSR default here would flash the wrong theme.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setMode(readStoredMode());
    const mq = getMediaQuery();
    setSystemDark(mq?.matches ?? false);
    setReady(true);

    // T4: follow the OS setting live while in System mode.
    const onSystemChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    mq?.addEventListener?.("change", onSystemChange);

    // T4: keep every open tab on the same theme.
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
      setMode(isThemeMode(event.newValue) ? event.newValue : "system");
    };
    window.addEventListener("storage", onStorage);

    return () => {
      mq?.removeEventListener?.("change", onSystemChange);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const resolvedTheme = resolveTheme(mode, systemDark);

  useEffect(() => {
    if (ready) applyTheme(resolvedTheme);
  }, [ready, resolvedTheme]);

  const setTheme = useCallback((next: ThemeMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage unavailable: the choice still applies for this tab.
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(resolvedTheme === "dark" ? "light" : "dark");
  }, [resolvedTheme, setTheme]);

  const value = useMemo<ThemeContextType>(
    () => ({ theme: resolvedTheme, resolvedTheme, mode, toggleTheme, setTheme }),
    [resolvedTheme, mode, toggleTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextType {
  const context = useContext(ThemeContext);
  if (!context) {
    return {
      theme: "light",
      resolvedTheme: "light",
      mode: "system",
      toggleTheme: () => {},
      setTheme: () => {},
    };
  }
  return context;
}
