"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readStorageJson, writeStorage } from "@/lib/storage";

/** Row density for admin tables. */
export type TableDensity = "comfortable" | "compact";

interface StoredPrefs {
  hidden: string[];
  density: TableDensity;
  /** Auto-hidden (priority) columns the user turned back on. Omitted when empty. */
  shown?: string[];
}

const DEFAULT_PREFS: StoredPrefs = { hidden: [], density: "comfortable" };

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((key) => typeof key === "string");

function isStoredPrefs(value: unknown): value is StoredPrefs {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    isStringArray(v.hidden) &&
    (v.shown === undefined || isStringArray(v.shown)) &&
    (v.density === "comfortable" || v.density === "compact")
  );
}

export const tablePrefsKey = (tableId: string) => `askdiq.table.${tableId}`;

/**
 * Priority columns: column key -> the narrowest viewport (px) that shows it.
 * Below it the column is hidden (still listed, unchecked, in the Columns menu)
 * unless the user turns it back on, so the table fits instead of sliding
 * columns under its pinned Actions column.
 */
export type AutoHideColumns = Readonly<Record<string, number>>;

/**
 * Column visibility and density for one table, remembered per table in
 * localStorage. Storage failures are ignored: the preferences then live in
 * memory for the session.
 */
export function useTablePreferences(
  tableId: string,
  {
    autoHide,
  }: {
    /** Priority-based hiding by viewport width; follows resizes. */
    autoHide?: AutoHideColumns;
  } = {},
) {
  const [prefs, setPrefs] = useState<StoredPrefs>(DEFAULT_PREFS);
  const [loaded, setLoaded] = useState(false);
  // Infinity until mounted, so server and first client render agree (nothing auto-hidden).
  const [width, setWidth] = useState(Number.POSITIVE_INFINITY);
  const key = tablePrefsKey(tableId);
  // Only stored or user-changed prefs are written: viewport-based hiding is not a choice.
  const persist = useRef(false);

  // Read after mount so server and first client render agree.
  useEffect(() => {
    const stored = readStorageJson(key, isStoredPrefs);
    persist.current = stored !== null && stored !== undefined;
    setPrefs(stored ?? DEFAULT_PREFS);
    setLoaded(true);
  }, [key]);

  const hasAutoHide = autoHide !== undefined && Object.keys(autoHide).length > 0;
  useEffect(() => {
    if (!hasAutoHide) return undefined;
    const update = () => setWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [hasAutoHide]);

  useEffect(() => {
    if (!loaded || !persist.current) return;
    const { shown, ...rest } = prefs;
    writeStorage(key, JSON.stringify(shown?.length ? prefs : rest));
  }, [key, loaded, prefs]);

  const autoHidden = useMemo(
    () => Object.entries(autoHide ?? {}).filter(([, minWidth]) => width < minWidth).map(([column]) => column),
    [autoHide, width],
  );

  const hiddenColumns = useMemo(() => {
    const shown = prefs.shown ?? [];
    const auto = autoHidden.filter((column) => !shown.includes(column) && !prefs.hidden.includes(column));
    return auto.length ? [...prefs.hidden, ...auto] : prefs.hidden;
  }, [autoHidden, prefs.hidden, prefs.shown]);

  const toggleColumn = useCallback(
    (column: string) => {
      persist.current = true;
      const isAuto = autoHidden.includes(column);
      setPrefs((p) => {
        const shown = p.shown ?? [];
        const hiddenNow = p.hidden.includes(column) || (isAuto && !shown.includes(column));
        return hiddenNow
          ? {
              ...p,
              hidden: p.hidden.filter((k) => k !== column),
              shown: isAuto && !shown.includes(column) ? [...shown, column] : shown,
            }
          : { ...p, hidden: [...p.hidden, column], shown: shown.filter((k) => k !== column) };
      });
    },
    [autoHidden],
  );

  const setDensity = useCallback((density: TableDensity) => {
    persist.current = true;
    setPrefs((p) => ({ ...p, density }));
  }, []);

  const visibleColumns = useCallback(
    <T extends { key: string }>(columns: T[]): T[] => columns.filter((c) => !hiddenColumns.includes(c.key)),
    [hiddenColumns],
  );

  return useMemo(
    () => ({ hiddenColumns, density: prefs.density, toggleColumn, setDensity, visibleColumns }),
    [hiddenColumns, prefs.density, toggleColumn, setDensity, visibleColumns],
  );
}
