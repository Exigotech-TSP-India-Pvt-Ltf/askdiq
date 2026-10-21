/** Evaluation (Live) takes the global date range: the report request carries it. */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import EvaluationLivePage from "@/app/(app)/evaluation-live/page";
import { api } from "@/lib/api";
import { presetRange, rangeUtcDays } from "@/lib/range-context";
import { renderPage } from "../../helpers/entity-harness";

vi.mock("next/navigation", async () => (await import("../../helpers/router-mock")).navigationMock());

afterEach(() => vi.restoreAllMocks());

const REPORT = { pass_rate: 0.9, n_cases: 0, outcomes: { passed: 0, failed: 0 }, by_case_type: {}, operational: {} };

/** `[view, URLSearchParams]` of the page's summary reads. */
function summaryCalls(spy: { mock: { calls: unknown[][] } }) {
  return spy.mock.calls
    .filter((c) => c[1] === "summary")
    .map((c) => new URLSearchParams(String(c[2])));
}

describe("Evaluation header actions", () => {
  it("Compare with benchmark is a labelled toggle on the tab row, before the date filter (OR-2)", async () => {
    vi.spyOn(api, "evaluationLiveView").mockResolvedValue(REPORT);
    renderPage(<EvaluationLivePage />, { url: "/admin/evaluation-live" });
    const compare = await screen.findByRole("button", { name: /compare with benchmark/i });
    expect(compare).toHaveTextContent("Compare with benchmark");
    expect(compare).toHaveAttribute("aria-pressed", "false");
    const cluster = compare.closest("[data-toolbar-actions]");
    expect(cluster).toContainElement(screen.getByRole("button", { name: /date range: last 7 days/i }));
    expect(compare.closest("[data-page-toolbar]")).toContainElement(screen.getByRole("tablist", { name: "Live evaluation domains" }));

    fireEvent.click(compare);
    expect(compare).toHaveAttribute("aria-pressed", "true");
    expect(compare.parentElement?.querySelector("[data-tooltip]")).toHaveTextContent("Exit comparison");
  });
});

describe("Evaluation date range", () => {
  it("sends a custom range as from / to days", async () => {
    const spy = vi.spyOn(api, "evaluationLiveView").mockResolvedValue(REPORT);
    renderPage(<EvaluationLivePage />, { url: "/admin/evaluation-live?from=2026-10-01&to=2026-10-05" });
    await waitFor(() => expect(summaryCalls(spy).length).toBeGreaterThan(0));
    const qs = summaryCalls(spy).at(-1)!;
    expect([qs.get("from"), qs.get("to")]).toEqual(["2026-10-01", "2026-10-05"]);
  });

  it("sends a preset range as its UTC days", async () => {
    const spy = vi.spyOn(api, "evaluationLiveView").mockResolvedValue(REPORT);
    renderPage(<EvaluationLivePage />, { url: "/admin/evaluation-live?range=30d" });
    const days = rangeUtcDays(presetRange("30d"));
    await waitFor(() => {
      const qs = summaryCalls(spy).at(-1);
      expect(qs && { from: qs.get("from"), to: qs.get("to") }).toEqual(days);
    });
  });
});

describe("evaluationReportLive", () => {
  it("puts from / to on the query string only when given", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api.evaluationReportLive("t", "users", false, undefined, { from: "2026-10-01", to: "2026-10-05" });
    await api.evaluationReportLive("t", "users");
    const urls = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]));
    expect(urls[0]).toContain("from=2026-10-01");
    expect(urls[0]).toContain("to=2026-10-05");
    expect(urls[1]).not.toContain("from=");
    vi.unstubAllGlobals();
  });
});
