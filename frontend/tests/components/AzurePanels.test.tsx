import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AzureReconciliationPanel, driftedCount, reconciliationRows } from "@/components/admin/azure/AzureReconciliationPanel";
import { AzureTokensPanel, dailyTokens, previewCost, sparkFor } from "@/components/admin/azure/AzureTokensPanel";
import { NOT_CONNECTED_MESSAGE, dayLabel, formatDrift, isNotConnected } from "@/components/admin/azure/azure-format";
import { azureKeys, TOKENS_SERIES, validSummary, type AzureMetricSeries, type AzureMetricsSummary, type ReconciliationCell, type ReconciliationRow } from "@/lib/azure-metrics-api";
import { prefixesForScopes } from "@/lib/live-updates";
import { queryCache } from "@/lib/query-cache";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "mock-token", user: null, loading: false }),
}));

const cellOf = (azure: number | null, askdiq: number | null, drift: number | null, drifted = false): ReconciliationCell => ({
  azure,
  askdiq,
  delta: azure === null ? null : azure - (askdiq ?? 0),
  drift_pct: drift,
  drifted,
});

function row(over: Partial<ReconciliationRow> & Pick<ReconciliationRow, "deployment" | "role">): ReconciliationRow {
  return {
    day: null,
    requests: cellOf(12, 12, 0),
    tokens_in: cellOf(3000, 3000, 0),
    tokens_out: cellOf(1000, 1000, 0),
    throttled: cellOf(0, 0, null),
    latency_avg_ms: cellOf(1200, 1100, 8.33),
    latency_max_ms: cellOf(2500, 1400, 44),
    ttft_avg_ms: 400,
    availability_pct: 100,
    estimated_cost_usd: null,
    not_seen_tokens: 0,
    drifted: false,
    ...over,
  };
}

const LLM_DRIFTED = row({
  deployment: "gpt-5-mini",
  role: "llm",
  requests: cellOf(12, 7, 41.67, true),
  throttled: cellOf(3, 2, 33.33, true),
  not_seen_tokens: 650,
  drifted: true,
});
const OTHER = row({
  deployment: "playground-gpt-4o",
  role: "other",
  requests: cellOf(1, null, 100),
  tokens_in: cellOf(400, null, 100),
  tokens_out: cellOf(100, null, 100),
  not_seen_tokens: 500,
});

const SUMMARY: AzureMetricsSummary = {
  connected: true,
  state: "ok",
  as_of: "2031-03-10T10:07:30Z",
  last_point_at: "2031-03-10T10:02:00Z",
  days: 7,
  drift_threshold_pct: 10,
  llm_deployment: "gpt-5-mini",
  embedding_deployment: "text-embedding-3-small",
  last_24h: [LLM_DRIFTED, OTHER],
  daily: [
    { ...LLM_DRIFTED, day: "2031-03-10", estimated_cost_usd: 0.18 },
    { ...OTHER, day: "2031-03-10" },
    row({ deployment: "gpt-5-mini", role: "llm", day: "2031-03-09", tokens_in: cellOf(5000, 0, 100, true), tokens_out: cellOf(2000, 0, 100, true), not_seen_tokens: 7000, drifted: true, estimated_cost_usd: 0.42 }),
  ],
  tokens: [
    { deployment: "gpt-5-mini", role: "llm", azure_tokens: 11000, azure_tokens_in: 8000, azure_tokens_out: 3000, askdiq_tokens: 3350, not_seen_tokens: 7650, not_seen_pct: 69.5 },
    { deployment: "playground-gpt-4o", role: "other", azure_tokens: 500, azure_tokens_in: 400, azure_tokens_out: 100, askdiq_tokens: null, not_seen_tokens: 500, not_seen_pct: 100 },
  ],
  preview_cost: { enabled: true, empty_since: null, last_seen: "2031-03-10" },
  generated_at: "2031-03-10T10:08:00Z",
};

const SERIES: AzureMetricSeries = {
  connected: true,
  as_of: "2031-03-10T10:07:30Z",
  metric: "TokenTransaction",
  deployment: "*",
  range: "7d",
  grain: "PT1H",
  since: "2031-03-03T11:00:00Z",
  until: "2031-03-10T11:00:00Z",
  series: [
    {
      deployment: "gpt-5-mini",
      status_code: "",
      points: [
        { t: "2031-03-09T09:00:00Z", total: 7000, average: null, maximum: null },
        { t: "2031-03-10T10:00:00Z", total: 4000, average: null, maximum: null },
      ],
    },
  ],
  generated_at: "2031-03-10T10:08:00Z",
};

const NOT_CONNECTED: AzureMetricsSummary = {
  ...SUMMARY,
  connected: false,
  state: "not_configured",
  as_of: null,
  last_point_at: null,
  last_24h: [row({ deployment: "gpt-5-mini", role: "llm", requests: cellOf(null, 4, null), tokens_in: cellOf(null, 10, null) })],
  daily: [],
  tokens: [],
};

function stubFetch(summary: AzureMetricsSummary): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (url: string) => {
    const body = String(url).includes("/azure/metrics/series") ? SERIES : summary;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => queryCache.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  queryCache.clear();
});

