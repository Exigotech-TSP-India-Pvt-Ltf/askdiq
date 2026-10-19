"use client";

import React, { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { useCommandCenter } from "@/components/shell/command-center-context";
import { focusRing, iconButton } from "@/components/ui/styles";
import { isApplePlatform, paletteShortcutLabel } from "@/lib/shortcuts";

interface GoToPageButtonProps {
  /** Icon-only rail button (collapsed sidebar). */
  collapsed?: boolean;
  /** Runs before the palette opens (the mobile drawer closes itself). */
  onBeforeOpen?: () => void;
}

/** "Ctrl K" until mounted, then "⌘K" on Apple platforms (avoids a hydration mismatch). */
function useShortcutLabel(): string {
  const [apple, setApple] = useState(false);
  useEffect(() => setApple(isApplePlatform()), []);
  return paletteShortcutLabel(apple);
}

/**
 * Search-field-style trigger for the go-to-page command palette, shown at the
 * top of the sidebar. Hidden outside CommandCenter (no palette to open).
 */
export function GoToPageButton({ collapsed = false, onBeforeOpen }: GoToPageButtonProps) {
  const commands = useCommandCenter();
  const shortcut = useShortcutLabel();
  if (!commands) return null;

  function open() {
    onBeforeOpen?.();
    commands?.openPalette();
  }

  const shared = {
    type: "button" as const,
    onClick: open,
    "data-tour": "shell-command-palette",
    "aria-label": "Go to page",
    "aria-keyshortcuts": "Control+K Meta+K",
    "aria-haspopup": "dialog" as const,
  };

  if (collapsed) {
    return (
      <button {...shared} title={`Go to page (${shortcut})`} className={`${iconButton} h-9 w-9 border border-line bg-surface-muted/60`}>
        <Search className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
      </button>
    );
  }

  return (
    <button
      {...shared}
      className={`group flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-surface-muted/60 px-2.5 text-[13px] text-fg-subtle shadow-sm transition-colors hover:border-brand-300 hover:bg-surface-card hover:text-fg dark:hover:border-brand-700/60 ${focusRing}`}
    >
      <Search className="h-4 w-4 shrink-0 transition-colors group-hover:text-brand-600 dark:group-hover:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-left">Go to page…</span>
      <kbd
        aria-hidden="true"
        className="shrink-0 rounded-md border border-line bg-surface-card px-1.5 py-0.5 font-sans text-[10px] font-medium leading-none text-fg-subtle"
      >
        {shortcut}
      </kbd>
    </button>
  );
}
