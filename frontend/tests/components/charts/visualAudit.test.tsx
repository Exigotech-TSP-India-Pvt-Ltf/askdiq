/**
 * Visual audit (plan 9.16) logic fixes: integer and width-based axis ticks,
 * single-point sparklines, the funnel baseline, compare drift, the forecast
 * budget line, treemap labels, Sankey label spacing and small helpers.
 */
import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { axisTicks, integerTicks, isIntegerSeries, textWidth } from "@/components/charts/primitives";
import { xTickIndices, X_LABEL_GAP } from "@/components/charts/cartesian";
import { Sparkline } from "@/components/charts/Sparkline";
import { Funnel } from "@/components/charts/Funnel";
import { StackedArea } from "@/components/charts/StackedArea";
import { tileLabel } from "@/components/charts/heavy/TreemapImpl";
import { buildDriftRows, formatCompareDiff, EvaluationCompareMatrix } from "@/components/admin/EvaluationCompareMatrix";
import { showBudgetLine } from "@/components/admin/usage/CostInsights";
import { spreadLabels } from "@/components/admin/analytics/FlowSankey";
import { dropoffSummary } from "@/components/admin/analytics/DropoffChart";
import { errorRateTone } from "@/app/(app)/admin/governance/overall/OverallView";
import type { HealthSignal } from "@/lib/governance-api";

const yTickTexts = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("svg text[text-anchor='end']")).map((t) => t.textContent ?? "");

