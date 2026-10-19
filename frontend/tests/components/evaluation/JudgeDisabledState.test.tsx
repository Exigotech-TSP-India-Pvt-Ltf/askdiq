import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { JudgeDisabledState, hasJudgedSamples } from "@/components/admin/evaluation/JudgeDisabledState";

/** Evaluation (Live) > Semantic Quality: empty state instead of "— / 5.0" cards when the judge is off. */
describe("hasJudgedSamples", () => {
  it("is false when the online judge scored nothing (ONLINE_EVAL_ENABLED=false)", () => {
    expect(hasJudgedSamples({ n_cases: 40, judge: { faithfulness: null, relevance: null, completeness: null, n: 0 } })).toBe(false);
  });

  it("is false for an empty report (zeros, no n) and for a missing report", () => {
    expect(hasJudgedSamples({ n_cases: 0, judge: { faithfulness: 0, relevance: 0, completeness: 0 } })).toBe(false);
    expect(hasJudgedSamples(null)).toBe(false);
    expect(hasJudgedSamples({})).toBe(false);
  });

  it("is true once at least one answer was judged", () => {
    expect(hasJudgedSamples({ n_cases: 40, judge: { faithfulness: 4.2, relevance: 4.5, completeness: 4.1, n: 12 } })).toBe(true);
    // Older servers without `n`: any positive score counts.
    expect(hasJudgedSamples({ n_cases: 40, judge: { faithfulness: 4.2 } })).toBe(true);
  });
});

describe("JudgeDisabledState", () => {
  it("explains that the LLM judge is off and how to enable it", () => {
    render(<JudgeDisabledState />);
    expect(screen.getByText("No LLM-judged answers in this range")).toBeInTheDocument();
    expect(screen.getByText("ONLINE_EVAL_ENABLED=true")).toBeInTheDocument();
    expect(screen.getByTestId("judge-disabled")).toHaveTextContent(/semantic quality and citation scoring/);
    expect(screen.queryByText(/\/ 5\.0/)).not.toBeInTheDocument();
  });
});
