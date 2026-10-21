"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readStorageJson, writeStorage } from "@/lib/storage";

/** Row density for admin tables. */
export type TableDensity = "comfortable" | "compact";

interface StoredPrefs {
  hidden: string[];
  density: TableDensity;
}

const DEFAULT_PREFS: StoredPrefs = { hidden: [], density: "comfortable" };

function isStoredPrefs(value: unknown): value is StoredPrefs {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    Array.isArray(v.hidden) &&
    v.hidden.every((key) => typeof key === "string") &&
    (v.density === "comfortable" || v.density === "compact")
  );
}

export const tablePrefsKey = (tableId: string) => `askdiq.table.${tableId}`;

/**
 * Column visibility and density for one table, remembered per table in
 * localStorage. Storage failures are ignored: the preferences then live in
 * memory for the session.
 */
export function useTablePreferences(
  tableId: string,
  {
    defaultHidden,
  }: {
    /**
     * Columns hidden until the user changes the table (no stored preferences
     * yet), e.g. secondary columns on narrow screens. Read once after mount.
     */
    defaultHidden?: () => readonly string[];
  } = {},
) {
  const [prefs, setPrefs] = useState<StoredPrefs>(DEFAULT_PREFS);
  const [loaded, setLoaded] = useState(false);
  const key = tablePrefsKey(tableId);
  const defaultsRef = useRef(defaultHidden);
  defaultsRef.current = defaultHidden;
  // Viewport-based defaults are not a choice: store only stored or user-changed prefs,
  // so a first visit on a narrow screen does not hide columns on a wide one later.
  const persist = useRef(false);

  // Read after mount so server and first client render agree.
  useEffect(() => {
    const stored = readStorageJson(key, isStoredPrefs);
    persist.current = stored !== null && stored !== undefined;
    setPrefs(stored ?? { ...DEFAULT_PREFS, hidden: [...(defaultsRef.current?.() ?? [])] });
    setLoaded(true);
  }, [key]);

  useEffect(() => {
    if (loaded && persist.current) writeStorage(key, JSON.stringify(prefs));
  }, [key, loaded, prefs]);

  const toggleColumn = useCallback((column: string) => {
    persist.current = true;
    setPrefs((p) => ({
      ...p,
      hidden: p.hidden.includes(column) ? p.hidden.filter((k) => k !== column) : [...p.hidden, column],
    }));
  }, []);

  const setDensity = useCallback((density: TableDensity) => {
    persist.current = true;
    setPrefs((p) => ({ ...p, density }));
  }, []);

  const visibleColumns = useCallback(
    <T extends { key: string }>(columns: T[]): T[] => columns.filter((c) => !prefs.hidden.includes(c.key)),
    [prefs.hidden],
  );

  return useMemo(
    () => ({ hiddenColumns: prefs.hidden, density: prefs.density, toggleColumn, setDensity, visibleColumns }),
    [prefs, toggleColumn, setDensity, visibleColumns],
  );
}
