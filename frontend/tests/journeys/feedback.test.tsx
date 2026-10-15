/**
 * Phase F5 integration journey (planchanges4 F5.5): a thumbs-down with reason
 * chips sends the right payload, then keyboard triage in the Review Queue
 * (j, k, p) calls the right endpoints, the promoted row leaves the queue and
 * shows up in Golden Cases. `fetch` is mocked; the real data flow is covered
 * by the backend `test_feedback_integration.py`.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import { FeedbackWorkspace } from "@/components/admin/feedback/FeedbackWorkspace";
import { queryCache } from "@/lib/query-cache";
import { submitMessageFeedback, type FeedbackDetails, type FeedbackItem } from "@/lib/feedback-api";
import {
  Providers,
  analyticsFixture,
  history,
  installFetch,
  makeGolden,
  makeItem,
  page,
  type FetchCall,
} from "../components/feedback/fixtures";

vi.mock("next/navigation", async () => (await import("../components/feedback/fixtures")).navigationMock());

let calls: FetchCall[];
let queue: FeedbackItem[];
const promoted: string[] = [];

beforeEach(() => {
  queryCache.clear();
  promoted.length = 0;
  queue = [makeItem({ user_query: "Alpha question" }), makeItem({ user_query: "Bravo question" }), makeItem({ user_query: "Charlie question" })];
  calls = installFetch((call) => {
    if (call.method === "POST" && call.path.startsWith("/sessions/")) {
      const body = call.body as { rating: number; reason_tags: string[]; corrected_answer: string | null };
      // The rating lands in the queue as a new item.
      queue = [makeItem({ user_query: "Chat question", reason_tags: body.reason_tags, corrected_answer: body.corrected_answer }), ...queue];
      return { json: { message_id: "m-1", session_id: "s-1", ...body, comment: null, triage_status: "new", created_at: "", updated_at: "" } };
    }
    if (call.path.startsWith("/admin/feedback/analytics")) return { json: analyticsFixture };
    if (call.method === "GET" && call.path.startsWith("/admin/feedback?")) {
      return { json: page(queue.filter((it) => !promoted.includes(it.message_id))) };
    }
    if (call.method === "POST" && call.path.endsWith("/promote-to-test")) {
      const messageId = call.path.split("/")[3];
      promoted.push(messageId);
      return { json: { status: "promoted", case_id: "fb_case_7", query: "Bravo question", created: true, feedback_ids: [] } };
    }
    if (call.method === "GET" && call.path.startsWith("/admin/feedback/golden")) {
      const items = promoted.length ? [makeGolden({ id: "fb_case_7", query: "Bravo question" })] : [];
      return { json: { items, total: items.length, limit: 100, offset: 0 } };
    }
    return undefined;
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("Phase F5 feedback journey", () => {
  it("chat reason chips -> keyboard triage -> promoted row leaves the queue and appears in Golden Cases", async () => {
    // 1. Chat: thumbs down, pick reasons, write what it should have said, send.
    const onFeedback = (id: string, rating: number, comment?: string, details?: FeedbackDetails) =>
      submitMessageFeedback("t", "s-1", id, { rating, comment, ...details }).then(() => undefined);
    const chat = render(
      <Providers>
        <MessageBubble message={{ id: "m-1", role: "assistant", content: "Keys rotate yearly.", createdAt: 0 }} onFeedback={onFeedback} />
      </Providers>,
    );
    fireEvent.click(screen.getByRole("button", { name: /unhelpful response/i }));
    const panel = await screen.findByRole("dialog", { name: /what went wrong/i });
    fireEvent.click(within(panel).getByRole("button", { name: "Wrong" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Incomplete" }));
    fireEvent.change(within(panel).getByLabelText(/what should it have said/i), { target: { value: "Every 90 days." } });
    fireEvent.click(within(panel).getByRole("button", { name: /send feedback/i }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/sessions/s-1/messages/m-1/feedback")).toHaveLength(2));
    const sent = calls.filter((c) => c.path === "/sessions/s-1/messages/m-1/feedback");
    expect(sent[0].body).toEqual({ rating: 1, comment: null, reason_tags: [], corrected_answer: null });
    expect(sent[1].body).toEqual({ rating: 1, comment: null, reason_tags: ["wrong", "incomplete"], corrected_answer: "Every 90 days." });
    chat.unmount();

    // 2. Admin: the queue, keyboard triage.
    history.reset("/admin/feedback?tab=queue");
    render(
      <Providers>
        <FeedbackWorkspace />
      </Providers>,
    );
    await waitFor(() => expect(screen.getAllByTestId("queue-row").length).toBeGreaterThanOrEqual(3));
    const target = queue.find((it) => it.user_query === "Bravo question")!;
    const index = queue.indexOf(target);
    for (let i = 0; i < index + 1; i += 1) fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.getAllByTestId("queue-row")[index]).toHaveAttribute("data-focused", "true");
    fireEvent.keyDown(window, { key: "p" });

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.path === `/admin/feedback/${target.message_id}/promote-to-test`)).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Bravo question")).not.toBeInTheDocument());
    expect(screen.getByText("Alpha question")).toBeInTheDocument();

    // 3. The case is in Golden Cases.
    fireEvent.click(screen.getByRole("tab", { name: /golden cases/i }));
    expect(await screen.findByText("fb_case_7")).toBeInTheDocument();
  });
});
