"use client";

import React from "react";
import { Sun, Moon } from "lucide-react";
import { useTheme } from "@/lib/theme-context";

interface ThemeToggleProps {
  className?: string;
  size?: "sm" | "md";
}

export function ThemeToggle({ className = "", size = "md" }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";

  const btnSize = size === "sm" ? "h-8 w-8" : "h-9 w-9";
  const iconSize = size === "sm" ? "h-4 w-4" : "h-4 w-4";

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className={`group relative flex ${btnSize} items-center justify-center rounded-lg border border-ink/10 dark:border-white/10 bg-white dark:bg-dark-surface text-ink/70 dark:text-slate-200 shadow-sm transition-all duration-200 hover:border-brand-500/40 hover:bg-brand-50/50 dark:hover:bg-brand-950/40 dark:hover:text-white active:scale-95 ${className}`}
    >
      {isDark ? (
        <Sun
          className={`${iconSize} text-amber-400 transition-transform duration-300 group-hover:rotate-45`}
          strokeWidth={2}
        />
      ) : (
        <Moon
          className={`${iconSize} text-slate-600 transition-transform duration-300 group-hover:-rotate-12`}
          strokeWidth={2}
        />
      )}
    </button>
  );
}
