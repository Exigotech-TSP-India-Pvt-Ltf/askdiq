/**
 * "No loading after a click": list pages warm the details of their visible
 * rows (one batched request, or a bounded number of single ones), rows warm
 * on hover / focus, the next page warms while idle, and a click then renders
 * the drawer from cache without any loading state.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import ConversationsPage from "@/app/(app)/admin/visitors/page";
import { DataTable } from "@/components/admin/DataTable";
import { QueryInspectorDrawer } from "@/components/admin/usage/QueryInspectorDrawer";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { conversationKey } from "@/lib/audience-data";
import { api } from "@/lib/api";
import {
  INTENT_DELAY_MS,
  QueryCache,
  prefetchBatch,
  prefetchEach,
  queryCache,
  usePrefetchNextPage,
  usePrefetchRows,
} from "@/lib/query-cache";
import { queryDetailKey, usePrefetchQueryDetails } from "@/lib/use-usage-data";
import { callsTo, envelope, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import { CONV_ID, CONV_ID_2, conversationDetail, conversationRows, conversationStats } from "../helpers/entity-fixtures";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const batchOf = <T,>(items: Record<string, T>, missing: string[] = []) => ({ items, missing });

// ---------------------------------------------------------------------------
// prefetchBatch / prefetchEach / usePrefetchRows
// ---------------------------------------------------------------------------

describe("row detail prefetch", () => {
  it("one batch request for the stale ids; each item lands under its own key", async () => {
    const cache = new QueryCache();
    cache.setData(["d", "fresh"], { id: "fresh" });
    const fetchBatch = vi.fn(async (ids: string[]) => batchOf(Object.fromEntries(ids.map((id) => [id, { id }])), []));
    await prefetchBatch(["a", "fresh", "b", "a"], (id) => ["d", id], fetchBatch, { cache });
    expect(fetchBatch).toHaveBeenCalledTimes(1);
    expect(fetchBatch.mock.calls[0][0]).toEqual(["a", "b"]);
    expect(cache.getData(["d", "a"])).toEqual({ id: "a" });
    expect(cache.getData(["d", "b"])).toEqual({ id: "b" });
    // Now fresh: nothing left to ask for.
    await prefetchBatch(["a", "b"], (id) => ["d", id], fetchBatch, { cache });
    expect(fetchBatch).toHaveBeenCalledTimes(1);
  });

  it("caps the ids per batch, never repeats ids a running batch is filling, and swallows failures", async () => {
    const cache = new QueryCache();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const fetchBatch = vi.fn(async (ids: string[]) => {
      await gate;
      return batchOf(Object.fromEntries(ids.map((id) => [id, id])));
    });
    const ids = Array.from({ length: 40 }, (_, i) => `id-${i}`);
    const first = prefetchBatch(ids, (id) => ["c", id], fetchBatch, { cache, max: 25 });
    const second = prefetchBatch(ids.slice(0, 5), (id) => ["c", id], fetchBatch, { cache });
    release();
    await Promise.all([first, second]);
    expect(fetchBatch).toHaveBeenCalledTimes(1);
    expect(fetchBatch.mock.calls[0][0]).toHaveLength(25);

    const failing = vi.fn(async () => {
      throw new Error("down");
    });
    await expect(prefetchBatch(["x"], (id) => ["c", id], failing, { cache })).resolves.toBeUndefined();
    expect(cache.getData(["c", "x"])).toBeUndefined();
  });

  it("prefetchEach keeps at most `concurrency` requests in flight", async () => {
    const cache = new QueryCache();
    let inFlight = 0;
    let peak = 0;
    const fetchOne = (id: string) => async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 5));
      inFlight -= 1;
      return id;
    };
    await prefetchEach(["a", "b", "c", "d", "e"], (id) => ["e", id], fetchOne, { cache, concurrency: 2 });
    expect(peak).toBe(2);
    expect(["a", "b", "c", "d", "e"].map((id) => cache.getData(["e", id]))).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("usePrefetchRows warms once idle and again after a live update (new version) marks them stale", async () => {
    const cache = new QueryCache();
    const fetchBatch = vi.fn(async (ids: string[]) => batchOf(Object.fromEntries(ids.map((id) => [id, { id, v: 1 }]))));
    const { rerender } = renderHook(
      ({ version }) => usePrefetchRows(["a", "b"], (id) => ["rows", id], { batch: fetchBatch }, { cache, version }),
      { initialProps: { version: 1 } },
    );
    await waitFor(() => expect(cache.getData(["rows", "b"])).toEqual({ id: "b", v: 1 }));
    expect(fetchBatch).toHaveBeenCalledTimes(1);
    cache.invalidate("rows");
    rerender({ version: 2 });
    await waitFor(() => expect(fetchBatch).toHaveBeenCalledTimes(2));
    expect(fetchBatch.mock.calls[1][0]).toEqual(["a", "b"]);
  });

  it("does nothing without ids (no permission / no token)", async () => {
    const fetchBatch = vi.fn();
    renderHook(() => usePrefetchRows(null, (id) => ["n", id], { batch: fetchBatch }));
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchBatch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// DataTable intent: pointerenter / focus with a dwell, Enter opens
// ---------------------------------------------------------------------------

describe("DataTable row intent", () => {
  const rows = [
    { id: "r1", name: "First" },
    { id: "r2", name: "Second" },
  ];
  const columns = [{ key: "name", header: "Name" }];

  it("hover warms the row after the dwell; leaving earlier cancels", () => {
    vi.useFakeTimers();
    const onRowIntent = vi.fn();
    render(<DataTable columns={columns} data={rows} onRowClick={vi.fn()} onRowIntent={onRowIntent} />);
    const first = screen.getByText("First").closest("tr") as HTMLElement;
    fireEvent.pointerEnter(first);
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS - 20));
    fireEvent.pointerLeave(first);
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS));
    expect(onRowIntent).not.toHaveBeenCalled();

    fireEvent.pointerEnter(first);
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS));
    expect(onRowIntent).toHaveBeenCalledWith(rows[0]);
  });

  it("keyboard focus warms the row and Enter opens it", () => {
    vi.useFakeTimers();
    const onRowIntent = vi.fn();
    const onRowClick = vi.fn();
    render(<DataTable columns={columns} data={rows} onRowClick={onRowClick} onRowIntent={onRowIntent} />);
    const second = screen.getByText("Second").closest("tr") as HTMLElement;
    expect(second).toHaveAttribute("tabindex", "0");
    fireEvent.focus(second);
    act(() => vi.advanceTimersByTime(INTENT_DELAY_MS));
    expect(onRowIntent).toHaveBeenCalledWith(rows[1]);
    fireEvent.keyDown(second, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledWith(rows[1]);
  });
});

// ---------------------------------------------------------------------------
// Conversations: the visible rows' previews arrive in one batch; a click
// opens the preview with no loading state and no detail request
// ---------------------------------------------------------------------------

describe("Conversations preview from the batch", () => {
  beforeEach(() => history.reset("/admin/visitors?tab=conversations"));

  it("prefetches the visible rows in one request, then the click renders at once", async () => {
    const routes: Route[] = [
      ["GET", /^\/admin\/conversations$/, () => ({ body: envelope(conversationRows, 2) })],
      ["GET", /^\/admin\/conversations\/stats$/, () => ({ body: conversationStats })],
      [
        "GET",
        /^\/admin\/conversations\/batch$/,
        (url) => ({
          body: batchOf(
            Object.fromEntries(url.searchParams.getAll("ids").map((id) => [id, { ...conversationDetail, focus_turn: null }])),
          ),
        }),
      ],
      ["GET", new RegExp(`^/admin/conversations/${CONV_ID}$`), () => ({ body: conversationDetail })],
    ];
    const calls = mockFetch(routes);
    renderPage(<ConversationsPage />);
    await screen.findByText("Leave policy");

    await waitFor(() => expect(queryCache.getData(conversationKey(CONV_ID))).toBeDefined());
    const batches = callsTo(calls, "/admin/conversations/batch");
    expect(batches).toHaveLength(1);
    expect(batches[0].searchParams.getAll("ids")).toEqual([CONV_ID, CONV_ID_2]);

    fireEvent.click(screen.getByText("Leave policy"));
    const dialog = await screen.findByRole("dialog");
    // Rendered from cache: the turns are there with no skeleton in between.
    expect(within(dialog).queryByRole("status", { name: "Loading conversation" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("And sick leave?")).toBeInTheDocument();
    expect(callsTo(calls, `/admin/conversations/${CONV_ID}`)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Usage query inspector: batch-warmed, opens without "Loading…"
// ---------------------------------------------------------------------------

describe("Query inspector from the batch", () => {
  it("opens with the detail on screen and never shows the loading state", async () => {
    const detail = {
      request_id: "req-1",
      query: "How many leave days?",
      answer: "Twenty.",
      cost_usd: 0.01,
      grounded: true,
      tokens: { prompt: 10, cached_prompt: 0, reasoning: 0, visible: 5, query_embed: 1, guardrail_embed: 1, total_billed: 17 },
    };
    const batch = vi.spyOn(api, "getQueryDetailsBatch").mockResolvedValue(batchOf({ "req-1": detail }, ["req-x"]));
    const single = vi.spyOn(api, "getQueryDetail");
    renderHook(() => usePrefetchQueryDetails("t", ["req-1", "req-x"]));
    await waitFor(() => expect(queryCache.getData(queryDetailKey("req-1"))).toEqual(detail));
    expect(batch).toHaveBeenCalledTimes(1);
    expect(batch.mock.calls[0][1]).toEqual(["req-1", "req-x"]);

    render(
      <MotionProvider>
        <QueryInspectorDrawer requestId="req-1" token="t" onClose={vi.fn()} />
      </MotionProvider>,
    );
    expect(screen.queryByText(/Loading query accounting/)).not.toBeInTheDocument();
    expect(screen.getByText("How many leave days?")).toBeInTheDocument();
    expect(single).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Next page while idle
// ---------------------------------------------------------------------------

describe("next page while idle", () => {
  it("warms the next page key so 'Next' renders from cache", async () => {
    const cache = new QueryCache();
    const fetchPage = vi.fn(async () => ({ items: ["n1"], next_cursor: null }));
    renderHook(() => usePrefetchNextPage(["page", "c2"], fetchPage, { cache }));
    await waitFor(() => expect(cache.getData(["page", "c2"])).toEqual({ items: ["n1"], next_cursor: null }));
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
