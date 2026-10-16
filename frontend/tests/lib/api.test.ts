import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "@/lib/api";

describe("Frontend API Client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("sends multipart FormData in ingestFile with query flags", async () => {
    const fakeIngestResult = {
      source_name: "test.md",
      original_filename: "test.docx",
      status: "updated",
      content_hash: "abc1234",
      chunks_added: 2,
      chunks_kept: 5,
      chunks_removed: 1,
      embedding_tokens: 250,
      embedding_cost_usd: 0.0001,
      warnings: [],
      quarantined: false,
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => fakeIngestResult,
    } as Response);

    const file = new File(["test content"], "test.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const res = await api.ingestFile("test-token", file, { dryRun: true });

    expect(res.status).toBe("updated");
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/ingest/file?dry_run=true"),
      expect.objectContaining({
        method: "POST",
      })
    );
  });

  it("fetches branding metadata via GET /branding", async () => {
    const fakeBranding = {
      brand_name: "DeployIQ",
      brand_assistant_name: "AskDIQ",
      brand_support_email: "support@deployiq.com",
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => fakeBranding,
    } as Response);

    const branding = await api.getBranding();
    expect(branding).toEqual(fakeBranding);
  });
});
