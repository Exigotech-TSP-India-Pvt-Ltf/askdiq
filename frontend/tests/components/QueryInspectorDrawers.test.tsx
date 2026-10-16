import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { QueryInspectorDrawer as EvaluationInspector } from "@/components/admin/QueryInspectorDrawer";
import { QueryInspectorDrawer as UsageInspector } from "@/components/admin/usage/QueryInspectorDrawer";
import { api, ApiError } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

const record = {
  request_id: "req-eval-1",
  query: "What is Essential Eight?",
  answer: "A set of mitigation strategies.",
  passed: true,
  retrieved_chunks: [{ source_name: "ACSC guide", content: "Patch applications", score: 0.9 }],
};

function EvaluationHarness() {
  const [open, setOpen] = useState(false);
  return (
    <MotionProvider>
      <button type="button" onClick={() => setOpen(true)}>
        Inspect
      </button>
      <EvaluationInspector isOpen={open} onClose={() => setOpen(false)} recordId="rec-1" initialData={record} token="t" />
    </MotionProvider>
  );
}

function UsageHarness() {
  const [open, setOpen] = useState(false);
  return (
    <MotionProvider>
      <button type="button" onClick={() => setOpen(true)}>
        Inspect
      </button>
      {open ? <UsageInspector requestId="req-usage-1" token="t" onClose={() => setOpen(false)} /> : null}
    </MotionProvider>
  );
}

describe("QueryInspectorDrawer copies share DrawerShell (F0.6.1)", () => {
  it("evaluation inspector: dialog, sub-tabs, trace deep link, Esc closes and focus returns", async () => {
    render(<EvaluationHarness />);
    const trigger = screen.getByRole("button", { name: "Inspect" });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog", { name: /evaluation passed/i });
    expect(dialog).toHaveTextContent("ID: req-eval-1");
    expect(screen.getByText("What is Essential Eight?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open full view/i })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Traces&request_id=req-eval-1",
    );

    fireEvent.click(screen.getByRole("button", { name: /sources & citations \(1\)/i }));
    expect(screen.getByText("Patch applications")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("evaluation inspector: the footer Close Inspector button still closes it", async () => {
    render(<EvaluationHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "Close Inspector" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("usage inspector: keeps its accessible name, close label and trace link", async () => {
    vi.spyOn(api, "getQueryDetail").mockResolvedValue({
      user_query: "How much did this cost?",
      assistant_answer: "About a cent.",
      cost_usd: 0.0123,
      grounded: true,
      tokens: { prompt: 100, cached_prompt: 0, reasoning: 10, visible: 20, query_embed: 5, guardrail_embed: 5, total_billed: 140 },
    } as never);
    render(<UsageHarness />);
    const trigger = screen.getByRole("button", { name: "Inspect" });
    trigger.focus();
    fireEvent.click(trigger);

    expect(await screen.findByRole("dialog", { name: /single-query economic inspector/i })).toBeInTheDocument();
    expect(await screen.findByText("How much did this cost?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open full view/i })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Traces&request_id=req-usage-1",
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Close inspector" })).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("usage inspector: errors show ErrorState with the request id and a working retry", async () => {
    const spy = vi
      .spyOn(api, "getQueryDetail")
      .mockRejectedValueOnce(new ApiError("Upstream timeout", 504))
      .mockResolvedValueOnce({ user_query: "Retried query", tokens: {} } as never);
    render(<UsageHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Upstream timeout");
    expect(alert).toHaveTextContent("req-usage-1");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText("Retried query")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("evaluation inspector never shows made-up scores", () => {
  it("unreported metrics render as a dash and a failed load is reported", async () => {
    vi.spyOn(api, "evaluationRecordDetail").mockRejectedValue(new ApiError("Record not found", 404));
    render(
      <MotionProvider>
        <EvaluationInspector
          isOpen
          onClose={() => undefined}
          recordId="rec-404"
          initialData={{ ...record, retrieved_chunks: [] }}
          token="t"
        />
      </MotionProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Record not found");
    expect(screen.getByText("— / 5.0")).toBeInTheDocument();
    expect(screen.queryByText("4.5 / 5.0")).toBeNull();
    expect(screen.queryByText("92.0%")).toBeNull();
    expect(screen.queryByText("1.85 s")).toBeNull();
  });
});

describe("inspectors show recorded data, not canned values", () => {
  it("evaluation inspector: guardrails come from the record's flags; stage timings say not available", async () => {
    render(
      <MotionProvider>
        <EvaluationInspector
          isOpen
          onClose={() => undefined}
          recordId="rec-2"
          initialData={{ ...record, safety_passed: false, grounded: false, guardrail_flags: ["pii_leak_detected"] }}
          token="t"
        />
      </MotionProvider>,
    );
    const guardrails = await screen.findByTestId("inspector-guardrails");
    expect(guardrails).toHaveTextContent("PII Leakage:Detected");
    expect(guardrails).toHaveTextContent("Secret Scanning:Clean");
    expect(guardrails).toHaveTextContent("Groundedness:Not grounded");

    fireEvent.click(screen.getByRole("button", { name: /latency & ops/i }));
    expect(screen.getByTestId("inspector-stage-latency")).toHaveTextContent(/not available/i);
    expect(screen.queryByText("1,380 ms")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /llm judge breakdown/i }));
    expect(screen.queryByText(/ACSC and Zero Trust/)).toBeNull();
    expect(screen.getByText(/not evaluated/i)).toBeInTheDocument();
  });

  it("evaluation inspector: an unscanned record shows Not available, never Clean", async () => {
    render(
      <MotionProvider>
        <EvaluationInspector isOpen onClose={() => undefined} recordId="rec-3" initialData={record} token="t" />
      </MotionProvider>,
    );
    const guardrails = await screen.findByTestId("inspector-guardrails");
    expect(guardrails).toHaveTextContent("PII Leakage:Not available");
    expect(guardrails).not.toHaveTextContent("Clean");
  });

  it("usage inspector: reads `latencies` and bills only the uncached prompt at the prompt rate", async () => {
    vi.spyOn(api, "getQueryDetail").mockResolvedValue({
      query: "Cached?",
      cost_usd: 0.001,
      tokens: { prompt: 1000, cached_prompt: 400, reasoning: 0, visible: 10, query_embed: 0, guardrail_embed: 0, total_billed: 1010 },
      latencies: { total_ms: 1500, embedding_ms: 100, retrieval_ms: 200, llm_ms: 1100, reranker_ms: null },
    } as never);
    render(<UsageHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
    const stages = await screen.findByTestId("usage-latency-stages");
    expect(stages).toHaveTextContent("1,500 ms total");
    expect(stages).toHaveTextContent("1,100 ms");
    const uncachedRow = screen.getByText("2. Prompt Context (Uncached)").closest("tr");
    expect(uncachedRow).toHaveTextContent("600");
  });
});
