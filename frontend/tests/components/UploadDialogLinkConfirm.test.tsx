/**
 * Upload link confirmation (POST /ingest/preview): the dialog shows the page
 * link and where it came from (confirm or edit), the matched document and
 * what will happen, the warnings; a different-name replacement needs an
 * explicit confirm and sends replace_document_id; a 409
 * document_link_in_use asks for that confirm; the result shows the final
 * link and the added / kept / removed counts.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { UploadDialog } from "@/components/documents/UploadDialog";
import type { IngestPreview, UploadResult } from "@/lib/documents-api";
import { mockFetch, renderPage, type Route } from "../helpers/entity-harness";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const SETTINGS = {
  allowed_hosts: ["*.deployiq.com.au"],
  require_source_url: true,
  version: 1,
  updated_at: null,
  updated_by: null,
  max_hosts: 50,
  max_host_length: 253,
};

function preview(overrides: Partial<IngestPreview> = {}): IngestPreview {
  return {
    detected_link: "https://docs.deployiq.com.au/guide",
    link_source: "front_matter_url",
    canonical_name: "guide.md",
    existing_document: null,
    content_status: "new",
    chunks_estimate: 4,
    warnings: [],
    ...overrides,
  };
}

function uploaded(sourceUrl: string | null): UploadResult {
  return {
    document_id: "doc-1",
    source_name: "guide.md",
    status: "updated",
    content_hash: "a".repeat(64),
    chunk_count: 4,
    chunks_added: 3,
    chunks_kept: 1,
    chunks_removed: 2,
    embedding_tokens: 10,
    embedding_cost_usd: 0,
    warnings: [],
    quarantined: false,
    source_url: sourceUrl,
  };
}

type Sent = Record<string, string | null>;
let previews: Sent[];
let ingests: Sent[];

function fields(init: RequestInit | undefined, names: string[]): Sent {
  const form = init?.body as FormData;
  return Object.fromEntries(names.map((n) => [n, typeof form.get(n) === "string" ? (form.get(n) as string) : null]));
}

function routes(
  previewFor: (sourceUrl: string | null) => IngestPreview,
  ingestReply: (sent: Sent) => { status?: number; body: unknown } = (sent) => ({ body: uploaded(sent.source_url) }),
): Route[] {
  return [
    ["GET", /^\/knowledge\/settings$/, () => ({ body: SETTINGS })],
    [
      "POST",
      /^\/ingest\/preview$/,
      (_url, init) => {
        const sent = fields(init, ["source_url"]);
        previews.push(sent);
        return { body: previewFor(sent.source_url) };
      },
    ],
    [
      "POST",
      /^\/ingest\/file$/,
      (_url, init) => {
        const sent = fields(init, ["dry_run", "source_url", "replace_document_id"]);
        ingests.push(sent);
        return ingestReply(sent);
      },
    ],
  ];
}

beforeEach(() => {
  previews = [];
  ingests = [];
});
afterEach(() => vi.unstubAllGlobals());

async function openWithFile() {
  const onUploaded = vi.fn();
  renderPage(<UploadDialog open token="t" onClose={vi.fn()} onUploaded={onUploaded} />);
  const dialog = await screen.findByRole("dialog", { name: "Upload document" });
  fireEvent.change(screen.getByTestId("document-file-input"), { target: { files: [new File(["# Guide"], "guide.md")] } });
  await within(dialog).findByTestId("upload-link-confirm");
  return { dialog, onUploaded };
}

describe("UploadDialog link confirmation", () => {
  it("shows the detected link and its source; ingest waits for the confirm", async () => {
    mockFetch(routes(() => preview({ warnings: ["2 duplicate paragraphs removed"] })));
    const { dialog, onUploaded } = await openWithFile();

    expect(within(dialog).getByTestId("upload-detected-link")).toHaveTextContent("docs.deployiq.com.au/guide");
    expect(within(dialog).getByText("From the file's front matter (url:).")).toBeInTheDocument();
    expect(within(dialog).getByText(/New document: about 4 chunks will be embedded\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("list", { name: "Conversion warnings" })).toHaveTextContent("2 duplicate paragraphs removed");
    // The legacy dry run is not used when the preview endpoint exists.
    expect(ingests).toEqual([]);

    const ingest = within(dialog).getByRole("button", { name: "Ingest document" });
    expect(ingest).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));
    expect(within(dialog).getByText("Link confirmed")).toBeInTheDocument();
    await waitFor(() => expect(ingest).toBeEnabled());

    fireEvent.click(ingest);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests).toEqual([{ dry_run: null, source_url: "https://docs.deployiq.com.au/guide", replace_document_id: null }]);
    expect(await within(dialog).findByTestId("upload-final-link")).toHaveTextContent("docs.deployiq.com.au/guide");
    expect(within(dialog).getByRole("link", { name: "docs.deployiq.com.au/guide" })).toHaveAttribute(
      "href",
      "https://docs.deployiq.com.au/guide",
    );
    expect(within(dialog).getByText("+3 added · 1 kept · −2 removed")).toBeInTheDocument();
  });

  it("an edited link is re-previewed and then sent", async () => {
    mockFetch(
      routes((url) =>
        url ? preview({ detected_link: url, link_source: "form" }) : preview({ link_source: "docx" }),
      ),
    );
    const { dialog, onUploaded } = await openWithFile();
    expect(within(dialog).getByText("From the Word file's properties.")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Edit link" }));
    const field = within(dialog).getByLabelText(/Source URL/);
    const use = within(dialog).getByRole("button", { name: "Use this link" });
    expect(use).toBeDisabled();
    fireEvent.change(field, { target: { value: "https://github.com/x" } });
    expect(within(dialog).getByText(/github\.com is not on the allowed domain list/)).toBeInTheDocument();
    expect(use).toBeDisabled();
    fireEvent.change(field, { target: { value: "https://help.deployiq.com.au/new" } });
    expect(within(dialog).getByRole("button", { name: "Ingest document" })).toBeDisabled();
    fireEvent.click(use);

    expect(await within(dialog).findByText("From entered in this form.")).toBeInTheDocument();
    expect(within(dialog).getByText("Link confirmed")).toBeInTheDocument();
    expect(previews).toEqual([{ source_url: null }, { source_url: "https://help.deployiq.com.au/new" }]);
    const ingest = within(dialog).getByRole("button", { name: "Ingest document" });
    await waitFor(() => expect(ingest).toBeEnabled());
    fireEvent.click(ingest);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests[0].source_url).toBe("https://help.deployiq.com.au/new");
  });

  it("explains unchanged and changed matches", async () => {
    const existing = { id: "doc-1", name: "guide.md", link: "https://docs.deployiq.com.au/guide" };
    mockFetch(routes(() => preview({ existing_document: existing, content_status: "unchanged", link_source: "stored" })));
    let { dialog } = await openWithFile();
    expect(within(dialog).getByText(/Matches guide\.md: unchanged, nothing to re-ingest\./)).toBeInTheDocument();
    expect(within(dialog).getByText("From the link already stored for this document.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Re-check document" })).toBeDisabled();

    vi.unstubAllGlobals();
    mockFetch(routes(() => preview({ existing_document: existing, content_status: "changed" })));
    ({ dialog } = await openWithFile());
    expect(
      within(dialog).getByText(/Matches guide\.md: will replace the old version; old chunks removed\./),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Apply changes" })).toBeInTheDocument();
  });

  it("same link under another name needs an explicit replace confirm and sends replace_document_id", async () => {
    const existing = { id: "doc-old", name: "old-guide.md", link: "https://docs.deployiq.com.au/guide" };
    mockFetch(routes(() => preview({ existing_document: existing, content_status: "changed" })));
    const { dialog, onUploaded } = await openWithFile();
    expect(within(dialog).getByText(/Same link as old-guide\.md: will replace old-guide\.md/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));

    const replace = within(dialog).getByRole("button", { name: "Replace document" });
    expect(replace).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Replace old-guide.md with guide.md" }));
    await waitFor(() => expect(replace).toBeEnabled());
    fireEvent.click(replace);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests[0].replace_document_id).toBe("doc-old");
  });

  it("link_in_use_by names the document to replace even when the name matches another", async () => {
    mockFetch(
      routes(() =>
        preview({
          existing_document: { id: "doc-1", name: "guide.md", link: null, matched_by: "name" },
          link_in_use_by: { id: "doc-other", name: "other.md", link: "https://docs.deployiq.com.au/guide" },
          replace_required: true,
          content_status: "changed",
        }),
      ),
    );
    const { dialog, onUploaded } = await openWithFile();
    expect(within(dialog).getByText(/will replace other\.md/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Replace other.md with guide.md" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Replace document" }));
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests[0].replace_document_id).toBe("doc-other");
  });

  it("a 409 document_link_in_use shows the replace confirmation", async () => {
    const existing = { id: "doc-old", name: "old-guide.md", link: "https://docs.deployiq.com.au/guide" };
    let calls = 0;
    mockFetch(
      routes(
        // First preview missed the clash (e.g. another upload in between); the re-preview finds it.
        () => (calls++ === 0 ? preview() : preview({ existing_document: existing, content_status: "changed" })),
        (sent) =>
          sent.replace_document_id
            ? { body: uploaded(sent.source_url) }
            : { status: 409, body: { detail: "This link is already used by old-guide.md.", code: "document_link_in_use" } },
      ),
    );
    const { dialog, onUploaded } = await openWithFile();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Ingest document" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This link is already used by old-guide.md. Confirm the replacement above to continue.",
    );
    const replace = within(dialog).getByRole("button", { name: "Replace document" });
    expect(replace).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Replace old-guide.md with guide.md" }));
    await waitFor(() => expect(replace).toBeEnabled());
    fireEvent.click(replace);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(ingests.map((i) => i.replace_document_id)).toEqual([null, "doc-old"]);
  });

  it("no detected link: the field is shown and a required link must be entered", async () => {
    mockFetch(routes((url) => (url ? preview({ detected_link: url, link_source: "form" }) : preview({ detected_link: null, link_source: null }))));
    const { dialog } = await openWithFile();
    const field = within(dialog).getByLabelText(/Source URL/);
    await waitFor(() => expect(field).toHaveAttribute("aria-required", "true"));
    expect(within(dialog).queryByRole("button", { name: "Keep detected link" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Ingest document" })).toBeDisabled();
    fireEvent.change(field, { target: { value: "https://docs.deployiq.com.au/typed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Use this link" }));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Ingest document" })).toBeEnabled());
  });
});
