"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { CommandCenterContext, useCommandCenter } from "@/components/shell/command-center-context";
import { CommandPalette, type PaletteItem, type PaletteSearchProvider } from "@/components/ui/CommandPalette";
import { ShortcutsSheet } from "@/components/ui/ShortcutsSheet";
import { NAV_ITEMS, isNavItemEnabled } from "@/components/Sidebar";
import { isHelpShortcut, isPaletteShortcut } from "@/lib/shortcuts";
import { useAuth } from "@/lib/auth-context";
import { hasPermission, isPermission } from "@/lib/permissions";
import { Compass } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTourOptional } from "@/components/tour/TourProvider";
import { tourIdForPath } from "@/lib/tour/routes";

export { useCommandCenter };

/** Pages offered by the palette: the enabled sidebar items (permission-filtered when shown). */
export const PALETTE_PAGES: PaletteItem[] = NAV_ITEMS.filter(isNavItemEnabled).map((item) => ({
  id: item.href,
  label: item.label,
  href: item.href,
  icon: item.icon,
  keywords: item.keywords,
  hint: item.href,
  permission: item.permission,
  anyPermission: item.anyPermission,
}));

const TOUR_HREF_PREFIX = "#tour:";
const PAGE_TOUR_HREF = `${TOUR_HREF_PREFIX}page`;
const SHELL_TOUR_HREF = `${TOUR_HREF_PREFIX}shell`;

/** "Product tours" palette actions (shown only inside a TourProvider). */
export const PALETTE_TOURS: PaletteItem[] = [
  {
    id: PAGE_TOUR_HREF,
    label: "Tour this page",
    href: PAGE_TOUR_HREF,
    group: "Product tours",
    icon: Compass,
    keywords: ["tour", "guide", "walkthrough", "help", "onboarding"],
  },
  {
    id: SHELL_TOUR_HREF,
    label: "Dashboard basics tour",
    href: SHELL_TOUR_HREF,
    group: "Product tours",
    icon: Compass,
    keywords: ["tour", "guide", "walkthrough", "help", "onboarding", "basics"],
  },
];

interface CommandCenterProps {
  children: React.ReactNode;
  /** `/admin/search` results once that endpoint exists. */
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
  const tour = useTourOptional();
  const router = useRouter();
  const onNavigate = useCallback(
    (href: string) => {
      if (!href.startsWith(TOUR_HREF_PREFIX)) {
        router.push(href);
        return;
      }
      const id = href === SHELL_TOUR_HREF ? "shell" : tourIdForPath(window.location.pathname, window.location.search);
      if (id) tour?.start(id);
    },
    [router, tour],
  );
  const pages = useMemo(() => (tour ? [...PALETTE_PAGES, ...PALETTE_TOURS] : PALETTE_PAGES), [tour]);
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
        pages={pages}
        onNavigate={tour ? onNavigate : undefined}
        searchProvider={searchProvider}
        canAccess={canAccess}
      />
      <ShortcutsSheet open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </CommandCenterContext.Provider>
  );
}
