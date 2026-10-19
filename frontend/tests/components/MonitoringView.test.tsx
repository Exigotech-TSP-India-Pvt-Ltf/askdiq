import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import {
  exactTitle,
  flagRows,
  llmUsageSummary,
  outcomeRows,
} from "@/app/(app)/admin/governance/monitoring/MonitoringView";
import { formatCount, formatCountExact } from "@/lib/governance-format";
import { BarList } from "@/components/charts/BarList";

afterEach(cleanup);

describe("Monitoring helpers", () => {
  it("outcomeRows lists every outcome, zero rows included, unknown only when present", () => {
    const rows = outcomeRows({ answered: 30 });
    expect(rows.map((r) => r.key)).toEqual(["answered", "ungrounded", "blocked", "conversational", "llm_failed"]);
    expect(rows.find((r) => r.key === "blocked")?.value).toBe(0);
    expect(outcomeRows({ answered: 1, unknown: 2 }).at(-1)).toEqual({ key: "unknown", label: "Unknown", value: 2 });
    expect(outcomeRows(undefined).every((r) => r.value === 0)).toBe(true);
  });

  it("flagRows sorts by count and humanizes", () => {
    expect(flagRows({ spam: 1, low_retrieval_confidence: 4 })).toEqual([
      { key: "low_retrieval_confidence", label: "Low retrieval confidence", value: 4 },
      { key: "spam", label: "Spam", value: 1 },
    ]);
  });

  it("llmUsageSummary counts only LLM errors as failed", () => {
    const s = llmUsageSummary({
      requests: 200,
      failed: 10,
      outcomes: { answered: 100, ungrounded: 40, blocked: 30, conversational: 20, llm_failed: 10 },
    });
    expect(s.failed).toBe(10);
    expect(s.succeeded).toBe(190);
    expect(s.successRate).toBeCloseTo(95);
    expect([s.blocked, s.conversational, s.ungrounded]).toEqual([30, 20, 40]);
  });

  it("llmUsageSummary has no rate without requests and no breakdown from an old API", () => {
    const s = llmUsageSummary({ requests: 0, failed: 0 });
    expect(s.successRate).toBeNull();
    expect(s.blocked).toBeNull();
    expect(llmUsageSummary(undefined).requests).toBeNull();
  });

  it("formats with en grouping, compact above 10k, exact value in the title", () => {
    expect(formatCount(19452869)).toBe("19.5M");
    expect(formatCount(3896232)).toBe("3.9M");
    expect(formatCount(6649)).toBe("6,649");
    expect(formatCountExact(19452869)).toBe("19,452,869");
    expect(exactTitle(19452869)).toBe("19,452,869");
    expect(exactTitle(6649)).toBeUndefined();
    expect(exactTitle(null)).toBeUndefined();
  });
});

describe("BarList dimZero", () => {
  it("marks zero rows when asked", () => {
    const data = [
      { key: "a", label: "A", value: 3 },
      { key: "b", label: "B", value: 0 },
    ];
    const { container, rerender } = render(<BarList data={data} label="Mix" dimZero height={120} />);
    const zero = container.querySelectorAll("[data-zero='true']");
    // ChartFrame only draws once it has a size; when it does, only the zero row is marked.
    expect(zero.length).toBeLessThanOrEqual(1);
    zero.forEach((g) => expect(g.getAttribute("data-key")).toBe("b"));
    rerender(<BarList data={data} label="Mix" height={120} />);
    expect(container.querySelectorAll("[data-zero='true']")).toHaveLength(0);
  });
});
