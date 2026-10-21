"use client";

import React from "react";
import { AnimatePresence, m } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { useTheme, type ResolvedTheme } from "@/lib/theme-context";
import { crossfade } from "@/lib/motion";

interface ThemeToggleProps {
  className?: string;
  size?: "sm" | "md";
}

const NEXT: Record<ResolvedTheme, ResolvedTheme> = { light: "dark", dark: "light" };
const LABEL: Record<ResolvedTheme, string> = { light: "Light", dark: "Dark" };

/**
 * One-click switch to the opposite of the theme shown (an explicit Light or
 * Dark choice). The full Light / Dark / System choice is in the profile menu.
 * The icon shows the mode a click switches to; the label announces both.
 */
export function ThemeToggle({ className = "", size = "md" }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const next = NEXT[resolvedTheme];
  const label = `Theme: ${LABEL[resolvedTheme]}. Switch to ${LABEL[next].toLowerCase()} mode`;

  const btnSize = size === "sm" ? "h-8 w-8" : "h-9 w-9";
  const Icon = resolvedTheme === "dark" ? Sun : Moon;
  const iconColor = resolvedTheme === "dark" ? "text-status-warn" : "text-fg-muted";

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      title={label}
      aria-label={label}
      data-theme-mode={resolvedTheme}
      className={`group relative flex ${btnSize} items-center justify-center rounded-lg border border-ink/10 dark:border-white/10 bg-surface-card dark:bg-dark-surface text-fg-muted shadow-sm transition-all duration-200 hover:border-brand-500/40 hover:bg-brand-50/50 dark:hover:bg-brand-950/40 dark:hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 active:scale-95 ${className}`}
    >
      <AnimatePresence mode="wait" initial={false}>
        <m.span key={resolvedTheme} variants={crossfade} initial="hidden" animate="visible" exit="exit" className="flex">
          <Icon className={`h-4 w-4 ${iconColor}`} strokeWidth={2} aria-hidden="true" />
        </m.span>
      </AnimatePresence>
    </button>
  );
}
