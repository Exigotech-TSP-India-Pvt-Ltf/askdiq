"use client";

import { useMemo } from "react";
import { useChartTheme, useResolvedThemeClass } from "@/lib/use-chart-theme";
import { mixHex, readableOn, type ChartTheme, type InsightsOutcome } from "@/lib/theme-tokens";

export interface InsightsPalette {
  ct: ChartTheme;
  outcome: (outcome: string) => string;
  quality: (code: string, index: number) => string;
  /** Red (0 % handled) to teal (100 %), the HTML's topic scale. */
  handled: (pct: number) => string;
  /** Hour-of-day ramp; empty hours use the track colour. */
  hour: (value: number, max: number) => string;
  /** Label colour with the best contrast on a mark fill. */
  textOn: (fill: string) => string;
}

/** Analytics colours from the chart tokens; re-renders when the theme flips. */
export function useInsightsPalette(): InsightsPalette {
  const ct = useChartTheme();
  const theme = useResolvedThemeClass();
  return useMemo(() => {
    const c = ct.insights;
    return {
      ct,
      outcome: (o) => c.outcome[o as InsightsOutcome] ?? ct.categorical[3],
      quality: (code, i) => c.quality[code] ?? ct.categorical[i % ct.categorical.length],
      handled: (pct) => mixHex(c.handled.low, c.handled.high, pct / 100),
      hour: (v, max) => (v > 0 ? mixHex(c.hours.low, c.hours.high, max > 0 ? v / max : 0) : ct.track),
      textOn: (fill) => readableOn(fill, theme),
    };
  }, [ct, theme]);
}
