"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { CommandPalette, type PaletteItem, type PaletteSearchProvider } from "@/components/ui/CommandPalette";
import { ShortcutsSheet } from "@/components/ui/ShortcutsSheet";
import { NAV_ITEMS, isNavItemEnabled } from "@/components/Sidebar";
import { isHelpShortcut, isPaletteShortcut } from "@/lib/shortcuts";
import { useAuth } from "@/lib/auth-context";
import { hasPermission, isPermission } from "@/lib/permissions";

interface CommandCenterValue {
  openPalette: () => void;
  openShortcuts: () => void;
}

const CommandCenterContext = createContext<CommandCenterValue | null>(null);

/** Null outside CommandCenter (the header hides its palette button). */
export function useCommandCenter(): CommandCenterValue | null {
  return useContext(CommandCenterContext);
}

/** Pages offered by the palette: the enabled sidebar items (permission-filtered when shown). */
export const PALETTE_PAGES: PaletteItem[] = NAV_ITEMS.filter(isNavItemEnabled).map((item) => ({
  id: item.href,
  label: item.label,
  href: item.href,
  icon: item.icon,
  keywords: item.keywords,
  hint: item.href,
  permission: item.permission,
}));

interface CommandCenterProps {
  children: React.ReactNode;
  /** `/admin/search` results once that endpoint exists (newplan §15.1). */
  searchProvider?: PaletteSearchProvider;
  /** Overrides the permission check (default: the signed-in role's permissions). */
  canAccess?: (permission: string) => boolean;
}

/**
 * Global keyboard layer: Ctrl/Cmd+K toggles the command palette (also inside
 * fields), `?` opens the shortcuts sheet (never while typing).
 */
export function CommandCenter({ children, searchProvider, canAccess: canAccessOverride }: CommandCenterProps) {
  const { permissions } = useAuth();
  const canAccess = useCallback(
    (permission: string) =>
      canAccessOverride ? canAccessOverride(permission) : isPermission(permission) && hasPermission(permissions, permission),
    [canAccessOverride, permissions],
  );
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  const openPalette = useCallback(() => {
    setShortcutsOpen(false);
    setPaletteOpen(true);
  }, []);
  const openShortcuts = useCallback(() => {
    setPaletteOpen(false);
    setShortcutsOpen(true);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (isPaletteShortcut(event)) {
        event.preventDefault();
        setShortcutsOpen(false);
        setPaletteOpen((open) => !open);
      } else if (isHelpShortcut(event)) {
        event.preventDefault();
        openShortcuts();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openShortcuts]);

  const value = useMemo(() => ({ openPalette, openShortcuts }), [openPalette, openShortcuts]);

  return (
    <CommandCenterContext.Provider value={value}>
      {children}
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        pages={PALETTE_PAGES}
        searchProvider={searchProvider}
        canAccess={canAccess}
      />
      <ShortcutsSheet open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </CommandCenterContext.Provider>
  );
}
