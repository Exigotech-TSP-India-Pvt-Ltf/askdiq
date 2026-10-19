/** Evaluation (Live) takes the global date range: the report request carries it. */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { waitFor } from "@testing-library/react";
import EvaluationLivePage from "@/app/(app)/evaluation-live/page";
import { api } from "@/lib/api";
import { presetRange, rangeUtcDays } from "@/lib/range-context";
import { renderPage } from "../../helpers/entity-harness";

vi.mock("next/navigation", async () => (await import("../../helpers/router-mock")).navigationMock());

afterEach(() => vi.restoreAllMocks());

const REPORT = { pass_rate: 0.9, n_cases: 0, cases: [], failed_cases: [], by_case_type: {}, operational: {} };

describe("Evaluation date range", () => {
  it("sends a custom range as from / to days", async () => {
    const spy = vi.spyOn(api, "evaluationReportLive").mockResolvedValue(REPORT);
    renderPage(<EvaluationLivePage />, { url: "/admin/evaluation-live?from=2026-10-01&to=2026-10-05" });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls.at(-1)?.[4]).toEqual({ from: "2026-10-01", to: "2026-10-05" });
  });

  it("sends a preset range as its UTC days", async () => {
    const spy = vi.spyOn(api, "evaluationReportLive").mockResolvedValue(REPORT);
    renderPage(<EvaluationLivePage />, { url: "/admin/evaluation-live?range=30d" });
    const days = rangeUtcDays(presetRange("30d"));
    await waitFor(() => expect(spy.mock.calls.at(-1)?.[4]).toEqual(days));
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
