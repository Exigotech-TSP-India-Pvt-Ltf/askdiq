/**
 * Phase F4 integration journey: the Governance page with
 * `fetch` and `EventSource` mocked and an in-memory router history.
 *
 * - An overview incident marker deep-links into Logs, which then queries the
 *   API with those filters.
 * - `?request_id=` opens the trace drawer; its conversation link targets the
 *   conversation; previous / next walk the page.
 * - Logs "Older" sends the cursor the server returned (deep pages beyond
 *   2,000 rows stay reachable).
 * - Monitoring labels each count and refreshes from SSE instead of polling.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { RangeProvider } from "@/lib/range-context";
import { queryCache } from "@/lib/query-cache";
import GovernancePage from "@/app/(app)/admin/governance/page";
import { MONITORING_REFETCH_MIN_MS } from "@/app/(app)/admin/governance/monitoring/MonitoringView";

// The full suite runs files in parallel; give async queries headroom on slow CI.
configure({ asyncUtilTimeout: 5_000 });
vi.setConfig({ testTimeout: 30_000 });

// --- router ------------------------------------------------------------------

const history = vi.hoisted(() => {
  const state = { entries: ["/admin/governance"], index: 0, listeners: new Set<() => void>() };
  const notify = () => state.listeners.forEach((listener) => listener());
  return {
    state,
    current: () => state.entries[state.index],
    reset(url: string) {
      state.entries = [url];
      state.index = 0;
      notify();
    },
    push: vi.fn((url: string) => {
      state.entries = [...state.entries.slice(0, state.index + 1), url];
      state.index += 1;
      notify();
    }),
    replace: vi.fn((url: string) => {
      state.entries[state.index] = url;
      notify();
    }),
  };
});

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    history.state.listeners.add(cb);
    return () => {
      history.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, history.current);
  return {
    useRouter: () => ({ push: history.push, replace: history.replace }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
    redirect: vi.fn(),
  };
});

const permissions = vi.hoisted(() => ({ granted: true }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "t", user: null, isLoading: false, logout: vi.fn() }),
  usePermission: () => permissions.granted,
}));

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({ audience: "overview", setAudience: vi.fn(), counts: null, refreshCounts: vi.fn() }),
}));

// --- EventSource ----------------------------------------------------------------

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), cb]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    act(() => {
      for (const cb of this.listeners.get(type) ?? []) cb({ data: JSON.stringify(data) } as MessageEvent<string>);
    });
  }
}
const liveStream = () => FakeEventSource.instances.filter((s) => !s.closed).at(-1) as FakeEventSource;

// --- fixtures --------------------------------------------------------------------

const HOUR_MS = 3_600_000;
const now = new Date();
now.setUTCMinutes(0, 0, 0);
const HOURS = Array.from({ length: 24 }, (_, i) => new Date(now.getTime() - (23 - i) * HOUR_MS).toISOString().replace(".000Z", "Z"));
const LAST_HOUR = HOURS[23];

function logItem(id: string, level = "INFO", createdAt = LAST_HOUR) {
  return {
    id,
    created_at: createdAt.replace("Z", ""),
    level,
    service: "system",
    action: "server_error",
    summary: `Event ${id}`,
    detail: { path: "/query", note: "<script>alert(1)</script>" },
    message: "",
    user_id: null,
    ip_address: "10.0.0.1",
    request_id: `req-${id}`,
  };
}

function kpi(value: number | null, unit: string | null) {
  return { value, unit, sparkline: HOURS.map((_, i) => (i % 5 === 0 ? 3 : 0)), window: "24h", source: "chat_messages" };
}

const OVERVIEW = {
  audience: "overview",
  window_hours: 24,
  hours: HOURS,
  health: {
    status: "Incident",
    window_minutes: 15,
    signals: {
      error_rate: { value: 12, unit: "%", status: "Incident", degraded_at: 2, incident_at: 10 },
      p95_latency: { value: 1800, unit: "ms", status: "Healthy", degraded_at: 4000, incident_at: 8000 },
      quota_utilization: { value: 20, unit: "%", status: "Healthy", degraded_at: 80, incident_at: 95 },
    },
  },
  kpis: {
    requests: kpi(120, null),
    error_rate: kpi(12, "%"),
    latency_p50: kpi(900, "ms"),
    latency_p95: kpi(1800, "ms"),
    active_users: kpi(7, null),
    groundedness: kpi(91.5, "%"),
    blocked_or_flagged: kpi(3, null),
    cost_today: kpi(1.25, "USD"),
  },
  incidents: [logItem("inc-1", "CRITICAL", HOURS[23].replace(":00:00Z", ":05:00Z"))],
  online_quality_kpis: { sampled_count: 10, violation_rate: 0.1 },
};

const MONITORING = {
  window_hours: 24,
  latency_unit: "ms",
  api_requests: 138,
  queries_24h: 45,
  active_users: 4,
  active_queries: 0,
  avg_latency_ms: 1010,
  total_latency_ms: 1010,
  p95_latency_ms: 2400,
  error_rate: 1.5,
  error_counts: { errors: 3, requests: 200, window: "24h", source: "x" },
  traces_total: 1119,
  stage_latency: {
    embedding: { avg_ms: null, sample_count: 0, unit: "ms" },
    retrieval: { avg_ms: 0.18, sample_count: 45, unit: "ms" },
    reranker: { avg_ms: null, sample_count: 0, unit: "ms" },
    llm: { avg_ms: 1200, sample_count: 45, unit: "ms" },
    guardrail_pre: { avg_ms: 4, sample_count: 45, unit: "ms" },
    guardrail_post: { avg_ms: 6, sample_count: 45, unit: "ms" },
  },
  stage_percentiles: {
    retrieval: { p50_ms: 0.15, p95_ms: 0.3, p99_ms: 0.4, sample_count: 45, unit: "ms" },
    llm: { p50_ms: 1100, p95_ms: 2200, p99_ms: 2600, sample_count: 45, unit: "ms" },
    total: { p50_ms: 1000, p95_ms: 2400, p99_ms: 2900, sample_count: 45, unit: "ms" },
  },
  hourly: HOURS.map((hour) => ({
    hour,
    requests: 5,
    errors: 1,
    error_rate: 20,
    latency_samples: 2,
    latency_avg_ms: 1000,
    latency_p50_ms: 900,
    latency_p95_ms: 2000,
  })),
  guardrail_flag_breakdown: { low_retrieval_confidence: 4, spam: 1 },
  outcome_mix: { answered: 30, ungrounded: 5 },
  counts: {
    api_requests: { value: 138, window: "since_process_start", source: "in_memory_counter" },
    queries_24h: { value: 45, window: "24h", source: "latency_metrics" },
    active_users: { value: 4, window: "24h", source: "chat_sessions" },
    active_queries: { value: 0, window: "now", source: "in_memory_counter" },
    errors: { value: 3, window: "24h", source: "chat_messages.outcome+audit_log.server_error" },
    traces_total: { value: 1119, window: "all_time", source: "latency_metrics" },
  },
  process_cpu_percent: 12,
  process_ram_mb: 512,
  process_ram_percent: 40,
  azure_quota_telemetry: { chat: { tpm_headroom_pct: 70, rpm_headroom_pct: 90 }, embedding: { tpm_headroom_pct: 95 } },
  azure_quota_series: [],
  online_quality_kpis: { sampled_count: 10, violation_rate: 0.1, mean_faithfulness: 4.5 },
};

function trace(i: number) {
  return {
    request_id: `req-${i}`,
    user_id: null,
    conversation_id: `conv-${i}`,
    ip_address: "10.0.0.1",
    timestamp: LAST_HOUR,
    status: "ok",
    outcome: "answered",
    guardrail_flags: [],
    total_ms: 1200 + i,
    latency_unit: "ms",
    latency_bucket: "moderate",
    slowest_stage: { stage: "llm", ms: 1000, unit: "ms" },
    query: `question ${i}`,
    tokens: { prompt: 10, completion: 5, reasoning: null, embedding: 3, total: 18 },
    cost_usd: 0.0021,
  };
}

// The Logs tab's default rows-per-page (LOGS_PAGE_SIZE in LogsView).
const LOG_PAGE_SIZE = 10;

function logsPage(cursor: string | null, limit = LOG_PAGE_SIZE) {
  const pageNo = cursor ? Number(cursor.slice(1)) : 0;
  return {
    // Deeper pages use lightweight rows (no detail / request link) to keep the
    // deep-page walk fast; page 0 exercises the full row rendering.
    items: Array.from({ length: limit }, (_, i) => {
      const item = logItem(`p${pageNo}-${i}`, i % 7 === 0 ? "ERROR" : "INFO");
      return pageNo === 0 ? item : { ...item, detail: {}, request_id: null };
    }),
    total: 6000,
    total_estimate: null,
    total_is_estimate: false,
    level_counts: { CRITICAL: 1, ALERT: 2, ERROR: 30, WARNING: 4, INFO: 5963 },
    level_counts_capped: false,
    limit,
    next_cursor: `c${pageNo + 1}`,
    has_more: true,
  };
}

// All-time AI Usage card: only llm_failed is a failure (blocked / conversational / ungrounded are not).
const LLM_USAGE = {
  window: "all_time",
  latency_window_hours: 720,
  requests: 6649,
  successful: 6549,
  failed: 100,
  input_tokens: 19452869,
  output_tokens: 3896232,
  total_tokens: 23349101,
  average_latency_ms: 1200,
  outcomes: { answered: 3842, ungrounded: 900, blocked: 1200, conversational: 607, llm_failed: 100, unknown: 0 },
};

const calls: URL[] = [];
const callsTo = (path: string) => calls.filter((u) => u.pathname === `/api/proxy/admin/governance/${path}`);

function respond(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, statusText: "", json: () => Promise.resolve(body) } as Response);
}

function mockFetch(input: RequestInfo | URL): Promise<Response> {
  const url = new URL(String(input), "http://localhost");
  calls.push(url);
  const path = url.pathname.replace("/api/proxy/admin", "");
  if (path === "/governance/overview") return respond(OVERVIEW);
  if (path === "/governance/monitoring") return respond(MONITORING);
  if (path === "/usage/llm") return respond(LLM_USAGE);
  if (path === "/governance/logs") {
    const limit = Number(url.searchParams.get("limit"));
    return respond(limit === 1 ? { ...logsPage(null), items: [] } : logsPage(url.searchParams.get("cursor"), limit));
  }
  if (path === "/governance/traces/page") {
    return respond({
      items: [0, 1, 2].map(trace),
      total: 1119,
      total_estimate: null,
      total_is_estimate: false,
      limit: 10,
      next_cursor: "t2",
      has_more: true,
    });
  }
  const detail = path.match(/^\/governance\/traces\/(.+)$/);
  if (detail) {
    const i = Number(decodeURIComponent(detail[1]).replace("req-", ""));
    return respond({
      ...trace(i),
      grounded: true,
      stages: {
        unit: "ms",
        total_ms: 1200,
        other_ms: 20,
        stages: [
          { stage: "embedding", start_ms: 0, duration_ms: 40 },
          { stage: "reranker", start_ms: null, duration_ms: null },
          { stage: "llm", start_ms: 60, duration_ms: 1000 },
        ],
      },
      sources: [{ chunk_id: "ch-1", document_id: "doc-1", source_name: "Essential Eight.pdf", score: 0.8123 }],
      flags: ["low_retrieval_confidence"],
    });
  }
  return respond({});
}

function renderPage(url: string) {
  history.reset(url);
  return render(
    <MotionProvider>
      <RangeProvider>
        <GovernancePage />
      </RangeProvider>
    </MotionProvider>,
  );
}

beforeEach(() => {
  calls.length = 0;
  FakeEventSource.instances = [];
  queryCache.clear();
  history.push.mockClear();
  history.replace.mockClear();
  vi.stubGlobal("fetch", vi.fn(mockFetch));
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.documentElement.classList.remove("dark");
});

describe("Governance journey", () => {
  it("overview incident marker links into Logs, which queries with those filters", async () => {
    renderPage("/admin/governance");
    const banner = await screen.findByRole("status", { name: "System health: Incident" });
    expect(banner).toHaveTextContent("Incident in progress");
    expect(screen.getByText("Latency p95")).toBeInTheDocument();
    expect(screen.getAllByText("1.8 s").length).toBeGreaterThan(0);
    expect(screen.getByText("Event inc-1")).toBeInTheDocument();

    const marker = screen.getByTestId("incident-marker");
    const href = marker.getAttribute("href") ?? "";
    expect(href).toContain("tab=Logs");
    expect(href).toContain("level=CRITICAL%2CALERT");
    expect(href).toContain(`at=${encodeURIComponent(LAST_HOUR)}`);

    // Follow the link (Next's <Link> is a plain anchor in tests).
    cleanup();
    calls.length = 0;
    renderPage(href.replace("http://localhost", ""));
    await screen.findByText("Event p0-0");
    const req = callsTo("logs").find((u) => u.searchParams.get("limit") === "10");
    expect(req?.searchParams.getAll("level")).toEqual(["CRITICAL", "ALERT"]);
    expect(req?.searchParams.get("from")).toBe(new Date(LAST_HOUR).toISOString());
    expect(req?.searchParams.get("to")).toBe(new Date(new Date(LAST_HOUR).getTime() + HOUR_MS).toISOString());
    expect(screen.getByRole("button", { name: /critical/i })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^warning/i })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/Hour of/)).toBeInTheDocument();
  });

  it("applies live SSE events on the Overall tab (incident highlight, health change)", async () => {
    renderPage("/admin/governance");
    await screen.findByRole("status", { name: "System health: Incident" });
    const es = liveStream();
    expect(es.url).toBe("/api/proxy/admin/governance/stream");
    act(() => es.onopen?.());
    expect(screen.getByText("Live")).toBeInTheDocument();

    es.emit("incident", logItem("inc-2", "ALERT"));
    const row = (await screen.findByText("Event inc-2")).closest("li");
    expect(row).toHaveAttribute("data-fresh", "true");

    es.emit("health", { status: "Healthy", previous: "Incident", health: { ...OVERVIEW.health, status: "Healthy" } });
    expect(await screen.findByRole("status", { name: "System health: Healthy" })).toBeInTheDocument();

    es.emit("monitoring", { kpis: { requests: 999 } });
    expect(await screen.findByText("999")).toBeInTheDocument();
  });

  it("?request_id= opens the trace drawer with waterfall, sources, flags and conversation link", async () => {
    renderPage("/admin/governance?tab=Traces&request_id=req-1");
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Essential Eight.pdf");
    expect(within(dialog).getByRole("link", { name: "Open conversation" })).toHaveAttribute(
      "href",
      "/admin/conversations/conv-1",
    );
    expect(within(dialog).getByText("score 0.812")).toBeInTheDocument();
    expect(within(dialog).getByText("Low retrieval confidence")).toBeInTheDocument();
    expect(within(dialog).getByText(/No data: Reranker/)).toBeInTheDocument();
    expect(within(dialog).getByRole("group", { name: "Pipeline stages" })).toBeInTheDocument();

    await screen.findByText("question 2");
    fireEvent.click(within(dialog).getByRole("button", { name: "Next record" }));
    expect(history.current()).toContain("request_id=req-2");
    await within(await screen.findByRole("dialog")).findByRole("link", { name: "Open conversation" });
    await waitFor(() =>
      expect(within(screen.getByRole("dialog")).getByRole("link", { name: "Open conversation" })).toHaveAttribute(
        "href",
        "/admin/conversations/conv-2",
      ),
    );
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Previous record" }));
    expect(history.current()).toContain("request_id=req-1");

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(history.current()).not.toContain("request_id"));
  });

  it("Traces has no User column and pages 10 at a time with a rows-per-page selector", async () => {
    renderPage("/admin/governance?tab=Traces");
    await screen.findByRole("button", { name: "req-0" });
    expect(screen.queryByRole("columnheader", { name: "User" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "IP" })).toBeInTheDocument();
    expect(screen.getByLabelText(/rows per page/i)).toHaveValue("10");
    expect(screen.getByTestId("cursor-page")).toHaveTextContent("Page 1 of 112");
  });

  it("opens the drawer from a row button (keyboard reachable)", async () => {
    renderPage("/admin/governance?tab=Traces");
    const button = await screen.findByRole("button", { name: "req-0" });
    fireEvent.click(button);
    expect(history.current()).toContain("request_id=req-0");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    const page = callsTo("traces/page")[0];
    expect(page.searchParams.get("limit")).toBe("10");
    expect(page.searchParams.get("from")).toBeTruthy();
  });

  it("Logs 'Older' uses the returned cursor; deep pages beyond 2,000 rows are reachable", async () => {
    renderPage("/admin/governance?tab=Logs");
    await screen.findByText("Event p0-0");
    expect(callsTo("logs").find((u) => u.searchParams.get("limit") === "10")?.searchParams.has("cursor")).toBe(false);

    expect(screen.getByTestId("cursor-page")).toHaveTextContent("Page 1 of 600");

    // Rows per page restarts paging from the newest page at the new size.
    fireEvent.change(screen.getByLabelText(/rows per page/i), { target: { value: "100" } });
    await waitFor(() =>
      expect(callsTo("logs").find((u) => u.searchParams.get("limit") === "100")?.searchParams.has("cursor")).toBe(false),
    );
    await waitFor(() => expect(screen.getByTestId("cursor-page")).toHaveTextContent("Page 1 of 60"));

    const older = () => screen.getByRole("button", { name: /older/i });
    const withCursor = (cursor: string) =>
      callsTo("logs").filter((u) => u.searchParams.get("cursor") === cursor && u.searchParams.get("limit") === "100");
    await waitFor(() => expect(older()).toBeEnabled());
    // The next page is warmed while idle, so "Older" renders from cache.
    await waitFor(() => expect(withCursor("c1")).toHaveLength(1));
    fireEvent.click(older());
    expect(screen.getByText("Event p1-0")).toBeInTheDocument();
    expect(withCursor("c1")).toHaveLength(1);

    // 21 pages x 100 rows = 2,100 rows, past the old 2,000-row ceiling (G12).
    for (let page = 2; page <= 21; page += 1) {
      fireEvent.click(older());
      await waitFor(() => expect(withCursor(`c${page}`).length).toBeGreaterThan(0));
      await waitFor(() => expect(older()).toBeEnabled());
    }
    expect(await screen.findByText("Event p21-0")).toBeInTheDocument();
    expect(withCursor("c21").at(-1)?.searchParams.get("limit")).toBe("100");
    expect(screen.getByTestId("cursor-page")).toHaveTextContent("Page 22 of 60");

    fireEvent.click(screen.getByRole("button", { name: /newer/i }));
    await screen.findByText("Event p20-0");

    // "Newest" jumps straight back to the first page.
    fireEvent.click(screen.getByRole("button", { name: /newest/i }));
    await screen.findByText("Event p0-0");
    expect(screen.getByTestId("cursor-page")).toHaveTextContent("Page 1 of 60");
  }, 120_000);

  it("Logs renders level-count chips, service names, escaped JSON and a filtered CSV export", async () => {
    renderPage("/admin/governance?tab=Logs&service=system&q=boom");
    await screen.findByText("Event p0-0");
    expect(screen.getByRole("button", { name: /error\s*30/i })).toBeInTheDocument();
    expect(screen.getAllByText("System").length).toBeGreaterThan(0);

    const exportLink = screen.getByTestId("logs-export");
    const exportUrl = new URL(exportLink.getAttribute("href") ?? "", "http://localhost");
    expect(exportUrl.pathname).toBe("/api/proxy/admin/governance/logs/export.csv");
    expect(exportUrl.searchParams.get("service")).toBe("system");
    expect(exportUrl.searchParams.get("q")).toBe("boom");

    fireEvent.click(screen.getAllByRole("button", { name: /show event detail/i })[0]);
    expect(screen.getByTestId("json-viewer").textContent).toContain("<script>alert(1)</script>");
    expect(document.querySelector("script")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^error/i }));
    expect(history.current()).toContain("level=ERROR");
  });

  it("Logs hides the CSV export without usage:export", async () => {
    permissions.granted = false;
    try {
      renderPage("/admin/governance?tab=Logs");
      await screen.findByText("Event p0-0");
      expect(screen.queryByTestId("logs-export")).not.toBeInTheDocument();
    } finally {
      permissions.granted = true;
    }
  });

  it("Monitoring labels each count, shows no-data stages and refreshes from SSE", async () => {
    renderPage("/admin/governance?tab=Monitoring");
    await screen.findByText("1,119");
    const labels = screen.getAllByTestId("count-label").map((n) => n.textContent);
    expect(labels).toContain("since restart · process counter");
    expect(labels).toContain("24h · latency records");
    expect(labels).toContain("all time · latency records");
    expect(screen.getByText(/No data: Embedding, Reranker/)).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(1);

    const before = callsTo("monitoring").length;
    const es = liveStream();
    es.emit("snapshot", OVERVIEW);
    expect(callsTo("monitoring")).toHaveLength(before);

    const realNow = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(realNow + MONITORING_REFETCH_MIN_MS + 1);
    es.emit("monitoring", { kpis: { requests: 1 } });
    await waitFor(() => expect(callsTo("monitoring").length).toBe(before + 1));
    es.emit("monitoring", { kpis: { requests: 2 } });
    expect(callsTo("monitoring").length).toBe(before + 1);
  });

  it("Monitoring count tiles and outcome rows link to the rows behind them", async () => {
    renderPage("/admin/governance?tab=Monitoring");
    await screen.findByText("1,119");
    const errorTile = screen.getByRole("link", { name: /^Error rate:/ });
    expect(errorTile.getAttribute("href")).toContain("tab=Traces");
    expect(errorTile.getAttribute("href")).toContain("status=failed");
    expect(screen.getByRole("link", { name: /^p95 latency:/ }).getAttribute("href")).toContain("latency_bucket=slow");
    expect(screen.getByRole("link", { name: /^Active visitors:/ })).toHaveAttribute("href", "/admin/usage?tab=users");
    fireEvent.click(errorTile);
  });

  it("Monitoring AI Usage counts only LLM errors as failed and formats numbers compactly", async () => {
    renderPage("/admin/governance?tab=Monitoring");
    const failed = await screen.findByRole("link", { name: /^Failed: 100, LLM errors · all time/ });
    expect(failed.getAttribute("href")).toContain("outcome=llm_failed");
    expect(screen.getByTestId("usage-not-failed")).toHaveTextContent(
      "Not counted as failed: 1,200 blocked by guardrails, 607 conversational, 900 ungrounded (all time).",
    );
    expect(screen.getByRole("link", { name: /^Completed: 6,549, no LLM error/ })).toBeInTheDocument();
    const input = screen.getByRole("link", { name: /^Input tokens: 19\.5M/ });
    expect(input).toHaveAttribute("href", "/admin/usage");
    expect(input.querySelector("[title='19,452,869']")).not.toBeNull();
    expect(document.body.textContent).not.toContain("1,94,52,869");
  });

  it("switching tabs keeps state in the URL and only ranged tabs show the date picker", async () => {
    renderPage("/admin/governance?tab=Monitoring");
    await screen.findByText("1,119");
    expect(screen.queryByRole("button", { name: /last 7 days/i })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /logs/i }));
    expect(history.current()).toBe("/admin/governance?tab=Logs");
    await screen.findByText("Event p0-0");
    expect(screen.getByRole("button", { name: /last 7 days/i })).toBeInTheDocument();
  });

  it("renders in dark mode with themed tokens", async () => {
    document.documentElement.classList.add("dark");
    renderPage("/admin/governance");
    const banner = await screen.findByRole("status", { name: "System health: Incident" });
    await waitFor(() => expect(banner.style.backgroundColor).toBe("rgb(44, 15, 14)"));
  });
});
