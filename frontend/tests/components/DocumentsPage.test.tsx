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
const LONG_URL = "https://docs.deployiq.com.au/security/essential-eight/maturity-model-overview";

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
    source_url: LONG_URL,
    section_links: false,
    has_link: true,
    link_status: "ok",
    link_host: "docs.deployiq.com.au",
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
    source_url: null,
    section_links: false,
    has_link: false,
    link_status: "missing",
    link_host: null,
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
    source_url: "https://old.example.org/suspicious",
    section_links: false,
    has_link: true,
    link_status: "not_allowed",
    link_host: "old.example.org",
  },
];

const routes: Route[] = [["GET", /^\/documents$/, () => ({ body: inventory })]];

function rows(): HTMLElement[] {
  return within(screen.getByRole("table")).getAllByRole("row").slice(1);
}

const JSDOM_WIDTH = window.innerWidth;
function setViewportWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: width });
}

beforeEach(() => {
  history.reset("/documents");
  window.localStorage.clear();
  // Wide screen: every column starts visible (below 1440 px hash + size hide by priority, VA-23).
  setViewportWidth(1920);
});
afterEach(() => {
  vi.unstubAllGlobals();
  setViewportWidth(JSDOM_WIDTH);
});

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

    // KPIs come from the same rows (no invented numbers). The visible
    // CountUp span and its accessible sr-only duplicate (data-testid
    // "count-final") show the same text once reduced motion skips the
    // animated count -- getAllByText covers both instead of assuming
    // exactly one match.
    expect(screen.getByText("Total Canonical Documents")).toBeInTheDocument();
    expect(screen.getByText("1 standardized from .docx")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("44").length).toBeGreaterThan(0));
  });

  it("filters by original format and status from the URL", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { url: "/documents?format=docx" });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /^security-posture.md/ })).toBeInTheDocument();
    expect(screen.getAllByText("1 of 3 documents").length).toBeGreaterThan(0);

    renderPage(<DocumentsPage />, { url: "/documents?status=quarantined" });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /^suspicious.md/ })).toBeInTheDocument();
  });

  it("search matches the original file name and the hash, and writes ?q=", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    fireEvent.change(screen.getByPlaceholderText(/search name, original file/i), { target: { value: "Posture_Assessment.docx" } });
    await waitFor(() => expect(currentParams().get("q")).toBe("Posture_Assessment.docx"));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByRole("link", { name: /^essential-eight.md/ })).not.toBeInTheDocument();

    renderPage(<DocumentsPage />, { url: `/documents?q=${FULL_HASH.slice(20, 30)}` });
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("link", { name: /^security-posture.md/ })).toBeInTheDocument();
  });

  it("sorting by a column header updates ?sort= and the order", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
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
    expect(await screen.findByRole("link", { name: /^essential-eight.md/ })).toHaveAttribute("href", "/documents/doc-1");
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

describe("Documents Link column (OR-7)", () => {
  it("shows host + path truncated with the full URL as tooltip, Open (new tab) and Copy", async () => {
    mockFetch(routes);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderPage(<DocumentsPage />);
    const row = (await screen.findByRole("link", { name: /^essential-eight.md/ })).closest("tr") as HTMLElement;

    const shown = within(row).getByText("docs.deployiq.com.au/security/essential-eight/maturity-model-overview");
    expect(shown).toHaveAttribute("title", LONG_URL);
    expect(shown).toHaveClass("truncate");

    const open = within(row).getByRole("link", { name: "Open link for essential-eight.md in a new tab" });
    expect(open).toHaveAttribute("href", LONG_URL);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");

    fireEvent.click(within(row).getByRole("button", { name: "Copy link for essential-eight.md" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LONG_URL));
    expect(await within(row).findByRole("status")).toHaveTextContent("Copied");
  });

  it("badges missing and no-longer-allowed links (text, not colour only) and counts them as needing attention", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    const missing = (await screen.findByRole("link", { name: /^security-posture.md/ })).closest("tr") as HTMLElement;
    expect(within(missing).getByText("Missing link")).toBeInTheDocument();
    expect(within(missing).queryByRole("link", { name: /Open link/ })).not.toBeInTheDocument();

    const notAllowed = screen.getByRole("link", { name: /^suspicious.md/ }).closest("tr") as HTMLElement;
    expect(within(notAllowed).getByText("Link not allowed")).toBeInTheDocument();
    // A stored link whose host left the allow-list is not opened from here.
    expect(within(notAllowed).queryByRole("link", { name: /Open link/ })).not.toBeInTheDocument();

    expect(screen.getByText(/2 missing links/)).toBeInTheDocument();
  });

  it("?link=missing shows only rows with a missing or not-allowed link; the Link header sorts by has-link", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { url: "/documents?link=missing" });
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(screen.queryByRole("link", { name: /^essential-eight.md/ })).not.toBeInTheDocument();

    renderPage(<DocumentsPage />, { url: "/documents" });
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    fireEvent.click(screen.getByRole("columnheader", { name: "Link" }));
    await waitFor(() => expect(currentParams().get("sort")).toBe("link_desc"));
    await waitFor(() => expect(within(rows()[2]).getByRole("link")).toHaveAccessibleName(/security-posture\.md/));
  });
});

