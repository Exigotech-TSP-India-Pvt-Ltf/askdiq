import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_UPLOAD_BYTES,
  changeReasonText,
  documentStatus,
  filterInventory,
  formatBytes,
  originalFormat,
  shortHash,
  strategyLabel,
  uploadDocument,
  validateUploadFile,
  type DocumentInventoryItem,
} from "@/lib/documents-api";

function doc(overrides: Partial<DocumentInventoryItem>): DocumentInventoryItem {
  return {
    document_id: "d",
    source_name: "a.md",
    source_type: "markdown",
    chunk_count: 1,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("validateUploadFile", () => {
  it("accepts .md, .markdown and .docx up to the limit", () => {
    expect(validateUploadFile({ name: "Policy.DOCX", size: 10 })).toBeNull();
    expect(validateUploadFile({ name: "notes.markdown", size: 10 })).toBeNull();
    expect(validateUploadFile({ name: "a.md", size: MAX_UPLOAD_BYTES })).toBeNull();
  });

  it("rejects other types, empty and oversized files", () => {
    expect(validateUploadFile({ name: "a.pdf", size: 10 })).toMatch(/only \.md/i);
    expect(validateUploadFile({ name: "a.doc", size: 10 })).toMatch(/only \.md/i);
    expect(validateUploadFile({ name: "noext", size: 10 })).toMatch(/only \.md/i);
    expect(validateUploadFile({ name: "a.md", size: 0 })).toMatch(/empty/i);
    expect(validateUploadFile({ name: "a.md", size: MAX_UPLOAD_BYTES + 1 })).toMatch(/10 MB/);
  });
});

describe("uploadDocument", () => {
  it("sends the file and the dry_run flag as multipart form fields", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "new" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# Hi"], "hi.md", { type: "text/markdown" });
    await uploadDocument("t", file, { dryRun: true });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/proxy/ingest/file");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("dry_run")).toBe("true");
    expect(body.get("force")).toBeNull();
    expect((body.get("file") as File).name).toBe("hi.md");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });
});

describe("inventory helpers", () => {
  const docs = [
    doc({ document_id: "1", source_name: "beta.md", chunk_count: 5, converted_from: "docx", original_filename: "Beta.docx", canonical_size_bytes: 300, ingested_at: "2026-10-03T00:00:00Z" }),
    doc({ document_id: "2", source_name: "alpha.md", chunk_count: 2, converted_from: "md", warnings: ["w"], canonical_size_bytes: 900, ingested_at: "2026-10-05T00:00:00Z" }),
    doc({ document_id: "3", source_name: "gamma.md", chunk_count: 0, quarantined: true, content_sha256: "abcdef123456ffff", labels: { corpus: "hr" } }),
  ];
  const all = { q: "", format: "all", status: "all", warnings: "all" } as const;

  it("filters by format, status, warnings and search (name, original file, hash, labels)", () => {
    expect(filterInventory(docs, { ...all, format: "docx" }, "name_asc").map((d) => d.document_id)).toEqual(["1"]);
    expect(filterInventory(docs, { ...all, format: "md" }, "name_asc").map((d) => d.document_id)).toEqual(["2", "3"]);
    expect(filterInventory(docs, { ...all, status: "quarantined" }, "name_asc").map((d) => d.document_id)).toEqual(["3"]);
    expect(filterInventory(docs, { ...all, warnings: "with" }, "name_asc").map((d) => d.document_id)).toEqual(["2"]);
    expect(filterInventory(docs, { ...all, q: "BETA.DOCX" }, "name_asc").map((d) => d.document_id)).toEqual(["1"]);
    expect(filterInventory(docs, { ...all, q: "abcdef" }, "name_asc").map((d) => d.document_id)).toEqual(["3"]);
    expect(filterInventory(docs, { ...all, q: "hr" }, "name_asc").map((d) => d.document_id)).toEqual(["3"]);
  });

  it("sorts by name, chunks, size and ingestion time", () => {
    expect(filterInventory(docs, all, "name_asc").map((d) => d.source_name)).toEqual(["alpha.md", "beta.md", "gamma.md"]);
    expect(filterInventory(docs, all, "chunks_desc").map((d) => d.document_id)).toEqual(["1", "2", "3"]);
    expect(filterInventory(docs, all, "size_desc").map((d) => d.document_id)).toEqual(["2", "1", "3"]);
    expect(filterInventory(docs, all, "ingested_desc").map((d) => d.document_id)).toEqual(["2", "1", "3"]);
  });

  it("derives format and status for older rows without the new fields", () => {
    expect(originalFormat(doc({ original_filename: "x.docx" }))).toBe("docx");
    expect(originalFormat(doc({}))).toBe("md");
    expect(documentStatus(doc({ quarantined: true }))).toBe("quarantined");
    expect(documentStatus(doc({ chunk_count: 0 }))).toBe("empty");
    expect(documentStatus(doc({ status: "active" }))).toBe("active");
  });

  it("formats bytes, hashes and strategies", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024 * 1024)).toBe("20 MB");
    expect(shortHash("0123456789abcdef")).toBe("0123456789ab");
    expect(shortHash(undefined)).toBeNull();
    expect(strategyLabel("structure_aware")).toBe("Structure aware");
    expect(strategyLabel(null)).toBe("—");
  });

  it("states why an existing document was re-chunked", () => {
    expect(changeReasonText([])).toBe("Existing document changed");
    expect(changeReasonText(undefined)).toBe("Existing document changed");
    expect(changeReasonText(["chunking_changed"])).toBe(
      "Re-chunked because chunker settings changed (same text, different chunks)",
    );
    const owner = changeReasonText(["source_format_changed", "hash_scheme_changed", "content_changed"]);
    expect(owner).toContain("source format differs");
    expect(owner).toContain("older pipeline");
    expect(owner).toContain("text differs");
    expect(changeReasonText(["new_reason"])).toBe("Re-chunked because new reason");
  });
});
