import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { AzureServiceBanner, ConsoleAzureBanner } from "@/components/admin/governance/HealthBanner";
import { AdvisorCard, AzureHealthCard, RegionalQuotaCard, availabilityTone } from "@/components/admin/governance/AzurePanels";
import { azureKeys, quotaLimitCaption, usesAzureLimits, type HealthBannerState } from "@/lib/azure-capacity-api";
import { prefixesForScopes } from "@/lib/live-updates";
import { queryCache, serializeKey } from "@/lib/query-cache";

const auth = vi.hoisted(() => ({ permissions: ["dashboard:read"] as string[] }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "t", permissions: auth.permissions }),
}));

afterEach(() => {
  cleanup();
  queryCache.clear();
  vi.unstubAllGlobals();
  auth.permissions = ["dashboard:read"];
});

const wrap = (ui: React.ReactElement) => render(<MotionProvider>{ui}</MotionProvider>);

function respond(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, statusText: "", json: () => Promise.resolve(body) } as Response);
}

const INCIDENT: HealthBannerState = {
  show: true,
  level: "critical",
  title: "Azure service incident: Elevated errors",
  message: "Azure OpenAI Service · Australia East · tracking id FIX1-AE1",
  href: "/admin/governance?tab=Monitoring#azure-health",
  tracking_id: "FIX1-AE1",
};

describe("Azure service banner", () => {
  it("is hidden while Azure reports the resource available", () => {
    const { container } = wrap(<AzureServiceBanner banner={{ show: false }} />);
    expect(container).toBeEmptyDOMElement();
    expect(wrap(<AzureServiceBanner banner={undefined} />).container).toBeEmptyDOMElement();
  });

  it("shows a degraded resource as a warning and an incident as critical, linking to details", () => {
    wrap(<AzureServiceBanner banner={{ ...INCIDENT, level: "warning", title: "Azure OpenAI resource degraded" }} />);
    expect(screen.getByRole("alert")).toHaveAttribute("data-azure-health", "warning");
    expect(screen.getByText("Azure OpenAI resource degraded")).toBeInTheDocument();
    cleanup();
    wrap(<AzureServiceBanner banner={INCIDENT} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveAttribute("data-azure-health", "critical");
    expect(alert).toHaveTextContent("FIX1-AE1");
    expect(screen.getByRole("link", { name: /details/i })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Monitoring#azure-health",
    );
  });

  it("console banner reads the azure.health snapshot and appears after a live refetch", async () => {
    let body: unknown = { connected: true, banner: { show: false } };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/proxy/admin/azure/health");
      return respond(body);
    });
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ConsoleAzureBanner />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // A poll found an incident: the backend announces `azure.health`.
    body = { connected: true, banner: INCIDENT };
    prefixesForScopes(["azure.health"]).forEach((p) => queryCache.invalidate(p));
    expect(await screen.findByRole("alert")).toHaveTextContent("Elevated errors");
  });

  it("console banner never fetches without dashboard:read", () => {
    auth.permissions = [];
    const fetchMock = vi.fn(() => respond({}));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = wrap(<ConsoleAzureBanner />);
    expect(container).toBeEmptyDOMElement();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("azure.* scopes refresh the admin/azure query keys", () => {
    expect(prefixesForScopes(["azure.capacity"])).toContain("admin/azure");
    expect(serializeKey(azureKeys.health()).startsWith("admin/azure")).toBe(true);
  });
});

describe("Quota gauges with real limits", () => {
  it("labels limits from Azure, else the configured defaults", () => {
    const azure = {
      chat: { tpm_limit: 250000, rpm_limit: 250, tpm_limit_source: "azure", rpm_limit_source: "azure" },
      embedding: { tpm_limit: 120000, rpm_limit: 720, tpm_limit_source: "azure", rpm_limit_source: "azure" },
    } as const;
    expect(quotaLimitCaption(azure, "tpm")).toBe("Limit from Azure · TPM chat 250K / embedding 120K");
    expect(quotaLimitCaption(azure, "rpm")).toBe("Limit from Azure · RPM chat 250 / embedding 720");
    expect(usesAzureLimits(azure)).toBe(true);
    const configured = { chat: { tpm_limit: 1000000, tpm_limit_source: "settings" } } as const;
    expect(quotaLimitCaption(configured, "tpm")).toBe("Configured limit · TPM chat 1M");
    expect(usesAzureLimits(configured)).toBe(false);
    expect(quotaLimitCaption(undefined, "tpm")).toBe("Busiest of chat and embedding");
  });
});

describe("Azure panels", () => {
  it("regional quota card shows allocation bars and deployment limits", () => {
    wrap(
      <RegionalQuotaCard
        capacity={{
          connected: true,
          fetched_at: "2026-10-20T10:00:00Z",
          deployments: [{ name: "gpt-5-mini", sku_name: "GlobalStandard", tpm_limit: 250000, rpm_limit: 250, role: "chat" }],
          regional: {
            state: "ok",
            location: "australiaeast",
            items: [{ name: "OpenAI.GlobalStandard.gpt-5-mini", label: "gpt-5-mini", current_value: 900, limit_value: 1000, pct: 90 }],
          },
        }}
      />,
    );
    const meter = screen.getByRole("meter", { name: /gpt-5-mini allocated/ });
    expect(meter).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByText("250K")).toBeInTheDocument();
  });

  it("regional quota card explains a missing optional role", () => {
    wrap(<RegionalQuotaCard capacity={{ connected: true, deployments: [], regional: { state: "forbidden", items: [] } }} />);
    expect(screen.getByText(/Cognitive Services Usages Reader/)).toBeInTheDocument();
  });

  it("shows Not connected when Azure Insights is off", () => {
    wrap(<AzureHealthCard health={{ connected: false }} />);
    expect(screen.getByTestId("azure-health-card")).toHaveTextContent(/Not connected/);
  });

  it("health card lists active incidents and status tone", () => {
    wrap(
      <AzureHealthCard
        health={{
          connected: true,
          service_health_enabled: true,
          resource: { availability_state: "Degraded", summary: "Increased latency", since: "2026-10-20T10:00:00Z", checked_at: "2026-10-20T10:10:00Z" },
          events: [{ tracking_id: "FIX1-AE1", event_type: "ServiceIssue", status: "Active", title: "Elevated errors", regions: ["Australia East"], active: true }],
        }}
      />,
    );
    expect(screen.getByText("Increased latency")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Service Health events" })).toHaveTextContent("FIX1-AE1");
    expect(availabilityTone("Unavailable")).toBe("danger");
    expect(availabilityTone("Unknown")).toBe("info");
  });

  it("advisor card lists retirements first with their date", () => {
    wrap(
      <AdvisorCard
        advisor={{
          connected: true,
          enabled: true,
          recommendations: [
            { rec_id: "a", retirement: true, retirement_date: "2027-03-31", problem: "gpt-4o is scheduled for retirement" },
            { rec_id: "b", retirement: false, problem: "Unused deployment" },
          ],
        }}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Retirement");
    expect(items[0]).toHaveTextContent("2027-03-31");
    expect(screen.getByTestId("azure-advisor-card")).toHaveTextContent("1 retirement");
  });
});