describe("Documents Actions column (OR-12)", () => {
  function actionNames(row: HTMLElement): string[] {
    const cell = within(row).getAllByRole("cell").at(-1) as HTMLElement;
    return within(cell)
      .queryAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? "");
  }

  it("superadmin: an Actions header, Edit link then Delete, names include the document and match the tooltip", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { role: "superadmin" });
    const row = (await screen.findByRole("link", { name: /^essential-eight.md/ })).closest("tr") as HTMLElement;
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
    expect(actionNames(row)).toEqual(["Edit link for essential-eight.md", "Delete essential-eight.md"]);
    for (const name of actionNames(row)) {
      const button = within(row).getByRole("button", { name });
      const tip = button.parentElement?.querySelector("[data-tooltip]");
      expect(tip).toHaveTextContent(name);
    }
    expect(within(row).getByRole("button", { name: "Delete essential-eight.md" })).toHaveAttribute("data-tour", "documents-delete");
    // The column chooser lists it under its visible name (locked).
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    expect(within(screen.getByRole("group", { name: "Visible columns" })).getByLabelText("Actions")).toBeDisabled();
  });

  it("admin sees Delete only; operator sees no Actions column", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />, { role: "admin" });
    const row = (await screen.findByRole("link", { name: /^essential-eight.md/ })).closest("tr") as HTMLElement;
    expect(actionNames(row)).toEqual(["Delete essential-eight.md"]);

    renderPage(<DocumentsPage />, { role: "operator" });
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    expect(screen.queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit link for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
  });
});

describe("Documents inline link editor (OR-7)", () => {
  it("validates, shows the server 422 inline, saves with PATCH and updates the row", async () => {
    const patches: unknown[] = [];
    let current = inventory;
    mockFetch([
      ["GET", /^\/documents$/, () => ({ body: current })],
      [
        "PATCH",
        /^\/documents\/doc-2$/,
        (_url, init) => {
          const body = JSON.parse(String(init?.body)) as { source_url: string; section_links: boolean };
          patches.push(body);
          if (body.source_url.includes("evil")) {
            return {
              status: 422,
              body: { detail: "evil.example.net is not on the allowed domain list", code: "source_url_not_allowed" },
            };
          }
          const link = {
            document_id: "doc-2",
            source_url: body.source_url,
            section_links: body.section_links,
            has_link: true,
            link_status: "ok" as const,
            link_host: "docs.deployiq.com.au",
          };
          current = current.map((d) => (d.document_id === "doc-2" ? { ...d, ...link } : d));
          return { body: link };
        },
      ],
    ]);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^security-posture.md/ });
    const trigger = screen.getByRole("button", { name: "Edit link for security-posture.md" });
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Edit link for security-posture.md" });
    const input = within(dialog).getByLabelText("Source URL");
    await waitFor(() => expect(input).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    fireEvent.change(input, { target: { value: "javascript:alert(1)" } });
    expect(within(dialog).getByText("Only https:// links are allowed.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(input, { target: { value: "https://evil.example.net/x" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("evil.example.net is not on the allowed domain list");

    fireEvent.change(input, { target: { value: "https://docs.deployiq.com.au/posture" } });
    fireEvent.click(within(dialog).getByLabelText(/Section links/));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Edit link/ })).not.toBeInTheDocument());
    expect(patches.at(-1)).toEqual({ source_url: "https://docs.deployiq.com.au/posture", section_links: true });
    const row = screen.getByRole("link", { name: /^security-posture.md/ }).closest("tr") as HTMLElement;
    expect(within(row).getByText("docs.deployiq.com.au/posture")).toBeInTheDocument();
    expect(within(row).queryByText("Missing link")).not.toBeInTheDocument();
  });

  it("Esc cancels and returns focus to the Edit link button", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    const trigger = screen.getByRole("button", { name: "Edit link for essential-eight.md" });
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Edit link for essential-eight.md" });
    expect(within(dialog).getByLabelText("Source URL")).toHaveValue(LONG_URL);
    fireEvent.keyDown(within(dialog).getByLabelText("Source URL"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Edit link/ })).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});

describe("Documents table width (VA-23)", () => {
  const headerNames = () => screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());

  it("at 1366 / 1440 px the hash and size columns hide by priority but stay in the Columns menu", async () => {
    setViewportWidth(1440);
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    expect(headerNames()).toEqual(["Document", "Link", "Format", "Chunks", "Ingested", "Status", "Actions"]);
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    const menu = screen.getByRole("group", { name: "Visible columns" });
    expect(within(menu).getByLabelText("Content hash")).not.toBeChecked();
    expect(within(menu).getByLabelText("Size")).not.toBeChecked();
    expect(within(menu).getByLabelText("Format")).toBeChecked();
    // The user can turn a priority column back on.
    fireEvent.click(within(menu).getByLabelText("Content hash"));
    expect(headerNames()).toContain("Content hash");
  });

  it("follows the viewport: 1280 px also drops Format; widening shows every column again", async () => {
    setViewportWidth(1280);
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    expect(headerNames()).toEqual(["Document", "Link", "Chunks", "Ingested", "Status", "Actions"]);
    setViewportWidth(1366);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(headerNames()).toContain("Format"));
    setViewportWidth(1920);
    fireEvent(window, new Event("resize"));
    await waitFor(() =>
      expect(headerNames()).toEqual(["Document", "Link", "Format", "Size", "Chunks", "Content hash", "Ingested", "Status", "Actions"]),
    );
  });

  it("pins Actions with the shared sticky style (the row's colour, no own background)", async () => {
    mockFetch(routes);
    renderPage(<DocumentsPage />);
    await screen.findByRole("link", { name: /^essential-eight.md/ });
    expect(screen.getByRole("columnheader", { name: "Actions" })).toHaveClass("dt-sticky-head");
    const cell = screen.getByRole("button", { name: "Delete essential-eight.md" }).closest("td");
    expect(cell).toHaveClass("dt-sticky-cell");
    expect(cell?.className).not.toMatch(/bg-surface-card|bg-white/);
  });
});
