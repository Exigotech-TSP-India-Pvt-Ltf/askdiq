"use client";

import type React from "react";
import { useSyncExternalStore } from "react";
import { chartTheme, type ChartTheme, type ThemeName } from "@/lib/theme-tokens";

/**
 * T1 fix: charts (Recharts props, inline SVG) cannot use Tailwind `dark:`
 * classes, so they read colours from `chartTheme` through this hook. It
 * observes the `dark` class on <html> (set by ThemeProvider and the pre-paint
 * script), so every series, axis, grid and tooltip re-renders instantly when
 * the theme flips, in this tab or another one.
 */
function subscribe(onChange: () => void): () => void {
  if (typeof MutationObserver === "undefined" || typeof document === "undefined") {
    return () => {};
  }
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

function getSnapshot(): ThemeName {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function getServerSnapshot(): ThemeName {
  return "light";
}

export function useResolvedThemeClass(): ThemeName {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useChartTheme(): ChartTheme {
  return chartTheme[useResolvedThemeClass()];
}

/** Recharts `<Tooltip contentStyle>` built from the chart theme (card look in both themes). */
export function chartTooltipStyle(ct: ChartTheme): React.CSSProperties {
  return {
    borderRadius: 12,
    border: `1px solid ${ct.tooltip.border}`,
    backgroundColor: ct.tooltip.background,
    color: ct.tooltip.text,
    boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.1)",
    fontSize: 12,
  };
}

/** Extra px a Recharts axis needs for a title (add to the YAxis `width` / chart bottom margin). */
export const RECHARTS_AXIS_TITLE_SPACE = 16;

/**
 * Recharts axis title (XAxis / YAxis `label` prop) styled like the chart kit:
 * 11 px, axis colour, y title rotated on the left. Give the YAxis
 * `RECHARTS_AXIS_TITLE_SPACE` more width, and the chart that much more bottom
 * margin for an x title.
 */
export function rechartsAxisLabel(ct: ChartTheme, value: string, axis: "x" | "y") {
  const style = { fontSize: 11, fontWeight: 500, fill: ct.axis, textAnchor: "middle" as const };
  return axis === "x"
    ? { value, position: "insideBottom" as const, offset: -RECHARTS_AXIS_TITLE_SPACE + 4, style }
    : { value, angle: -90, position: "insideLeft" as const, offset: 4, style };
}
