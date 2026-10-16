/**
 * Documents inventory: every document with its formats (.docx -> .md
 * "Standardized to Markdown"), sizes, chunks, hash (copy), ingestion
 * provenance and status; URL-driven search / filters / sort; row -> detail.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import DocumentsPage from "@/app/(app)/documents/page";
import { currentParams, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import type { DocumentInventoryItem } from "@/lib/documents-api";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const FULL_HASH = "f6e5d4c3b2a10987f6e5d4c3b2a10987f6e5d4c3b2a10987f6e5d4c3b2a10987";

const inventory: DocumentInventoryItem[] = [
  {
    document_id: "doc-1",
    source_name: "essential-eight.md",
    source_type: "markdown",
    chunk_count: 14,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    converted_from: "md",
    original_filename: "essential-eight.md",
    content_hash: "a1b2c3d4e5f6",
    content_sha256: "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
    canonical_size_bytes: 4096,
    original_size_bytes: 4100,
    chunking_strategy: "metadata_aware",
    chunk_strategies: ["structure_aware"],
    ingested_at: "2026-10-02T00:00:00Z",
    ingested_by: "admin@example.com",
    status: "active",
    warnings: [],
    quarantined: false,
    labels: { corpus: "authoritative" },
  },
  {
    document_id: "doc-2",
    source_name: "security-posture.md",
    source_type: "markdown",
    chunk_count: 28,
    created_at: "2026-10-03T00:00:00Z",
    converted_from: "docx",
    standardized: true,
    converter: "mammoth+gfm",
    original_filename: "Security_Posture_Assessment.docx",
    original_sha256: "0123456789abcdef",
    content_hash: FULL_HASH.slice(0, 12),
    content_sha256: FULL_HASH,
    canonical_size_bytes: 20480,
    original_size_bytes: 61440,
    chunking_strategy: "structure_aware",
    ingested_at: "2026-10-05T00:00:00Z",
    ingested_by: "owner@example.com",
    ingest_count: 3,
    status: "active",
    warnings: ["Table formatting converted"],
    pii_redacted: ["email"],
    quarantined: false,
  },
  {
    document_id: "doc-3",
    source_name: "suspicious.md",
    source_type: "markdown",
    chunk_count: 2,
    created_at: "2026-10-04T00:00:00Z",
    converted_from: "md",
    status: "quarantined",
    quarantined: true,
    warnings: [],
  },
];

const routes: Route[] = [["GET", /^\/documents$/, () => ({ body: inventory })]];

function rows(): HTMLElement[] {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

beforeEach(() => history.reset("/documents"));
afterEach(() => vi.unstubAllGlobals());

describe("Documents inventory", () => {
  it("lists every document with formats, sizes, chunks, hash, provenance and status", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    const docx = (await screen.findByText("security-posture.md")).closest("tr") as HTMLElement;
    expect(within(docx).getByText(".docx")).toBeInTheDocument();
    expect(within(docx).getByText("Standardized to Markdown")).toBeInTheDocument();
    expect(within(docx).getByText("uploaded as Security_Posture_Assessment.docx")).toBeInTheDocument();
    expect(within(docx).getByText("20 KB")).toBeInTheDocument();
    expect(within(docx).getByText("original 60 KB")).toBeInTheDocument();
    expect(within(docx).getByText("28")).toBeInTheDocument();
    expect(within(docx).getByText("Structure aware")).toBeInTheDocument();
    expect(within(docx).getByText(FULL_HASH.slice(0, 12))).toHaveAttribute("title", FULL_HASH);
    expect(within(docx).getByRole("button", { name: "Copy content hash of security-posture.md" })).toBeInTheDocument();
    expect(within(docx).getByText(/by owner@example\.com · 3 ingests/)).toBeInTheDocument();
    expect(within(docx).getByText("1 warning")).toBeInTheDocument();
    expect(within(docx).getByText("PII redacted")).toBeInTheDocument();

    const md = screen.getByText("essential-eight.md").closest("tr") as HTMLElement;
    expect(within(md).getByText("Native Markdown")).toBeInTheDocument();
    expect(within(md).queryByText(".docx")).not.toBeInTheDocument();
    expect(within(md).getByText("corpus: authoritative")).toBeInTheDocument();

    const quarantined = screen.getByText("suspicious.md").closest("tr") as HTMLElement;
    expect(within(quarantined).getByText("Quarantined")).toBeInTheDocument();
    expect(within(quarantined).getAllByText("—").length).toBeGreaterThan(0);

    // KPIs come from the same rows (no invented numbers).
    expect(screen.getByText("Total Canonical Documents")).toBeInTheDocument();
    expect(screen.getByText("1 standardized from .docx")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("44")).toBeInTheDocument());
  });

  it("filters by original format and status from the URL", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { url: "/documents?format=docx" });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /security-posture\.md/ })).toBeInTheDocument();
    expect(screen.getAllByText("1 of 3 documents").length).toBeGreaterThan(0);

    renderPage(<DocumentsPage />, { url: "/documents?status=quarantined" });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /suspicious\.md/ })).toBeInTheDocument();
  });

  it("search matches the original file name and the hash, and writes ?q=", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /essential-eight\.md/ });
    fireEvent.change(screen.getByPlaceholderText(/search name, original file/i), { target: { value: "Posture_Assessment.docx" } });
    await waitFor(() => expect(currentParams().get("q")).toBe("Posture_Assessment.docx"));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByRole("link", { name: /essential-eight\.md/ })).not.toBeInTheDocument();

    renderPage(<DocumentsPage />, { url: `/documents?q=${FULL_HASH.slice(20, 30)}` });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /security-posture\.md/ })).toBeInTheDocument();
  });

  it("sorting by a column header updates ?sort= and the order", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /essential-eight\.md/ });
    // Default: newest ingestion first.
    expect(within(rows()[0]).getByRole("link")).toHaveAccessibleName(/security-posture\.md/);
    fireEvent.click(screen.getByRole("columnheader", { name: /Chunks/ }));
    await waitFor(() => expect(currentParams().get("sort")).toBe("chunks_desc"));
    await waitFor(() => expect(within(rows()[0]).getByRole("link")).toHaveAccessibleName(/security-posture\.md/));
    expect(within(rows()[2]).getByRole("link")).toHaveAccessibleName(/suspicious\.md/);
    fireEvent.click(screen.getByRole("columnheader", { name: /Document/ }));
    await waitFor(() => expect(currentParams().get("sort")).toBe("name_desc"));
    await waitFor(() => expect(within(rows()[0]).getByRole("link")).toHaveAccessibleName(/suspicious\.md/));
  });

  it("each row links to the document detail page", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    expect(await screen.findByRole("link", { name: /essential-eight\.md/ })).toHaveAttribute("href", "/documents/doc-1");
    fireEvent.click(rows()[0]);
    expect(history.push).toHaveBeenCalledWith("/documents/doc-2");
  });

  it("shows a plain empty state when there are no documents", async () => {
    mockFetch([["GET", /^\/documents$/, () => ({ body: [] })]]);
    renderPage(<DocumentsPage />);
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    expect(screen.getByText("Upload a .md or .docx file to add it to the knowledge base.")).toBeInTheDocument();
  });
});

describe("Documents deep link", () => {
  it("?action=upload opens the upload dialog once and clears the param", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { url: "/documents?action=upload" });
    expect(await screen.findByRole("dialog", { name: /upload/i })).toBeInTheDocument();
    await waitFor(() => expect(currentParams().get("action")).toBeNull());
  });
});