describe("integer axis ticks (VA-18)", () => {
  it("never produces fractional ticks for counts", () => {
    expect(integerTicks(5)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(integerTicks(2.5)).toEqual([0, 1, 2]);
    expect(integerTicks(250)).toEqual([0, 50, 100, 150, 200, 250]);
    expect(integerTicks(200)).toEqual([0, 50, 100, 150, 200]);
    expect(integerTicks(20)).toEqual([0, 5, 10, 15, 20]);
    expect(integerTicks(1)).toEqual([0, 1]);
    expect(integerTicks(0)).toEqual([0]);
    for (const max of [1, 2, 3, 5, 7, 10, 25, 62.5, 133, 250, 1000, 12345]) {
      const t = integerTicks(max);
      expect(t.every(Number.isInteger), `max ${max}`).toBe(true);
      expect(t.length).toBeLessThanOrEqual(6);
      expect(t[t.length - 1]).toBeLessThanOrEqual(max);
    }
  });

  it("detects count series and keeps even steps for others", () => {
    expect(isIntegerSeries([0, 3, 12])).toBe(true);
    expect(isIntegerSeries([0.5, 3])).toBe(false);
    expect(axisTicks(1, false)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(axisTicks(5, true)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("a stacked count chart labels whole numbers only", () => {
    const data = [
      { day: "15 Oct", up: 1, down: 0 },
      { day: "16 Oct", up: 3, down: 1 },
    ];
    const { container } = render(
      <StackedArea data={data} xKey="day" series={[{ key: "up", label: "Up" }, { key: "down", label: "Down" }]} />,
    );
    const ticks = yTickTexts(container);
    expect(ticks.length).toBeGreaterThan(1);
    for (const t of ticks) expect(t).toMatch(/^\d+$/);
  });
});

describe("x label density follows the width (VA-7, VA-8)", () => {
  const w = textWidth("15 Oct");

  it("thins labels so neighbours never overlap", () => {
    const narrow = xTickIndices(7, 32, w);
    const wide = xTickIndices(7, 120, w);
    expect(wide).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(narrow.length).toBeLessThan(7);
    for (let i = 1; i < narrow.length; i += 1) {
      expect((narrow[i] - narrow[i - 1]) * 32).toBeGreaterThanOrEqual(w + X_LABEL_GAP);
    }
  });

  it("keeps the last label without crowding the previous one", () => {
    // 14 days, every 3rd label fits: 0,3,6,9,12 then 13 would sit one slot from 12.
    const shown = xTickIndices(14, 20, 40);
    expect(shown[0]).toBe(0);
    expect(shown[shown.length - 1]).toBe(13);
    expect(shown).not.toContain(12);
  });

  it("honours an optional cap and edge cases", () => {
    expect(xTickIndices(30, 100, 10, 7).length).toBeLessThanOrEqual(8);
    expect(xTickIndices(1, 0, 40)).toEqual([0]);
    expect(xTickIndices(0, 10, 40)).toEqual([]);
  });
});

describe("sparkline with one point (VA-16)", () => {
  it("shows a 'not enough data' baseline instead of a lone dot", () => {
    const { container } = render(<Sparkline data={[{ label: "21 Oct", value: 4 }]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Not enough data for a trend");
    expect(container.querySelector("circle")).toBeNull();
  });

  it("still draws two or more points", () => {
    render(
      <Sparkline
        data={[
          { label: "a", value: 1 },
          { label: "b", value: 2 },
        ]}
      />,
    );
    expect(screen.getByTestId("spark-line")).toBeInTheDocument();
  });
});

describe("funnel as aligned bars (VA-45)", () => {
  it("starts every bar on one baseline and omits empty stages", () => {
    const { container } = render(
      <Funnel
        data={[
          { label: "Signed in", value: 3 },
          { label: "Requested", value: 0 },
          { label: "Approved", value: 0 },
          { label: "Active", value: 3 },
        ]}
      />,
    );
    const bars = Array.from(container.querySelectorAll("[data-testid='funnel-bar']"));
    expect(bars).toHaveLength(2);
    expect(new Set(bars.map((b) => b.getAttribute("x"))).size).toBe(1);
    expect(container.querySelector("path")).toBeNull();
  });
});

describe("compare drift (VA-12)", () => {
  const deltas = {
    pass_rate: { benchmark: 0.938, live: 0.818, diff: -0.12 },
    faithfulness: { benchmark: 5.0, live: 3.89, diff: -1.11 },
    retrieval_recall: { benchmark: 0.985, live: null, diff: null },
    citation_precision: { benchmark: 0.439, live: 0.702, diff: 0.263 },
  };

  it("derives sign, wording and direction from the KPI numbers", () => {
    const rows = buildDriftRows(deltas);
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(byKey.pass_rate).toMatchObject({ direction: "worse", value: "-12.0 pp" });
    expect(byKey.pass_rate.detail).toContain("lower");
    expect(byKey.pass_rate.detail).toContain("93.8% benchmark, 81.8% live");
    expect(byKey.faithfulness).toMatchObject({ direction: "worse", value: "-1.11" });
    expect(byKey.citation_precision).toMatchObject({ direction: "better", value: "+26.3 pp" });
    expect(byKey.retrieval_recall).toMatchObject({ direction: "unknown", value: "Not measured" });
  });

  it("calls a tiny difference in line and formats signs", () => {
    const [row] = buildDriftRows({ pass_rate: { benchmark: 0.9, live: 0.902, diff: 0.002 } });
    expect(row.direction).toBe("flat");
    expect(formatCompareDiff(0.058, true)).toBe("+5.8 pp");
    expect(formatCompareDiff(-0.1, false)).toBe("-0.10");
  });

  it("renders no hardcoded drift text", () => {
    render(<EvaluationCompareMatrix comparisonData={{ deltas, dual_radar: [] }} />);
    const drift = screen.getByTestId("drift-rows");
    expect(drift).toHaveTextContent("-12.0 pp");
    expect(drift).not.toHaveTextContent("+5.8%");
    expect(screen.queryByText(/77% Faster/)).toBeNull();
    expect(screen.queryByText(/100% Compliant/)).toBeNull();
    expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
  });
});

describe("forecast budget line (VA-10)", () => {
  const rows = [
    { actual: 1, forecast: null },
    { actual: null, forecast: 3.74 },
  ];
  it("draws the budget only when it is near the cost", () => {
    expect(showBudgetLine(100, rows)).toBe(false);
    expect(showBudgetLine(10, rows)).toBe(true);
    expect(showBudgetLine(null, rows)).toBe(false);
    expect(showBudgetLine(5, [])).toBe(false);
  });
});

describe("treemap labels (VA-27)", () => {
  it("shows a label whole, ellipsised, or not at all", () => {
    expect(tileLabel("Off-scope", 200, 11)).toBe("Off-scope");
    expect(tileLabel("Responsible AI and trust", 90, 11)).toMatch(/…$/);
    expect(tileLabel("Contact and sales", 40, 11)).toBeNull();
  });

});

describe("Sankey label spacing (VA-30)", () => {
  it("pushes thin neighbouring labels apart inside the plot", () => {
    const out = spreadLabels([60, 150, 152, 154], 14, 16, 160);
    for (let i = 1; i < out.length; i += 1) expect(out[i] - out[i - 1]).toBeGreaterThanOrEqual(14);
    expect(out[out.length - 1]).toBeLessThanOrEqual(160);
    expect(out[0]).toBe(60);
  });
});

describe("small helpers", () => {
  it("drop-off summary (VA-28)", () => {
    const s = dropoffSummary(
      [
        { issue: "kb", label: "Info not in knowledge base", conversations: 8 },
        { issue: "gen", label: "Generic answer", conversations: 6 },
      ],
      20,
    );
    expect(s).toMatchObject({ dropped: 20, issues: 2, topShare: 0.4 });
    expect(s.top?.issue).toBe("kb");
  });

  it("error-rate sparkline tone follows the thresholds (VA-20)", () => {
    const signal = { value: 0, unit: "%", status: "Healthy", degraded_at: 2, incident_at: 10 } as HealthSignal;
    expect(errorRateTone(0, signal)).toBe("brand");
    expect(errorRateTone(3, signal)).toBe("warn");
    expect(errorRateTone(12, signal)).toBe("danger");
    expect(errorRateTone(null, signal)).toBe("brand");
  });
});
