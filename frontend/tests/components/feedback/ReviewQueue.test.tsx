/**
 * planchanges4 F5.3: keyboard triage calls the right endpoints, optimistic
 * updates with undo, 409 conflict toast, operator read-only, bulk actions,
 * the detail drawer and idle prefetch of the next page.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FeedbackWorkspace } from "@/components/admin/feedback/FeedbackWorkspace";
import { queryCache } from "@/lib/query-cache";
import type { FeedbackItem } from "@/lib/feedback-api";
import { Providers, analyticsFixture, history, installFetch, makeItem, page, type FetchCall, type Role } from "./fixtures";

vi.mock("next/navigation", async () => (await import("./fixtures")).navigationMock());

let items: FeedbackItem[];
let calls: FetchCall[];

function route(overrides: { patchStatus?: number; nextCursor?: string | null } = {}) {
  return installFetch((call) => {
    if (call.path.startsWith("/admin/feedback/analytics")) return { json: analyticsFixture };
    if (call.method === "GET" && call.path.startsWith("/admin/feedback?")) {
      const cursor = new URLSearchParams(call.path.split("?")[1]).get("cursor");
      if (cursor) return { json: page([makeItem({ user_query: "From page two" })]) };
      return { json: page(items, overrides.nextCursor ?? null) };
    }
    if (call.method === "PATCH") {
      if (overrides.patchStatus) return { status: overrides.patchStatus, json: { detail: "Stale version" } };
      const id = call.path.split("/").pop();
      const current = items.find((it) => it.id === id) ?? items[0];
      const body = call.body as { version: number; triage_status?: FeedbackItem["triage_status"] };
      return { json: { ...current, triage_status: body.triage_status ?? current.triage_status, version: body.version + 1 } };
    }
    if (call.method === "POST" && call.path.endsWith("/promote-to-test")) {
      return { json: { status: "promoted", case_id: "fb_case_9", query: "q", created: true, feedback_ids: [] } };
    }
    if (call.method === "POST" && call.path === "/admin/feedback/bulk") {
      const body = call.body as { ids: string[]; action: string };
      return { json: { action: body.action, updated: body.ids.length, ids: body.ids, promoted_case_ids: [] } };
    }
    return undefined;
  });
}

function renderQueue(role: Role = "admin", url = "/admin/feedback?tab=queue") {
  history.reset(url);
  return render(
    <Providers role={role}>
      <FeedbackWorkspace />
    </Providers>,
  );
}

const rows = () => screen.queryAllByTestId("queue-row");
const press = (key: string) => fireEvent.keyDown(window, { key });

beforeEach(() => {
  queryCache.clear();
  items = [makeItem({ user_query: "First question" }), makeItem({ user_query: "Second question" }), makeItem({ user_query: "Third question" })];
});

afterEach(() => {
  vi.unstubAllGlobals();
  history.push.mockClear();
  history.replace.mockClear();
});

describe("Review Queue triage workspace", () => {
  it("lists only new feedback by default and moves focus with j / k", async () => {
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    const listCall = calls.find((c) => c.path.startsWith("/admin/feedback?"));
    expect(listCall?.path).toContain("triage_status=new");
    expect(rows()[0]).toHaveAttribute("data-focused", "true");

    press("j");
    expect(rows()[1]).toHaveAttribute("data-focused", "true");
    press("j");
    press("k");
    expect(rows()[1]).toHaveAttribute("data-focused", "true");
  });

  it("r marks the focused row reviewed via PATCH with its version, and the row leaves the new queue", async () => {
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    press("j");
    press("r");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.path).toBe(`/admin/feedback/${items[1].id}`);
    expect(patch?.body).toEqual({ version: 1, triage_status: "reviewed" });
    await waitFor(() => expect(screen.queryByText("Second question")).not.toBeInTheDocument());
    expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent("Marked reviewed");
  });

  it("p promotes through promote-to-test (message id) and the promoted row animates out", async () => {
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    press("p");
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.path === `/admin/feedback/${items[0].message_id}/promote-to-test`)).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("First question")).not.toBeInTheDocument());
    expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent("Promoted to golden cases");
  });

  it("d dismisses with an Undo toast that restores the row to new with the new version", async () => {
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    press("d");
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument());
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ version: 1, triage_status: "dismissed" });

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({ version: 2, triage_status: "new" });
  });

  it("shows the conflict toast and rolls back when the PATCH returns 409", async () => {
    calls = route({ patchStatus: 409 });
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    press("r");
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent("Changed by someone else"),
    );
    expect(screen.getByText("First question")).toBeInTheDocument();
  });

  it("Enter opens the drawer with the full exchange, sources and scores; next moves to the following row", async () => {
    items[0] = { ...items[0], answer: "Rotate keys every 90 days.", reason_tags: ["wrong"], guardrail_flags: ["pii"] };
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    press("Enter");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Rotate keys every 90 days.")).toBeInTheDocument();
    expect(within(dialog).getByText("0.812")).toBeInTheDocument();
    expect(within(dialog).getByText("pii")).toBeInTheDocument();
    expect(within(dialog).getByText("Wrong")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Write the correct answer")).toBeEnabled();
    expect(within(dialog).getByRole("link", { name: /open full view/i })).toHaveAttribute(
      "href",
      `/admin/conversations/${items[0].conversation_id}`,
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "Next record" }));
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByText("Answer " + items[1].id.slice(-4))).toBeInTheDocument());
  });

  it("bulk-marks the selected rows reviewed in one POST /bulk", async () => {
    calls = route();
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select feedback 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select feedback 3" }));
    const bulkBar = screen.getByRole("group", { name: "Bulk actions" });
    expect(bulkBar).toHaveTextContent("2 selected");
    fireEvent.click(within(bulkBar).getByRole("button", { name: /mark reviewed/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/admin/feedback/bulk")).toBe(true));
    expect(calls.find((c) => c.path === "/admin/feedback/bulk")?.body).toEqual({
      ids: [items[0].id, items[2].id],
      action: "reviewed",
    });
  });

  it("gives an operator a read-only queue: no checkboxes, no action buttons, shortcuts do nothing", async () => {
    calls = route();
    renderQueue("operator");
    await waitFor(() => expect(rows()).toHaveLength(3));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark reviewed/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /promote to test/i })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();

    press("r");
    press("d");
    press("p");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(calls.some((c) => c.method !== "GET")).toBe(false);

    press("Enter");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Write the correct answer")).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: /dismiss/i })).not.toBeInTheDocument();
  });

  it("prefetches the next page while idle", async () => {
    calls = route({ nextCursor: "abc" });
    renderQueue();
    await waitFor(() => expect(rows()).toHaveLength(3));
    await waitFor(() => expect(calls.some((c) => c.path.includes("cursor=abc"))).toBe(true));
  });

  it("filter changes from the toolbar round-trip through the URL", async () => {
    calls = route();
    renderQueue("admin", "/admin/feedback?tab=queue&rating=1&status=all");
    await waitFor(() => expect(rows()).toHaveLength(3));
    const listCall = calls.find((c) => c.path.startsWith("/admin/feedback?"));
    expect(listCall?.path).toContain("rating=1");
    expect(listCall?.path).not.toContain("triage_status");
    const chips = screen.getByRole("region", { name: "Active filters" });
    fireEvent.click(within(chips).getByRole("button", { name: "Remove filter Rating: 1" }));
    expect(history.params().get("rating")).toBeNull();
  });
});
