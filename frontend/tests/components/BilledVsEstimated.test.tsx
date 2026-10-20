/**
 * Azure Insights Phase 3: Usage & Cost → Overview "Billed vs estimated".
 */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import {
  BilledVsEstimatedCard,
  formatVariance,
  moneyFormat,
  unavailableMessage,
} from "@/components/admin/usage/BilledVsEstimated";
import { azureCostsKeys, type AzureCosts, type BilledDay } from "@/lib/azure-costs-api";
import { prefixesForScopes } from "@/lib/live-updates";

function day(date: string, partial: Partial<BilledDay> = {}): BilledDay {
  return {
    date,
    billed: 10,
    estimate: 9.5,
    estimate_usd: 6.2,
    variance: -0.5,
    variance_pct: -5,
    status: "estimated",
    closed: true,
    ...partial,
  };
}

function costs(partial: Partial<AzureCosts> = {}): AzureCosts {
  const totals = {
    billed: 41.2,
    estimate: 39.85,
    estimate_billed_days: 39.85,
    variance: -1.35,
    variance_pct: -3.3,
    billed_days: 18,
    final: false,
  };
  return {
    state: "ok",
    message: null,
    range: { preset: "7d", from_date: "2026-10-14", to_date: "2026-10-20" },
    currency: "AUD",
    estimate_currency: "AUD",
    repriced: true,
    currency_mismatch: false,
    estimate_note: null,
    days: [
      day("2026-10-17"),
      day("2026-10-18", { status: "final" }),
      day("2026-10-19", { billed: null, variance: null, variance_pct: null, status: "pending", closed: false }),
      day("2026-10-20", { billed: null, variance: null, variance_pct: null, status: "pending", closed: false }),
    ],
    totals,
    month_to_date: { ...totals, month: "2026-10" },
    billed_through: "2026-10-18",
    drift_threshold_pct: 15,
    drift_min: 1,
    lag_note: "Azure billed cost lags 8-72 h and can be rerated until the month closes.",
    last_success_at: "2026-10-19T16:02:00Z",
    next_run_at: "2026-10-20T16:00:00Z",
    as_of: "2026-10-20T08:00:00Z",
    ...partial,
  };
}

const noop = vi.fn();

describe("BilledVsEstimatedCard", () => {
  it("shows the month-to-date KPIs, the lag legend and the estimated badge", () => {
    render(<BilledVsEstimatedCard costs={costs()} isLoading={false} error={null} onRetry={noop} />);
    expect(screen.getByText("Billed vs estimated")).toBeInTheDocument();
    expect(screen.getByText("Billed (month to date)")).toBeInTheDocument();
    expect(screen.getByText(moneyFormat("AUD")(41.2))).toBeInTheDocument();
    expect(screen.getByText(moneyFormat("AUD")(39.85))).toBeInTheDocument();
    expect(screen.getByText("−3.3%")).toBeInTheDocument();
    expect(screen.getByText(/within 15%/)).toBeInTheDocument();
    expect(screen.getByTestId("billed-status-badge")).toHaveTextContent("Estimated · lags 8–72 h");
    expect(screen.getByTestId("billed-legend")).toHaveTextContent("Azure billed (lags 8–72 h)");
    expect(screen.getByText(/re-priced with Azure retail prices in AUD/)).toBeInTheDocument();
  });

  it("draws not-yet-billed days as dashed placeholders", () => {
    const { container } = render(
      <BilledVsEstimatedCard costs={costs()} isLoading={false} error={null} onRetry={noop} />,
    );
    const pending = container.querySelectorAll('[data-testid="billed-pending"]');
    expect(pending).toHaveLength(2);
    pending.forEach((el) => expect(el).toHaveAttribute("stroke-dasharray", "3 3"));
  });

  it("shows Final when every day in the range is final", () => {
    const final = costs();
    final.totals = { ...final.totals, final: true };
    render(<BilledVsEstimatedCard costs={final} isLoading={false} error={null} onRetry={noop} />);
    expect(screen.getByTestId("billed-status-badge")).toHaveTextContent("Final");
  });

  it("explains Not connected and Unsupported subscription states", () => {
    const { rerender } = render(
      <BilledVsEstimatedCard costs={costs({ state: "not_connected" })} isLoading={false} error={null} onRetry={noop} />,
    );
    expect(screen.getByText(/Not connected/)).toBeInTheDocument();
    expect(screen.queryByTestId("billed-vs-estimated")).not.toBeInTheDocument();
    rerender(
      <BilledVsEstimatedCard
        costs={costs({ state: "unsupported", message: "Cost Management is not available for this subscription type." })}
        isLoading={false}
        error={null}
        onRetry={noop}
      />,
    );
    expect(screen.getByText(/Unsupported subscription/)).toBeInTheDocument();
  });

  it("shows both currencies without a variance when they differ", () => {
    const mismatch = costs({ estimate_currency: "USD", repriced: false, currency_mismatch: true });
    mismatch.month_to_date = { ...mismatch.month_to_date, variance_pct: null, variance: null };
    render(<BilledVsEstimatedCard costs={mismatch} isLoading={false} error={null} onRetry={noop} />);
    expect(screen.getByText("currency differs")).toBeInTheDocument();
    expect(screen.getByText(/no FX conversion/)).toBeInTheDocument();
  });

  it("flags variance above the drift threshold", () => {
    const drift = costs();
    drift.month_to_date = { ...drift.month_to_date, variance_pct: 22.4 };
    render(<BilledVsEstimatedCard costs={drift} isLoading={false} error={null} onRetry={noop} />);
    expect(screen.getByText("+22.4%")).toBeInTheDocument();
    expect(screen.getByText(/above 15%/)).toBeInTheDocument();
  });
});

describe("billed vs estimated helpers", () => {
  it("formats variance with a sign", () => {
    expect(formatVariance(null)).toBe("—");
    expect(formatVariance(0)).toBe("0.0%");
    expect(formatVariance(-3.28)).toBe("−3.3%");
  });

  it("tolerates unknown currency codes", () => {
    expect(moneyFormat("XXX")(1)).toContain("1");
  });

  it("waits for the first poll before showing numbers", () => {
    expect(unavailableMessage(costs({ state: "never_run", billed_through: null }))).toMatch(/first daily/);
    expect(unavailableMessage(costs())).toBeNull();
  });

  it("refreshes on the azure.* live scope", () => {
    const [prefix] = azureCostsKeys.costs({ from: "2026-10-14", to: "2026-10-20" });
    expect(prefixesForScopes(["azure.costs.billed"])).toContain("admin/azure");
    expect(prefix.startsWith("admin/azure")).toBe(true);
  });
});
