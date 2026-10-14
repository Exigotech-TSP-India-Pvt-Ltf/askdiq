import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DocumentsPage from "@/app/(app)/documents/page";
import { api } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "mock-token", user: null, loading: false }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/documents",
  useSearchParams: () => new URLSearchParams(),
}));

const mockDocs: DocumentSummary[] = [
  {
    document_id: "doc-1",
    source_name: "essential-eight.md",
    source_type: "markdown",
    chunk_count: 14,
    created_at: new Date().toISOString(),
    content_hash: "a1b2c3d4e5f67890",
    quarantined: false,
    warnings: [],
  },
  {
    document_id: "doc-2",
    source_name: "security-posture.md",
    source_type: "markdown",
    converted_from: "docx",
    original_filename: "Security_Posture_Assessment.docx",
    original_sha256: "0123456789abcdef",
    chunk_count: 28,
    created_at: new Date().toISOString(),
    content_hash: "f6e5d4c3b2a10987",
    quarantined: false,
    warnings: ["Table formatting converted"],
  },
];

describe("DocumentsPage Canonical Markdown Presentation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listDocuments").mockResolvedValue(mockDocs);
  });

  it("renders canonical MARKDOWN (.md) for every document and provenance chip for converted docx", async () => {
    render(<DocumentsPage />);

    await waitFor(() => {
      expect(screen.getByText("essential-eight.md")).toBeInTheDocument();
    });

    expect(screen.getByText("security-posture.md")).toBeInTheDocument();

    // Verify all rows show MARKDOWN (.md)
    const formatChips = screen.getAllByText("MARKDOWN (.md)");
    expect(formatChips.length).toBe(2);

    // Verify Standardized from .docx provenance chip appears only for converted doc
    expect(screen.getByText("Standardized from .docx")).toBeInTheDocument();
  });

  it("filters documents by search query", async () => {
    render(<DocumentsPage />);

    await waitFor(() => {
      expect(screen.getByText("essential-eight.md")).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search source document/i);
    fireEvent.change(searchInput, { target: { value: "security" } });

    await waitFor(() => {
      expect(screen.queryByText("essential-eight.md")).not.toBeInTheDocument();
      expect(screen.getByText("security-posture.md")).toBeInTheDocument();
    });
  });
});
