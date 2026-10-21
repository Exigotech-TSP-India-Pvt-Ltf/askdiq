/**
 * Upload dialog Source URL (OR-7) and VA-51: the field appears after the file
 * check, pre-fills from the file's metadata, mirrors the server rules (https,
 * allow-list from GET /knowledge/settings) and blocks "Ingest document" while
 * a required URL is missing or invalid.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { UploadDialog } from "@/components/documents/UploadDialog";
import type { UploadResult } from "@/lib/documents-api";
import type { KnowledgeSettingsRead } from "@/lib/settings-api";
import { mockFetch, renderPage, type Route } from "../helpers/entity-harness";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

function settings(overrides: Partial<KnowledgeSettingsRead> = {}): KnowledgeSettingsRead {
  return {
    allowed_hosts: ["*.deployiq.com.au"],
    require_source_url: true,
    version: 1,
    updated_at: null,
    updated_by: null,
    max_hosts: 50,
    max_host_length: 253,
    ...overrides,
  };
}

function result(dryRun: boolean, overrides: Partial<UploadResult> = {}): UploadResult {
  return {
    document_id: dryRun ? "" : "doc-new",
    source_name: "guide.md",
    status: "new",
    content_hash: "a".repeat(64),
    chunk_count: 2,
    chunks_added: 2,
    chunks_kept: 0,
    chunks_removed: 0,
    embedding_tokens: 10,
    embedding_cost_usd: 0,
    warnings: [],
    quarantined: false,
    dry_run: dryRun,
    detected_source_url: null,
    source_url: null,
    ...overrides,
  };
}

let ingests: { dryRun: boolean; sourceUrl: string | null }[];

function routes(knowledge: KnowledgeSettingsRead, preview: Partial<UploadResult> = {}, reject?: { code: string; detail: string }): Route[] {
  return [
    ["GET", /^\/knowledge\/settings$/, () => ({ body: knowledge })],
    [
      "POST",
      /^\/ingest\/file$/,
      (_url, init) => {
        const form = init?.body as FormData;
        const dryRun = form.get("dry_run") === "true";
        const url = form.get("source_url");
        ingests.push({ dryRun, sourceUrl: typeof url === "string" ? url : null });
        if (!dryRun && reject) return { status: 422, body: { detail: reject.detail, code: reject.code } };
        return { body: result(dryRun, dryRun ? preview : {}) };
      },
    ],
  ];
}

beforeEach(() => {
  ingests = [];
});
afterEach(() => vi.unstubAllGlobals());

async function openWithFile() {
  const onUploaded = vi.fn();
  renderPage(<UploadDialog open token="t" onClose={vi.fn()} onUploaded={onUploaded} />);
  const dialog = await screen.findByRole("dialog", { name: "Upload document" });
  // VA-51: the primary action reads as unavailable before a file passed the check.
  const ingest = within(dialog).getByRole("button", { name: "Ingest document" });
  expect(ingest).toBeDisabled();
  expect(ingest.className).toContain("disabled:bg-surface-muted");
  fireEvent.change(screen.getByTestId("document-file-input"), { target: { files: [new File(["# Guide"], "guide.md")] } });
  await within(dialog).findByText(/New document: 2 chunks will be embedded\./);
  return { dialog, ingest, onUploaded };
}

describe("UploadDialog Source URL", () => {
  it("requires a valid https URL on the allow-list before ingesting (require_source_url on)", async () => {
    mockFetch(routes(settings()));
    const { dialog, ingest, onUploaded } = await openWithFile();
    const field = within(dialog).getByLabelText(/Source URL/);
    expect(within(dialog).getByText("(required)")).toBeInTheDocument();
    await waitFor(() => expect(field).toHaveAttribute("aria-required", "true"));
    expect(ingest).toBeDisabled();

    fireEvent.blur(field);
    expect(within(dialog).getByText("A source URL is required.")).toBeInTheDocument();

    fireEvent.change(field, { target: { value: "http://docs.deployiq.com.au/guide" } });
    expect(within(dialog).getByText("Only https:// links are allowed.")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(field, { target: { value: "https://github.com/exigo/guide" } });
    expect(within(dialog).getByText(/github\.com is not on the allowed domain list/)).toBeInTheDocument();
    expect(ingest).toBeDisabled();
    expect(within(dialog).getByText(/allowed domains: \*\.deployiq\.com\.au/)).toBeInTheDocument();

    fireEvent.change(field, { target: { value: "https://docs.deployiq.com.au/guide" } });
    expect(ingest).toBeEnabled();
    fireEvent.click(ingest);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests).toEqual([
      { dryRun: true, sourceUrl: null },
      { dryRun: false, sourceUrl: "https://docs.deployiq.com.au/guide" },
    ]);
  });

  it("pre-fills the field from the file's metadata after the check", async () => {
    mockFetch(routes(settings(), { detected_source_url: "https://help.deployiq.com.au/front-matter" }));
    const { dialog, ingest } = await openWithFile();
    expect(within(dialog).getByLabelText(/Source URL/)).toHaveValue("https://help.deployiq.com.au/front-matter");
    await waitFor(() => expect(ingest).toBeEnabled());
  });

  it("is optional when the setting is off, but still validated when filled", async () => {
    mockFetch(routes(settings({ require_source_url: false, allowed_hosts: [] })));
    const { dialog, ingest } = await openWithFile();
    expect(within(dialog).getByText("(optional)")).toBeInTheDocument();
    await waitFor(() => expect(ingest).toBeEnabled());
    const field = within(dialog).getByLabelText(/Source URL/);
    fireEvent.change(field, { target: { value: "javascript:alert(1)" } });
    expect(ingest).toBeDisabled();
    fireEvent.change(field, { target: { value: "https://anything.example.org/page" } });
    expect(ingest).toBeEnabled();
  });

  it("shows the server 422 next to the field", async () => {
    mockFetch(routes(settings({ require_source_url: false, allowed_hosts: [] }), {}, { code: "source_url_required", detail: "A source URL is required for new documents." }));
    const { dialog, ingest } = await openWithFile();
    await waitFor(() => expect(ingest).toBeEnabled());
    fireEvent.click(ingest);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("A source URL is required for new documents.");
    expect(within(dialog).getByLabelText(/Source URL/)).toHaveAttribute("aria-invalid", "true");
  });
});
