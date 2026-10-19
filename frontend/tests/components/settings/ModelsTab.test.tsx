import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelsTab } from "@/components/admin/settings/ModelsTab";
import { diffModelsForm, formFromModelSettings, validateModelsForm } from "@/components/admin/settings/models-form";
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
    version: 0,
    updated_at: null,
    updated_by: null,
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

const saveButton = () => screen.getByRole("button", { name: /save changes/i });

describe("models-form", () => {
  it("shows only overridden fields and diffs to the PUT body (empty clears)", () => {
    const baseline = formFromModelSettings(
      models({ llm_deployment: "gpt-next", price_output_per_million: 4, overridden: ["llm_deployment", "price_output_per_million"] }),
    );
    expect(baseline.llm_deployment).toBe("gpt-next");
    expect(baseline.embedding_model).toBe("");
    expect(baseline.price_output_per_million).toBe("4");

    const edited = { ...baseline, llm_deployment: "", price_embed_per_million: "0.05" };
    expect(diffModelsForm(baseline, edited)).toEqual({ llm_deployment: null, price_embed_per_million: 0.05 });
  });

  it("validates deployment names and prices", () => {
    const form = formFromModelSettings(models());
    expect(validateModelsForm({ ...form, llm_deployment: "bad name" }).llm_deployment).toMatch(/letters/);
    expect(validateModelsForm({ ...form, price_prompt_per_million: "-1" }).price_prompt_per_million).toMatch(/0 to 1000/);
    expect(validateModelsForm({ ...form, price_prompt_per_million: "0.3" })).toEqual({});
  });
});

describe("Settings → Models & Pricing", { timeout: 15_000 }, () => {
  it("shows the .env values as placeholders and saves a price override", async () => {
    const { calls } = installFetch(({ method, body }) => {
      if (method === "GET") return json(models());
      if (method === "PUT") {
        return json(
          models({ price_output_per_million: 3, overridden: ["price_output_per_million"], version: 1, updated_at: new Date().toISOString(), updated_by: "a@b.c" }),
        );
      }
      return body as undefined;
    });
    renderTab();
    const output = await screen.findByLabelText("LLM output");
    expect(output).toHaveAttribute("placeholder", "$2");
    expect(screen.getByLabelText("LLM deployment")).toHaveAttribute("placeholder", "gpt-5-mini");
    expect(screen.getByText("1536")).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();

    fireEvent.change(output, { target: { value: "3" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ version: 0, price_output_per_million: 3 });
    expect(await screen.findByText(/Last changed/)).toHaveTextContent("a@b.c");
  });

  it("warns that changing the embedding model needs re-ingestion", async () => {
    installFetch(({ method }) => (method === "GET" ? json(models()) : undefined));
    renderTab();
    fireEvent.change(await screen.findByLabelText("Embedding deployment"), { target: { value: "text-embedding-3-large" } });
    expect(screen.getByText(/Re-upload your Knowledge documents/)).toBeInTheDocument();
  });

  it("shows the server's reason when a model cannot be used", async () => {
    const reason = "The LLM deployment 'nope' could not be called: DeploymentNotFound";
    installFetch(({ method }) => (method === "GET" ? json(models()) : method === "PUT" ? problem(422, reason) : undefined));
    renderTab();
    fireEvent.change(await screen.findByLabelText("LLM deployment"), { target: { value: "nope" } });
    fireEvent.click(saveButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(reason);
  });
});
