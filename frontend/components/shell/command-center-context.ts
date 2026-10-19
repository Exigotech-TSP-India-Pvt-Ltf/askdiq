"use client";

import { createContext, useContext } from "react";

export interface CommandCenterValue {
  openPalette: () => void;
  openShortcuts: () => void;
}

/**
 * Kept apart from CommandCenter.tsx (which imports the Sidebar's NAV_ITEMS)
 * so the Sidebar can read it without an import cycle.
 */
export const CommandCenterContext = createContext<CommandCenterValue | null>(null);

/** Null outside CommandCenter (the sidebar hides its go-to-page button). */
export function useCommandCenter(): CommandCenterValue | null {
  return useContext(CommandCenterContext);
}
