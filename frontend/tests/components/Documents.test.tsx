/**
 * Documents list (citation charts, permission-gated upload / delete with
 * ConfirmDialog) and Document detail (debounced chunk search, sort by hits,
 * hit heatmap, low-rated citations, `?chunk=`).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import DocumentsPage from "@/app/(app)/documents/page";
import DocumentDetailPage from "@/app/(app)/documents/[id]/page";
import { callsTo, currentParams, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import { ASSISTANT_MSG, CHUNK_ID, DOC_ID, chunks, citationStats, documents, lowRated } from "../helpers/entity-fixtures";
import { CHUNK_SEARCH_DEBOUNCE_MS } from "@/lib/entity-stats-api";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const docsRoute: Route = ["GET", /^\/documents$/, () => ({ body: documents })];
const statsRoute: Route = ["GET", /^\/documents\/stats\/citations$/, () => ({ body: citationStats })];
const chunksRoute: Route = ["GET", /^\/documents\/[^/]+\/chunks$/, () => ({ body: chunks })];
const lowRatedRoute: Route = ["GET", /\/low-rated-citations$/, () => ({ body: lowRated })];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Documents list", () => {
  beforeEach(() => history.reset("/documents"));

  it("top-cited bar and age scatter deep-link to the document; coverage gauge shows the share", async () => {
    mockFetch([docsRoute, statsRoute]);
    renderPage(<DocumentsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Leave policy.md, Citations: 120" }));
    expect(history.push).toHaveBeenCalledWith(`/documents/${DOC_ID}`);
    fireEvent.click(screen.getByRole("button", { name: /^Leave policy.md: Age \(days\) 200/ }));
    expect(history.push).toHaveBeenLastCalledWith(`/documents/${DOC_ID}`);
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByText("870 of 1,000 answers")).toBeInTheDocument();
  });

  it("shows empty charts when the citation endpoint is absent and keeps the inventory", async () => {
    mockFetch([docsRoute]);
    renderPage(<DocumentsPage />);
    expect((await screen.findAllByText("Leave policy.md")).length).toBeGreaterThan(0);
    expect(await screen.findAllByText("Citation stats are not available yet")).toHaveLength(3);
  });

  it("Upload document is a labelled primary action on the toolbar row, before the date filter (OR-2)", async () => {
    mockFetch([docsRoute, statsRoute]);
    renderPage(<DocumentsPage />);
    const upload = await screen.findByRole("button", { name: "Upload document" });
    expect(upload).toHaveTextContent("Upload document");
    expect(upload.className).toContain("bg-brand-gradient");
    expect(upload).toHaveAttribute("data-tour", "documents-upload");
    expect(screen.getByRole("banner")).not.toContainElement(upload);
    const cluster = upload.closest("[data-toolbar-actions]");
    expect(cluster).not.toBeNull();
    expect(cluster?.lastElementChild).toHaveAttribute("data-tour", "shell-date-range");
  });

  it("an operator sees no upload or delete controls", async () => {
    mockFetch([docsRoute, statsRoute]);
    renderPage(<DocumentsPage />, { role: "operator" });
    await screen.findAllByText("Leave policy.md");
    expect(screen.queryByRole("button", { name: /upload document/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
  });

  it("delete goes through ConfirmDialog", async () => {
    const calls = mockFetch([docsRoute, statsRoute, ["DELETE", /^\/documents\//, () => ({ status: 204 })]]);
    renderPage(<DocumentsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Leave policy.md" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(callsTo(calls, /^\/documents\//, "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Delete Leave policy.md" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete document" }));
    await waitFor(() => expect(callsTo(calls, `/documents/${DOC_ID}`, "DELETE")).toHaveLength(1));
  });
});

describe("Document detail", () => {
  const scrollIntoView = vi.fn();
  beforeEach(() => {
    scrollIntoView.mockClear();
    Element.prototype.scrollIntoView = scrollIntoView;
  });

  it("has a DashboardHeader breadcrumb and lists chunks with hit counts", async () => {
    mockFetch([docsRoute, chunksRoute, lowRatedRoute]);
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}` });
    expect(await screen.findByRole("heading", { level: 1, name: "Leave policy.md" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "href",
      "/documents",
    );
    expect(screen.getByText("42 hits")).toBeInTheDocument();
  });

  it("chunk search filters at once in the browser and is debounced into the URL", async () => {
    const calls = mockFetch([docsRoute, chunksRoute, lowRatedRoute]);
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}` });
    await screen.findByText("42 hits");
    const box = screen.getByRole("searchbox", { name: "Search chunks" });
    fireEvent.change(box, { target: { value: "a" } });
    fireEvent.change(box, { target: { value: "an" } });
    fireEvent.change(box, { target: { value: "annual" } });
    expect(currentParams().get("q")).toBeNull();
    expect(callsTo(calls, /\/chunks$/).some((u) => u.searchParams.has("q"))).toBe(false);
    // Filtered in the browser before the URL even changes.
    expect(screen.queryByText("Introduction to the policy.")).not.toBeInTheDocument();
    await waitFor(() => expect(currentParams().get("q")).toBe("annual"), { timeout: CHUNK_SEARCH_DEBOUNCE_MS * 5 });
    // Typing never asks the server again.
    expect(callsTo(calls, /\/chunks$/).some((u) => u.searchParams.has("q"))).toBe(false);
  });

  it("sort by hits orders the chunks without a request", async () => {
    const calls = mockFetch([docsRoute, chunksRoute, lowRatedRoute]);
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}` });
    await screen.findByText("42 hits");
    const before = callsTo(calls, /\/chunks$/).length;
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "hits" } });
    const articles = await screen.findAllByRole("article");
    await waitFor(() => expect(articles[0]).toHaveAttribute("aria-label", "Chunk 4"));
    expect(callsTo(calls, /\/chunks$/)).toHaveLength(before);
  });

  it("`?chunk=` scrolls to and highlights the chunk; heatmap and low-rated items set it", async () => {
    mockFetch([docsRoute, chunksRoute, lowRatedRoute]);
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}?chunk=${CHUNK_ID}` });
    const chunk = await screen.findByRole("article", { name: "Chunk 4" });
    expect(chunk).toHaveAttribute("data-highlighted", "true");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());

    history.reset(`/documents/${DOC_ID}`);
    fireEvent.click(await screen.findByRole("button", { name: "#1 5 Oct: 2" }));
    await waitFor(() => expect(currentParams().get("chunk")).toBe(chunks[0].id));

    fireEvent.click(screen.getByRole("tab", { name: /Citations/ }));
    await waitFor(() => expect(currentParams().get("tab")).toBe("citations"));
    const list = await screen.findByRole("list", { name: "Low-rated citations" });
    expect(within(list).getByRole("link", { name: "Open in Feedback" })).toHaveAttribute("href", `/admin/feedback?message_id=${ASSISTANT_MSG}`);
    fireEvent.click(within(list).getByRole("button", { name: "Chunk 4" }));
    await waitFor(() => expect(currentParams().get("chunk")).toBe(CHUNK_ID));
    // Jumping to a chunk returns to the Chunks tab.
    expect(currentParams().get("tab")).toBeNull();
    expect(await screen.findByRole("article", { name: "Chunk 4" })).toBeInTheDocument();
  });

  it("renders chunks from an older backend (no hits, no low-rated endpoint)", async () => {
    mockFetch([
      docsRoute,
      ["GET", /^\/documents\/[^/]+\/chunks$/, () => ({ body: chunks.map(({ hits, low_rated_hits, weekly_hits, ...rest }) => rest) })],
    ]);
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}` });
    expect(await screen.findByText("Annual leave is 20 days per year.")).toBeInTheDocument();
    expect(screen.getByText("Chunk hit counts are not available yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Citations/ }));
    expect(await screen.findByText("Low-rated citations are not available yet")).toBeInTheDocument();
    // No detail or canonical endpoint either: metadata comes from the inventory row.
    expect(screen.getByRole("region", { name: "Document metadata" })).toHaveTextContent("Native Markdown");
    fireEvent.click(screen.getByRole("tab", { name: /Canonical Markdown/ }));
    expect(await screen.findByText("The canonical Markdown was not stored for this document")).toBeInTheDocument();
  });
});
