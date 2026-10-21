import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ModelsTab } from "@/components/admin/settings/ModelsTab";
import { driftedPrices, officialUpdate } from "@/components/admin/settings/OfficialPrices";
import { hasDrift, priceDrift, type OfficialPrices } from "@/lib/azure-api";
import type { ModelSettingsRead } from "@/lib/settings-api";
import { Providers, installFetch, json, problem, resetSettingsState } from "./harness";

const ENV = {
  llm_deployment: "gpt-5-mini",
  embedding_model: "text-embedding-3-small",
  price_prompt_per_million: 0.25,
  price_cached_per_million: 0.125,
  price_output_per_million: 2,
  price_embed_per_million: 0.02,
};

function models(overrides: Partial<ModelSettingsRead> = {}): ModelSettingsRead {
  return {
    ...ENV,
    embedding_dim: 1536,
    defaults: ENV,
    overridden: [],
    version: 2,
    updated_at: null,
    updated_by: null,
    price_source: null,
    prices_synced_at: null,
    prices_effective_date: null,
    ...overrides,
  };
}

/** The live Retail Prices API values for australiaeast (2026-10-20). */
function official(overrides: Partial<OfficialPrices> = {}): OfficialPrices {
  return {
    source: "azure_retail",
    region: "australiaeast",
    deployment_type: "Global",
    local_currency: "AUD",
    synced_at: "2026-10-20T10:00:00Z",
    effective_date: "2025-08-01",
    prices: [
      { role: "prompt", field: "price_prompt_per_million", model_key: "gpt-5-mini", meter_name: "GPT 5 Mini Inpt Glbl 1M Tokens", found: true, usd_per_million: 0.25, local_per_million: 0.3562, effective_date: "2025-08-01" },
      { role: "cached", field: "price_cached_per_million", model_key: "gpt-5-mini", meter_name: "GPT 5 Mini cchd Inpt Glbl 1M Tokens", found: true, usd_per_million: 0.025, local_per_million: 0.0356, effective_date: "2025-08-01" },
      { role: "output", field: "price_output_per_million", model_key: "gpt-5-mini", meter_name: "GPT 5 Mini outpt Glbl 1M Tokens", found: true, usd_per_million: 2, local_per_million: 2.8492, effective_date: "2025-08-01" },
      { role: "embed", field: "price_embed_per_million", model_key: "unknown-embed", meter_name: null, found: false, usd_per_million: null, local_per_million: null, effective_date: null },
    ],
    ...overrides,
  };
}

beforeEach(resetSettingsState);
afterEach(() => {
  vi.unstubAllGlobals();
  resetSettingsState();
});

function renderTab() {
  return render(
    <Providers>
      <ModelsTab />
    </Providers>,
  );
}

describe("official price helpers", () => {
  it("flags drift above 1% only", () => {
    expect(priceDrift(0.25, 0.25)).toBe(0);
    expect(hasDrift(0.2524, 0.25)).toBe(false);
    expect(hasDrift(0.126, 0.125)).toBe(false);
    expect(hasDrift(0.125, 0.025)).toBe(true);
    expect(hasDrift(1, null)).toBe(false);
  });

  it("builds the confirm body from found prices only", () => {
    expect(officialUpdate(2, official())).toEqual({
      version: 2,
      price_source: "azure_retail",
      price_prompt_per_million: 0.25,
      price_cached_per_million: 0.025,
      price_output_per_million: 2,
    });
    expect(officialUpdate(2, official({ prices: [] }))).toBeNull();
    expect(driftedPrices(models(), official()).map((p) => p.role)).toEqual(["cached"]);
  });
});

describe("Settings → Sync official prices", { timeout: 15_000 }, () => {
  it("warns about drift from the stored official prices without calling Azure", async () => {
    const { calls } = installFetch(({ method, url }) => {
      if (method === "GET" && url.endsWith("/admin/models/prices/official")) return json(official());
      if (method === "GET") return json(models());
      return undefined;
    });
    renderTab();
    expect(await screen.findByText(/differ from the official Azure retail price/)).toHaveTextContent(
      "LLM cached input $0.125 vs $0.025",
    );
    expect(screen.getByText(/Source: the server defaults/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("previews official prices, then saves them with Azure retail provenance", async () => {
    const saved = models({
      price_cached_per_million: 0.025,
      version: 3,
      price_source: "azure_retail",
      prices_effective_date: "2025-08-01",
      prices_synced_at: new Date().toISOString(),
      overridden: ["price_prompt_per_million", "price_cached_per_million", "price_output_per_million"],
    });
    const { calls } = installFetch(({ method, url }) => {
      if (method === "GET" && url.endsWith("/admin/models/prices/official")) return json(official({ synced_at: null }));
      if (method === "POST" && url.endsWith("/admin/models/prices/sync")) return json(official());
      if (method === "PUT") return json(saved);
      if (method === "GET") return json(models());
      return undefined;
    });
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /sync official prices/i }));
    const preview = await screen.findByRole("region", { name: "Official price preview" });
    expect(within(preview).getByText("No official price found")).toBeInTheDocument();
    expect(within(preview).getByText("0.3562 AUD")).toBeInTheDocument();
    // Previewing changes nothing.
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    fireEvent.click(within(preview).getByRole("button", { name: /use official prices/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      version: 2,
      price_source: "azure_retail",
      price_prompt_per_million: 0.25,
      price_cached_per_million: 0.025,
      price_output_per_million: 2,
    });
    expect(await screen.findByText(/Source: Azure retail, effective 1 Aug 2025/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("region", { name: "Official price preview" })).not.toBeInTheDocument());
  });

  it("shows the reason when the sync fails", async () => {
    installFetch(({ method, url }) => {
      if (method === "GET" && url.endsWith("/admin/models/prices/official")) return json(official());
      if (method === "POST") return problem(502, "Azure Retail Prices API failed (http_500).");
      if (method === "GET") return json(models());
      return undefined;
    });
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /sync official prices/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Azure Retail Prices API failed");
  });
});
