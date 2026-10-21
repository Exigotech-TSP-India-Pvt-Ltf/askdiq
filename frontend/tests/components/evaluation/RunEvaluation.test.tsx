/**
 * "Run evaluation": gated on eval:run, confirmation dialog with the estimate
 * and judge toggle, live progress chip with cancel, finish toast, and the
 * Runs history list with pagination and results.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RunEvaluationControl } from "@/components/admin/evaluation/RunEvaluationControl";
import { EvaluationRunsPanel } from "@/components/admin/evaluation/EvaluationRunsPanel";
import { usePermission } from "@/lib/auth-context";
import { prefixesForScopes } from "@/lib/live-updates";
import { queryCache } from "@/lib/query-cache";
import type { EvalRun, EvalRunEstimate } from "@/lib/evaluation-runs";
import { Providers, installFetch, type FetchCall } from "../feedback/fixtures";

vi.mock("next/navigation", async () => (await import("../feedback/fixtures")).navigationMock());

beforeEach(() => queryCache.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const RUNS = "/metrics/evaluation/runs";

function makeRun(overrides: Partial<EvalRun> = {}): EvalRun {
  return {
    id: "11111111-2222-4333-8444-555555555555",
    status: "succeeded",
    started_by: "admin@example.com",
    options: { judge: false, case_types: null },
    created_at: "2026-10-20T10:00:00Z",
    started_at: "2026-10-20T10:00:01Z",
    finished_at: "2026-10-20T10:05:00Z",
    cases_total: 80,
    cases_done: 80,
    cases_passed: 72,
    cancel_requested: false,
    pass_rate: 0.9,
    duration_seconds: 299,
    cost_usd: 0.42,
    error: null,
    ...overrides,
  };
}

function estimate(judge: boolean): EvalRunEstimate {
  return {
    case_count: 80,
    judge,
    avg_input_tokens_per_case: 3000,
    avg_output_tokens_per_case: 400,
    avg_seconds_per_case: 6,
    price_prompt_per_million: 0.25,
    price_output_per_million: 2,
    estimated_cost_usd: judge ? 0.248 : 0.124,
    estimated_duration_seconds: judge ? 768 : 480,
    basis: "history",
    sample_runs: 3,
  };
}

const page = (items: EvalRun[], active: EvalRun | null = null, total = items.length) => ({
  json: { items, total, limit: 10, offset: 0, active },
});

function Gate() {
  const canRun = usePermission("eval:run");
  return <RunEvaluationControl token="t" canRun={canRun} pollMs={20} />;
}

describe("Run evaluation button", () => {
  it("is shown only with eval:run", async () => {
    installFetch((call) => (call.path.startsWith(RUNS) ? page([]) : undefined));
    const { unmount } = render(
      <Providers role="operator">
        <Gate />
      </Providers>,
    );
    expect(screen.queryByRole("button", { name: /run evaluation/i })).not.toBeInTheDocument();
    unmount();
    render(
      <Providers role="admin">
        <Gate />
      </Providers>,
    );
    expect(await screen.findByRole("button", { name: /run evaluation/i })).toBeInTheDocument();
  });

  it("confirms with the estimate, the judge toggle changes it, Run starts the job", async () => {
    let started = false;
    const calls: FetchCall[] = installFetch((call) => {
      if (call.method === "GET" && call.path.startsWith(`${RUNS}/estimate`)) {
        return { json: estimate(call.path.includes("judge=true")) };
      }
      if (call.method === "POST" && call.path === RUNS) {
        started = true;
        const run = makeRun({ status: "queued", finished_at: null, pass_rate: null, cases_done: 0 });
        return { status: 202, json: { run_id: run.id, status: "queued", run } };
      }
      if (call.method === "GET" && call.path.startsWith(RUNS)) {
        return started ? page([], makeRun({ status: "queued", cases_done: 0 })) : page([]);
      }
      return undefined;
    });
    render(
      <Providers>
        <RunEvaluationControl token="t" canRun />
      </Providers>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /run evaluation/i }));
    const dialog = await screen.findByRole("dialog", { name: /run the full evaluation/i });
    await waitFor(() => expect(within(dialog).getByTestId("estimate-cases")).toHaveTextContent("80"));
    expect(within(dialog).getByTestId("estimate-cost")).toHaveTextContent("$0.12");
    expect(within(dialog).getByTestId("estimate-duration")).toHaveTextContent("8 min");

    fireEvent.click(within(dialog).getByRole("switch", { name: /llm judge/i }));
    await waitFor(() => expect(within(dialog).getByTestId("estimate-cost")).toHaveTextContent("$0.25"));
    expect(within(dialog).getByTestId("estimate-duration")).toHaveTextContent("13 min");

    fireEvent.click(within(dialog).getByRole("button", { name: /^run$/i }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === RUNS)).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ judge: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows live progress, disables the button with a reason, cancels, then toasts on finish", async () => {
    let phase: "running" | "done" = "running";
    let cancelled = false;
    const active = () =>
      makeRun({ status: "running", cases_done: 12, cases_total: 80, cancel_requested: cancelled, finished_at: null });
    const calls = installFetch((call) => {
      if (call.method === "POST" && call.path.endsWith("/cancel")) {
        cancelled = true;
        return { status: 202, json: active() };
      }
      if (call.method === "GET" && call.path.startsWith(RUNS)) {
        return phase === "running" ? page([active()], active()) : page([makeRun({ status: "cancelled" })]);
      }
      return undefined;
    });
    render(
      <Providers>
        <RunEvaluationControl token="t" canRun pollMs={20} />
      </Providers>,
    );
    expect(await screen.findByTestId("run-progress-chip")).toHaveTextContent("12 / 80 cases");
    const button = screen.getByTestId("run-evaluation-button");
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAccessibleDescription(/started by admin@example.com/);
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop run/i }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/cancel"))).toBe(true));
    expect(await screen.findByRole("button", { name: /stopping/i })).toBeDisabled();

    phase = "done";
    expect(await screen.findByText("Evaluation cancelled")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("run-progress-chip")).not.toBeInTheDocument());
    expect(screen.getByTestId("run-evaluation-button")).not.toHaveAttribute("aria-disabled");
  });

  it("maps the progress scope to the runs views only", () => {
    expect(prefixesForScopes(["evaluation.runs"])).toEqual(["metrics/evaluation/runs"]);
    expect(prefixesForScopes(["evaluation.live"])).toEqual(["metrics/evaluation"]);
  });
});

describe("Runs history", () => {
  it("lists runs with who, status, pass rate, duration and cost, pages and opens results", async () => {
    const runs = [
      makeRun(),
      makeRun({
        id: "22222222-2222-4333-8444-555555555555",
        status: "failed",
        pass_rate: null,
        cost_usd: null,
        duration_seconds: 12,
        error: "model unavailable",
        started_by: "ops@example.com",
      }),
    ];
    const calls = installFetch((call) => {
      if (call.path.startsWith(`${RUNS}/${runs[0].id}`)) {
        return {
          json: {
            ...runs[0],
            summary: { groundedness_accuracy: 0.95 },
            case_results: [{ id: "zt_pillars", type: "answerable", query: "What are the pillars?", passed: true }],
          },
        };
      }
      if (call.path.startsWith(RUNS)) {
        const offset = Number(new URLSearchParams(call.path.split("?")[1]).get("offset") ?? 0);
        return { json: { items: offset === 0 ? runs : [], total: 12, limit: 10, offset, active: null } };
      }
      return undefined;
    });
    function Harness() {
      const [selected, setSelected] = React.useState<string | null>(null);
      return <EvaluationRunsPanel token="t" selectedId={selected} onSelect={setSelected} />;
    }
    render(
      <Providers>
        <Harness />
      </Providers>,
    );
    const table = await screen.findByRole("table", { name: "Evaluation runs" });
    const rows = within(table).getAllByTestId("run-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("admin@example.com");
    expect(rows[0]).toHaveTextContent("Succeeded");
    expect(rows[0]).toHaveTextContent("90.0%");
    expect(rows[0]).toHaveTextContent("5 min");
    expect(rows[0]).toHaveTextContent("$0.42");
    expect(rows[1]).toHaveTextContent("Failed");
    expect(rows[1]).toHaveTextContent("12 s");
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(calls.some((c) => c.path.includes("offset=10"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));

    fireEvent.click(await screen.findByRole("button", { name: /view results/i }));
    expect(await screen.findByRole("table", { name: "Case results" })).toHaveTextContent("zt_pillars");
    expect(screen.getByText("95.0%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /all runs/i }));
    expect(await screen.findByRole("table", { name: "Evaluation runs" })).toBeInTheDocument();
  });
});
