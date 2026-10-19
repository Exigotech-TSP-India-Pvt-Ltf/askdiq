/**
 * planchanges4 F5.4: Golden Cases "Run now" job chip polls until done;
 * Preference Data flags incomplete pairs and the JSONL download triggers;
 * Knowledge Gaps link into Documents; operators get no action controls.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { GoldenCasesTab } from "@/components/admin/feedback/GoldenCasesTab";
import { PreferenceDataTab } from "@/components/admin/feedback/PreferenceDataTab";
import { KnowledgeGapsTab } from "@/components/admin/feedback/KnowledgeGapsTab";
import { queryCache } from "@/lib/query-cache";
import { Providers, analyticsFixture, installFetch, makeGolden, type FetchCall } from "./fixtures";

vi.mock("next/navigation", async () => (await import("./fixtures")).navigationMock());

beforeEach(() => queryCache.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Golden Cases tab", () => {
  it("shows the last evaluation result and polls a Run now job until it finishes", async () => {
    let finished = false;
    let goldenGets = 0;
    const calls: FetchCall[] = installFetch((call) => {
      if (call.method === "GET" && call.path.startsWith("/admin/feedback/golden")) {
        goldenGets += 1;
        const c = finished
          ? makeGolden({ last_run_at: "2026-10-15T10:00:00Z", last_run_passed: true, last_run_detail: { confidence: 0.91 } })
          : makeGolden({ last_run_at: "2026-10-01T10:00:00Z", last_run_passed: false });
        return { json: { items: [c], total: 1, limit: 100, offset: 0 } };
      }
      if (call.method === "POST" && call.path === "/admin/feedback/golden/fb_case_1/run") {
        return { status: 202, json: { case_id: "fb_case_1", status: "queued", job_id: "job-1" } };
      }
      return undefined;
    });
    render(
      <Providers>
        <GoldenCasesTab token="t" canRun pollMs={25} />
      </Providers>,
    );
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^evaluation$/i })).toHaveAttribute("href", "/admin/evaluation-live");

    fireEvent.click(screen.getByRole("button", { name: "Run golden case fb_case_1 now" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(screen.getByTestId("job-chip")).toHaveAttribute("data-phase", expect.stringMatching(/queued|running/));
    expect(screen.getByRole("button", { name: "Run golden case fb_case_1 now" })).toBeDisabled();

    await waitFor(() => expect(goldenGets).toBeGreaterThan(1));
    finished = true;
    await waitFor(() => expect(screen.getByTestId("job-chip")).toHaveAttribute("data-phase", "passed"));
    const settled = goldenGets;
    await new Promise((r) => setTimeout(r, 80));
    expect(goldenGets).toBe(settled);
  });

  it("an operator sees results but no Run now button", async () => {
    installFetch((call) =>
      call.path.startsWith("/admin/feedback/golden") ? { json: { items: [makeGolden()], total: 1, limit: 100, offset: 0 } } : undefined,
    );
    render(
      <Providers role="operator">
        <GoldenCasesTab token="t" canRun={false} />
      </Providers>,
    );
    expect(await screen.findByText("Not run yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run golden case/i })).not.toBeInTheDocument();
  });
});

const PAIRS = [
  { feedback_id: "f1", message_id: "m1", prompt: "Q1", chosen: "Better", rejected: "Worse", rating: 1, reason_tags: ["wrong"], source_ids: [], triage_status: "new", created_at: "2026-10-14T09:30:00", complete: true, missing: [] },
  { feedback_id: "f2", message_id: "m2", prompt: "Q2", chosen: null, rejected: "Bad", rating: 1, reason_tags: [], source_ids: [], triage_status: "new", complete: false, missing: ["chosen"] },
];

describe("Preference Data tab", () => {
  it("previews pairs, flags the incomplete ones and downloads the JSONL", async () => {
    const calls = installFetch((call) =>
      call.path.startsWith("/admin/feedback/export")
        ? {
            text: PAIRS.map((p) => JSON.stringify(p)).join("\n") + "\n",
            headers: { "Content-Disposition": 'attachment; filename="feedback_pairs_20261015.jsonl"' },
          }
        : undefined,
    );
    const createUrl = vi.fn(() => "blob:pairs");
    const revokeUrl = vi.fn();
    const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    URL.createObjectURL = createUrl;
    URL.revokeObjectURL = revokeUrl;
    onTestFinished(() => {
      URL.createObjectURL = original.create;
      URL.revokeObjectURL = original.revoke;
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    render(
      <Providers>
        <PreferenceDataTab token="t" canExport />
      </Providers>,
    );
    await waitFor(() => expect(screen.getAllByTestId("pair-row")).toHaveLength(2));
    const incomplete = screen.getAllByTestId("pair-row").filter((r) => r.getAttribute("data-complete") === "false");
    expect(incomplete).toHaveLength(1);
    expect(within(incomplete[0]).getByText("Incomplete: chosen")).toBeInTheDocument();
    const [complete] = screen.getAllByTestId("pair-row").filter((r) => r.getAttribute("data-complete") === "true");
    expect(complete.querySelector("time")).toHaveAttribute("datetime", "2026-10-14T09:30:00.000Z");
    expect(within(incomplete[0]).getByText("—")).toBeInTheDocument();
    expect(screen.getByLabelText("Preview counts")).toHaveTextContent("1 complete · 1 incomplete");
    expect(calls[0].path).toContain("format=jsonl");

    fireEvent.click(screen.getByRole("button", { name: /download jsonl/i }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("feedback_pairs_20261015.jsonl");
    expect(createUrl).toHaveBeenCalled();
    expect(revokeUrl).toHaveBeenCalledWith("blob:pairs");
    expect(await screen.findByText("Export downloaded")).toBeInTheDocument();
  });

  it("an operator without feedback:export gets no preview and no download", () => {
    const calls = installFetch(() => undefined);
    render(
      <Providers role="operator">
        <PreferenceDataTab token="t" canExport={false} />
      </Providers>,
    );
    expect(screen.getByText(/needs the feedback:export permission/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });
});

describe("Knowledge Gaps tab", () => {
  it("ranks topics and links to Documents; an operator has no Add document link", () => {
    const onFilter = vi.fn();
    const { rerender } = render(
      <Providers>
        <KnowledgeGapsTab analytics={analyticsFixture} loading={false} error={undefined} onRetry={() => undefined} onFilter={onFilter} />
      </Providers>,
    );
    const items = within(screen.getByRole("list", { name: "Knowledge gaps" })).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("What is the SLA?");
    expect(within(items[0]).getByRole("link", { name: /open document/i })).toHaveAttribute("href", "/documents?q=What%20is%20the%20SLA%3F");
    expect(within(items[0]).getByRole("link", { name: /add document/i })).toHaveAttribute("href", "/documents?action=upload");
    fireEvent.click(within(items[0]).getByRole("button", { name: /show in queue/i }));
    expect(onFilter).toHaveBeenCalledWith({ message_id: "22222222-0000-4000-8000-000000009998", status: "all" });

    rerender(
      <Providers role="operator">
        <KnowledgeGapsTab analytics={analyticsFixture} loading={false} error={undefined} onRetry={() => undefined} onFilter={onFilter} />
      </Providers>,
    );
    expect(screen.queryByRole("link", { name: /add document/i })).not.toBeInTheDocument();
  });
});
