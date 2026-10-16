/**
 * Documents journey: upload a .docx -> preview (dry run) -> ingest -> "Converted
 * to Markdown, N chunks" -> the new document is in the inventory -> its detail
 * page shows the metadata, chunks, canonical Markdown and ingest history.
 * `fetch` is mocked; the real upload -> standardize -> chunk flow is covered by
 * the backend `tests/test_documents_inventory.py`.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import DocumentsPage from "@/app/(app)/documents/page";
import DocumentDetailPage from "@/app/(app)/documents/[id]/page";
import { callsTo, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import type { DocumentDetail, DocumentInventoryItem, UploadResult } from "@/lib/documents-api";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const NEW_ID = "d0c00000-0000-4000-8000-0000000000aa";
const CANONICAL = "# Leave policy\n\nStaff get **20 days** a year.\n\n## Sick leave\n\n- Ten days\n- Paid\n";

const existing: DocumentInventoryItem = {
  document_id: "d0c00000-0000-4000-8000-000000000001",
  source_name: "about-us.md",
  source_type: "markdown",
  chunk_count: 4,
  created_at: "2026-10-01T00:00:00Z",
  converted_from: "md",
  status: "active",
  warnings: [],
  quarantined: false,
};

const uploaded: DocumentInventoryItem = {
  document_id: NEW_ID,
  source_name: "Leave-Policy.md",
  source_type: "markdown",
  chunk_count: 3,
  created_at: "2026-10-16T01:00:00Z",
  updated_at: "2026-10-16T01:00:00Z",
  converted_from: "docx",
  standardized: true,
  converter: "mammoth+gfm",
  converter_version: "v1",
  original_filename: "Leave-Policy.docx",
  original_sha256: "9".repeat(64),
  content_hash: "1".repeat(12),
  content_sha256: "1".repeat(64),
  original_size_bytes: 15360,
  canonical_size_bytes: CANONICAL.length,
  canonical_available: true,
  total_chunk_chars: 120,
  chunk_strategies: ["metadata_aware"],
  chunking_strategy: "metadata_aware",
  embedding_model: "text-embedding-3-small",
  embedding_dim: 1536,
  ingested_at: "2026-10-16T01:00:00Z",
  ingested_by: "owner@example.com",
  ingest_count: 1,
  status: "active",
  warnings: ["images_dropped=1"],
  pii_redacted: [],
  pipeline_version: "v3",
  quarantined: false,
};

function result(dryRun: boolean): UploadResult {
  return {
    document_id: dryRun ? "" : NEW_ID,
    source_name: "Leave-Policy.md",
    original_filename: "Leave-Policy.docx",
    converted_from: "docx",
    status: "new",
    content_hash: "1".repeat(64),
    chunk_count: 3,
    chunks_added: 3,
    chunks_kept: 0,
    chunks_removed: 0,
    embedding_tokens: 40,
    embedding_cost_usd: 0,
    strategy_used: "metadata_aware",
    warnings: ["images_dropped=1"],
    quarantined: false,
    dry_run: dryRun,
    original_size_bytes: 15360,
    canonical_size_bytes: CANONICAL.length,
  };
}

const detail: DocumentDetail = {
  ...uploaded,
  history: [
    {
      at: "2026-10-16T01:00:00Z",
      actor: "owner@example.com",
      status: "new",
      original_filename: "Leave-Policy.docx",
      converted_from: "docx",
      content_hash: "111111111111",
      chunks_added: 3,
      chunks_kept: 0,
      chunks_removed: 0,
      total_chunks: null,
      embedding_tokens: 40,
      warnings: ["images_dropped=1"],
      pii_redacted: [],
      quarantined: false,
    },
  ],
  history_truncated: false,
  chunk_stats: { count: 3, total_chars: 120, min_chars: 30, max_chars: 50, avg_chars: 40 },
};

const chunks = [
  { id: "c1000000-0000-4000-8000-000000000001", chunk_index: 0, content: "Staff get 20 days a year.", strategy: "metadata_aware", base_strategy: "structure_aware", section_heading: "Leave policy", char_count: 25, word_count: 6, hits: 0, low_rated_hits: 0, last_hit_at: null, weekly_hits: [] },
  { id: "c1000000-0000-4000-8000-000000000002", chunk_index: 1, content: "Ten days of sick leave.", strategy: "metadata_aware", base_strategy: "structure_aware", section_heading: "Sick leave", char_count: 23, word_count: 5, hits: 0, low_rated_hits: 0, last_hit_at: null, weekly_hits: [] },
  { id: "c1000000-0000-4000-8000-000000000003", chunk_index: 2, content: "Sick leave is paid.", strategy: "metadata_aware", base_strategy: "structure_aware", section_heading: "Sick leave", char_count: 19, word_count: 4, hits: 0, low_rated_hits: 0, last_hit_at: null, weekly_hits: [] },
];

let inventory: DocumentInventoryItem[];
let uploads: { dryRun: boolean; name: string }[];

function routes(): Route[] {
  return [
    ["GET", /^\/documents$/, () => ({ body: inventory })],
    [
      "POST",
      /^\/ingest\/file$/,
      (_url, init) => {
        const form = init?.body as FormData;
        const dryRun = form.get("dry_run") === "true";
        uploads.push({ dryRun, name: (form.get("file") as File).name });
        if (!dryRun) inventory = [uploaded, ...inventory];
        return { body: result(dryRun) };
      },
    ],
    ["GET", new RegExp(`^/documents/${NEW_ID}$`), () => ({ body: detail })],
    ["GET", new RegExp(`^/documents/${NEW_ID}/canonical$`), () => ({ body: { document_id: NEW_ID, source_name: "Leave-Policy.md", format: "markdown", available: true, content: CANONICAL, size_bytes: CANONICAL.length, content_sha256: "1".repeat(64) } })],
    ["GET", new RegExp(`^/documents/${NEW_ID}/chunks$`), () => ({ body: chunks })],
    ["GET", /\/low-rated-citations$/, () => ({ body: { document_id: NEW_ID, total: 0, items: [] } })],
  ];
}

beforeEach(() => {
  inventory = [existing];
  uploads = [];
  history.reset("/documents");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => vi.unstubAllGlobals());

function pick(file: File) {
  fireEvent.change(screen.getByTestId("document-file-input"), { target: { files: [file] } });
}

describe("Documents journey: upload -> standardize -> chunk -> inventory -> detail", () => {
  it("uploads a .docx, shows the conversion result and the new document everywhere", async () => {
    const calls = mockFetch(routes());
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /about-us\.md/ });

    fireEvent.click(screen.getByRole("button", { name: "Upload Document" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload document" });

    // Client-side validation: nothing is sent for an unsupported type.
    pick(new File(["%PDF"], "scan.pdf", { type: "application/pdf" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Only .md, .markdown and .docx");
    expect(callsTo(calls, "/ingest/file", "POST")).toHaveLength(0);

    // A Word file: dry-run preview first (form field, not a real ingest).
    pick(new File(["PK\u0003\u0004 docx bytes"], "Leave-Policy.docx"));
    expect(await within(dialog).findByText(/New document: 3 chunks will be embedded\./)).toBeInTheDocument();
    expect(within(dialog).getByText("Leave-Policy.md")).toBeInTheDocument();
    expect(within(dialog).getByText(/Word document, will be converted to Markdown/)).toBeInTheDocument();
    expect(within(dialog).getByRole("list", { name: "Conversion warnings" })).toHaveTextContent("images_dropped=1");
    expect(uploads).toEqual([{ dryRun: true, name: "Leave-Policy.docx" }]);
    expect(inventory).toHaveLength(1);

    fireEvent.click(within(dialog).getByRole("button", { name: "Ingest document" }));
    expect(await within(dialog).findByText("Converted to Markdown, 3 chunks")).toBeInTheDocument();
    expect(uploads).toEqual([
      { dryRun: true, name: "Leave-Policy.docx" },
      { dryRun: false, name: "Leave-Policy.docx" },
    ]);
    expect(within(dialog).getByRole("link", { name: "Open document" })).toHaveAttribute("href", `/documents/${NEW_ID}`);

    // The inventory refreshes with the standardized document.
    const row = (await screen.findByRole("link", { name: /Leave-Policy\.md/ })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Standardized to Markdown")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByText("uploaded as Leave-Policy.docx")).toBeInTheDocument();

    // Detail page: metadata, chunks, canonical Markdown, history.
    renderPage(<DocumentDetailPage />, { url: `/documents/${NEW_ID}` });
    expect(await screen.findByRole("heading", { level: 1, name: "Leave-Policy.md" })).toBeInTheDocument();
    const meta = await screen.findByRole("region", { name: "Document metadata" });
    await waitFor(() => expect(meta).toHaveTextContent("mammoth+gfm v1"));
    expect(meta).toHaveTextContent("Word (.docx)");
    expect(meta).toHaveTextContent("15 KB");
    expect(meta).toHaveTextContent("text-embedding-3-small · 1536 dims");
    expect(meta).toHaveTextContent("30 min · 40 avg · 50 max");
    expect(meta).toHaveTextContent("owner@example.com");
    expect(meta).toHaveTextContent("images_dropped=1");

    expect(await screen.findAllByRole("article")).toHaveLength(3);
    expect(screen.getByRole("article", { name: "Chunk 2" })).toHaveTextContent("Sick leave");
    expect(screen.getByRole("button", { name: "Copy id of chunk 2" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Canonical Markdown/ }));
    const view = await screen.findByTestId("markdown-view");
    expect(within(view).getByRole("heading", { name: "Leave policy" })).toBeInTheDocument();
    expect(within(view).getByText("20 days").tagName).toBe("STRONG");
    fireEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(await screen.findByLabelText("Canonical Markdown source")).toHaveTextContent("# Leave policy");

    fireEvent.click(screen.getByRole("tab", { name: /History/ }));
    const historyTable = await screen.findByRole("table");
    expect(within(historyTable).getByText("+3 / 0 / −0")).toBeInTheDocument();
    expect(within(historyTable).getByText("Leave-Policy.docx")).toBeInTheDocument();
  });

  it("a server rejection is shown in the dialog and nothing is ingested", async () => {
    mockFetch([
      ["GET", /^\/documents$/, () => ({ body: inventory })],
      ["POST", /^\/ingest\/file$/, () => ({ status: 415, body: { detail: "Macro-enabled documents are not allowed" } })],
    ]);
    renderPage(<DocumentsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Upload Document" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload document" });
    pick(new File(["x"], "evil.docx"));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Unsupported file type");
    expect(within(dialog).getByRole("button", { name: "Ingest document" })).toBeDisabled();
  });
});
