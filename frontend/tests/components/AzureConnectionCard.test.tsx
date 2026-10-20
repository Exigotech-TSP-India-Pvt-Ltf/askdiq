import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { AzureConnectionBody, AzureConnectionCard } from "@/components/admin/governance/AzureConnectionCard";
import { prefixesForScopes } from "@/lib/live-updates";
import { azureKeys, type AzureStatus } from "@/lib/azure-api";
import { queryCache } from "@/lib/query-cache";

function status(overrides: Partial<AzureStatus> = {}): AzureStatus {
  return {
    connected: false,
    reason: "not_configured",
    region: null,
    deployment_type: "Global",
    metrics_calls_this_month: 0,
    metrics_call_cap: 100000,
    generated_at: "2026-10-20T10:00:00Z",
    sources: [
      {
        source: "retail",
        label: "Retail prices",
        scheduled: false,
        needs_credentials: false,
        interval_seconds: 604800,
        state: "never_run",
        last_run_at: null,
        last_success_at: null,
        last_error_at: null,
        last_error_code: null,
        last_error_message: null,
        consecutive_failures: 0,
        next_run_at: null,
        calls_this_month: 0,
        call_cap: null,
      },
    ],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  queryCache.clear();
});

describe("Azure connection card", () => {
  it("shows Not connected with the setup hint when disabled", () => {
    render(<AzureConnectionBody status={status()} />);
    expect(screen.getByRole("status")).toHaveTextContent(/Not connected/);
    expect(screen.getByText("Not run yet")).toBeInTheDocument();
    expect(screen.getByText("Not scheduled")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "100000");
  });

  it("shows per-source errors and the metrics call budget when connected", () => {
    const base = status().sources[0];
    render(
      <AzureConnectionBody
        status={status({
          connected: true,
          reason: null,
          region: "australiaeast",
          metrics_calls_this_month: 1234,
          sources: [
            { ...base, state: "ok", scheduled: true, last_success_at: "2026-10-20T09:00:00Z", next_run_at: "2026-10-27T00:00:00Z" },
            {
              ...base,
              source: "metrics",
              label: "Monitor metrics",
              needs_credentials: true,
              scheduled: true,
              state: "error",
              last_error_code: "AuthorizationFailed",
              last_error_message: "The client does not have authorization",
              calls_this_month: 1234,
              call_cap: 100000,
            },
          ],
        })}
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("1,234 / 100,000")).toBeInTheDocument();
    const metrics = screen.getByRole("row", { name: /Monitor metrics/ });
    expect(within(metrics).getByText("Error")).toBeInTheDocument();
    expect(within(metrics).getByText(/AuthorizationFailed: The client does not have authorization/)).toBeInTheDocument();
  });

  it("renders from the prefetched cache without a request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    queryCache.setData<AzureStatus>(azureKeys.status, status({ connected: true, reason: null, region: "australiaeast" }));
    render(<AzureConnectionCard />);
    expect(screen.getByText("Connected · australiaeast")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refetches on azure.* live-stream scopes", () => {
    expect(prefixesForScopes(["azure.status"])).toContain("admin/azure");
    expect(prefixesForScopes(["azure.retail"])).toContain("admin/azure");
    expect(azureKeys.status.startsWith("admin/azure")).toBe(true);
    expect(azureKeys.officialPrices.startsWith("admin/azure")).toBe(true);
  });
});