describe("Azure helpers", () => {
  it("picks rows per mode and counts drifted ones", () => {
    expect(reconciliationRows(SUMMARY, "24h")).toHaveLength(2);
    expect(reconciliationRows(SUMMARY, "daily")).toHaveLength(3);
    expect(reconciliationRows(undefined, "daily")).toEqual([]);
    expect(driftedCount(SUMMARY.daily)).toBe(2);
  });

  it("is 'not connected' only when disabled and without stored Azure data", () => {
    expect(isNotConnected(NOT_CONNECTED)).toBe(true);
    expect(isNotConnected(SUMMARY)).toBe(false);
    expect(isNotConnected({ ...SUMMARY, connected: false })).toBe(false);
    expect(isNotConnected(undefined)).toBe(false);
  });

  it("formats drift and UTC days", () => {
    expect(formatDrift(cellOf(12, 7, 41.67))).toBe("41.7%");
    expect(formatDrift(cellOf(null, 7, null))).toBe("—");
    expect(dayLabel("2031-03-10")).toBe("10 Mar");
  });

  it("splits daily Azure tokens into recorded and not seen", () => {
    expect(dailyTokens(SUMMARY)).toEqual([
      { day: "2031-03-09", label: "9 Mar", recorded: 0, notSeen: 7000 },
      { day: "2031-03-10", label: "10 Mar", recorded: 4000 - 650, notSeen: 650 + 500 },
    ]);
    expect(dailyTokens(undefined)).toEqual([]);
  });

  it("builds per-deployment sparklines and preview cost totals", () => {
    expect(sparkFor(SERIES, "GPT-5-MINI").map((p) => p.value)).toEqual([7000, 4000]);
    expect(sparkFor(SERIES, "other")).toEqual([]);
    expect(previewCost(SUMMARY).get("gpt-5-mini")).toBeCloseTo(0.6);
  });

  it("accepts only a summary-shaped payload", () => {
    expect(validSummary(SUMMARY)).toBe(SUMMARY);
    expect(validSummary({ counts: {} })).toBeUndefined();
    expect(validSummary(null)).toBeUndefined();
  });

  it("uses admin/azure query keys refreshed by the azure. live scope", () => {
    expect(azureKeys.summary()).toEqual(["admin/azure/metrics/summary", 7]);
    expect(azureKeys.series(TOKENS_SERIES)[0]).toBe("admin/azure/metrics/series");
    expect(prefixesForScopes(["azure.metrics.summary"])).toContain("admin/azure");
  });
});

describe("AzureReconciliationPanel", () => {
  it("highlights drifted rows and switches to the per-day view", async () => {
    const fetchMock = stubFetch(SUMMARY);
    render(<AzureReconciliationPanel />);
    const table = await screen.findByTestId("azure-reconciliation-table");
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/proxy/admin/azure/metrics/summary?days=7");
    const rows = within(table).getAllByTestId("azure-reconciliation-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-drifted", "true");
    expect(rows[0].className).toContain("bg-status-warn-bg");
    expect(rows[1]).toHaveAttribute("data-drifted", "false");
    expect(within(rows[1]).getByText("Not used by AskDIQ")).toBeInTheDocument();
    expect(within(rows[0]).getByTestId("cell-requests")).toHaveTextContent("AskDIQ 7");
    expect(within(rows[0]).getByTestId("cell-requests")).toHaveTextContent("41.7% drift");
    expect(screen.getByTestId("azure-drift-count")).toHaveTextContent("1 drifted");
    expect(screen.getByTestId("azure-as-of")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Per day" }));
    await waitFor(() => expect(screen.getAllByTestId("azure-reconciliation-row")).toHaveLength(3));
    expect(screen.getByText("Day (UTC)")).toBeInTheDocument();
    expect(screen.getByTestId("azure-drift-count")).toHaveTextContent("2 drifted");
  });

  it("shows the Not connected state", async () => {
    stubFetch(NOT_CONNECTED);
    render(<AzureReconciliationPanel />);
    expect(await screen.findByText(NOT_CONNECTED_MESSAGE)).toBeInTheDocument();
    expect(screen.getByTestId("azure-not-connected")).toHaveTextContent("Not connected");
    expect(screen.queryByTestId("azure-reconciliation-table")).not.toBeInTheDocument();
  });
});

describe("AzureTokensPanel", () => {
  it("lists Azure-reported tokens with the not-seen delta and Preview cost", async () => {
    stubFetch(SUMMARY);
    render(<AzureTokensPanel />);
    const rows = await screen.findAllByTestId("azure-token-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("gpt-5-mini")).toBeInTheDocument();
    expect(within(rows[0]).getByTestId("azure-not-seen")).toHaveTextContent("7,650 not seen by AskDIQ (69.5%)");
    expect(within(rows[0]).getByTestId("azure-preview-cost")).toHaveTextContent("Preview estimate");
    expect(within(rows[1]).queryByTestId("azure-preview-cost")).not.toBeInTheDocument();
    expect(screen.getByText("Recorded vs not seen")).toBeInTheDocument();
  });

  it("shows the Not connected state in both cards", async () => {
    stubFetch(NOT_CONNECTED);
    render(<AzureTokensPanel />);
    await waitFor(() => expect(screen.getAllByText(NOT_CONNECTED_MESSAGE)).toHaveLength(2));
    expect(screen.queryByTestId("azure-token-row")).not.toBeInTheDocument();
  });
});
