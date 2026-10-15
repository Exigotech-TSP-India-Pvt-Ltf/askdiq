"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { readStorageJson, writeStorage } from "@/lib/storage";

/** Row density for admin tables (newplan §15.1). */
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
export function useTablePreferences(tableId: string) {
  const [prefs, setPrefs] = useState<StoredPrefs>(DEFAULT_PREFS);
  const [loaded, setLoaded] = useState(false);
  const key = tablePrefsKey(tableId);

  // Read after mount so server and first client render agree.
  useEffect(() => {
    setPrefs(readStorageJson(key, isStoredPrefs) ?? DEFAULT_PREFS);
    setLoaded(true);
  }, [key]);

  useEffect(() => {
    if (loaded) writeStorage(key, JSON.stringify(prefs));
  }, [key, loaded, prefs]);

  const toggleColumn = useCallback((column: string) => {
    setPrefs((p) => ({
      ...p,
      hidden: p.hidden.includes(column) ? p.hidden.filter((k) => k !== column) : [...p.hidden, column],
    }));
  }, []);

  const setDensity = useCallback((density: TableDensity) => setPrefs((p) => ({ ...p, density })), []);

  const visibleColumns = useCallback(
    <T extends { key: string }>(columns: T[]): T[] => columns.filter((c) => !prefs.hidden.includes(c.key)),
    [prefs.hidden],
  );

  return useMemo(
    () => ({ hiddenColumns: prefs.hidden, density: prefs.density, toggleColumn, setDensity, visibleColumns }),
    [prefs, toggleColumn, setDensity, visibleColumns],
  );
}
