/**
 * planchanges4 F5.1: thumbs-down pops the reason chips and the optional
 * "what should it have said?" box; the payload carries the allow-listed tags
 * and the corrected answer; a resubmission updates the same feedback; thumbs
 * up stays one click; success shows a toast.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import { MAX_CORRECTED_ANSWER_CHARS, submitMessageFeedback } from "@/lib/feedback-api";
import type { ChatMessage } from "@/lib/types";
import { Providers, installFetch } from "./fixtures";

const message: ChatMessage = { id: "m-1", role: "assistant", content: "Keys rotate yearly.", createdAt: 0 };

afterEach(() => vi.unstubAllGlobals());

function renderBubble(onFeedback = vi.fn().mockResolvedValue(undefined)) {
  render(
    <Providers>
      <MessageBubble message={message} onFeedback={onFeedback} />
    </Providers>,
  );
  return onFeedback;
}

describe("Chat reason chips", () => {
  it("thumbs up is one click and shows a thank-you toast", async () => {
    const onFeedback = renderBubble();
    fireEvent.click(screen.getByRole("button", { name: /^helpful response$/i }));
    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith("m-1", 5, undefined));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("Thanks for the feedback")).toBeInTheDocument();
  });

  it("thumbs down pops the chips; Send submits the selected tags and corrected answer", async () => {
    const onFeedback = renderBubble();
    fireEvent.click(screen.getByRole("button", { name: /unhelpful response/i }));
    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith("m-1", 1, undefined));
    const panel = screen.getByRole("dialog", { name: /what went wrong/i });
    const chips = within(panel).getAllByRole("button", { pressed: false });
    expect(chips.map((c) => c.textContent)).toEqual(["Wrong", "Incomplete", "Off topic", "Unsafe", "Too slow", "Unclear"]);

    fireEvent.click(within(panel).getByRole("button", { name: "Wrong" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Too slow" }));
    expect(within(panel).getByRole("button", { name: "Wrong" })).toHaveAttribute("aria-pressed", "true");
    const box = within(panel).getByLabelText(/what should it have said/i);
    fireEvent.change(box, { target: { value: "Keys rotate every 90 days." } });
    expect(within(panel).getByText(`26/${MAX_CORRECTED_ANSWER_CHARS}`)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: /send feedback/i }));
    await waitFor(() =>
      expect(onFeedback).toHaveBeenLastCalledWith("m-1", 1, undefined, {
        reason_tags: ["wrong", "too_slow"],
        corrected_answer: "Keys rotate every 90 days.",
      }),
    );
    expect(await screen.findByText("Feedback sent")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /what went wrong/i })).not.toBeInTheDocument());
  });

  it("caps the corrected answer at 4000 characters", async () => {
    renderBubble();
    fireEvent.click(screen.getByRole("button", { name: /unhelpful response/i }));
    const box = await screen.findByLabelText(/what should it have said/i);
    fireEvent.change(box, { target: { value: "x".repeat(MAX_CORRECTED_ANSWER_CHARS + 50) } });
    expect(box).toHaveValue("x".repeat(MAX_CORRECTED_ANSWER_CHARS));
  });

  it("rolls the thumb back and shows the error when the rating fails", async () => {
    renderBubble(vi.fn().mockRejectedValue(new Error("Rate limit exceeded")));
    const down = screen.getByRole("button", { name: /unhelpful response/i });
    fireEvent.click(down);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Rate limit exceeded"));
    expect(down).toHaveAttribute("aria-pressed", "false");
  });
});

describe("submitMessageFeedback payload", () => {
  it("posts the allow-listed tags and corrected answer; a resubmission posts to the same message (upsert)", async () => {
    const calls = installFetch((call) =>
      call.method === "POST"
        ? {
            json: {
              message_id: "m-1",
              session_id: "s-1",
              rating: 1,
              comment: null,
              reason_tags: ["wrong"],
              corrected_answer: null,
              triage_status: "new",
              created_at: "",
              updated_at: "",
            },
          }
        : undefined,
    );
    await submitMessageFeedback("t", "s-1", "m-1", { rating: 1, reason_tags: ["wrong", "wrong"], corrected_answer: "  Better.  " });
    await submitMessageFeedback("t", "s-1", "m-1", { rating: 1, reason_tags: ["incomplete"] });
    expect(calls).toHaveLength(2);
    expect(calls[0].path).toBe("/sessions/s-1/messages/m-1/feedback");
    expect(calls[0].body).toEqual({ rating: 1, comment: null, reason_tags: ["wrong"], corrected_answer: "Better." });
    expect(calls[1].path).toBe(calls[0].path);
    expect(calls[1].body).toEqual({ rating: 1, comment: null, reason_tags: ["incomplete"], corrected_answer: null });
  });
});
