import React from "react";
import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage } from "@/lib/types";

describe("MessageBubble Component", () => {
  it("renders user message with bubble styling and copy button", () => {
    const message: ChatMessage = {
      id: "msg-user-1",
      role: "user",
      content: "What is DeployIQ?",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);
    expect(screen.getByText("What is DeployIQ?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("renders assistant message with feedback thumbs up and down buttons", () => {
    const message: ChatMessage = {
      id: "msg-asst-1",
      role: "assistant",
      content: "DeployIQ is an enterprise AI security platform.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} />);
    expect(screen.getByText("DeployIQ is an enterprise AI security platform.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^helpful response$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /unhelpful response/i })).toBeInTheDocument();
  });

  it("submits thumbs up rating (rating = 5) in one click without opening a dialog", async () => {
    const onFeedback = vi.fn().mockResolvedValue(undefined);
    const message: ChatMessage = {
      id: "msg-asst-2",
      role: "assistant",
      content: "Security posture verified.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} onFeedback={onFeedback} />);
    const thumbsUpBtn = screen.getByRole("button", { name: /^helpful response$/i });
    fireEvent.click(thumbsUpBtn);

    await waitFor(() => {
      expect(onFeedback).toHaveBeenCalledWith("msg-asst-2", 5, undefined);
    });
    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(thumbsUpBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("submits thumbs down (rating = 1), pops the reason chips and saves an optional note", async () => {
    const onFeedback = vi.fn().mockResolvedValue(undefined);
    const message: ChatMessage = {
      id: "msg-asst-3",
      role: "assistant",
      content: "Uncertain answer.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} onFeedback={onFeedback} />);
    const thumbsDownBtn = screen.getByRole("button", { name: /unhelpful response/i });
    fireEvent.click(thumbsDownBtn);

    await waitFor(() => {
      expect(onFeedback).toHaveBeenCalledWith("msg-asst-3", 1, undefined);
    });
    expect(screen.getByRole("dialog", { name: /what went wrong/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /add feedback note/i }));
    const textarea = screen.getByPlaceholderText(/what went well or what could be improved/i);
    fireEvent.change(textarea, { target: { value: "Needs more detailed references." } });

    const saveBtn = screen.getByRole("button", { name: /save note/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onFeedback).toHaveBeenCalledWith("msg-asst-3", 1, "Needs more detailed references.");
    });
  });

  it("rehydrates active rating state when feedbackRating is provided", () => {
    const message: ChatMessage = {
      id: "msg-asst-4",
      role: "assistant",
      content: "Answer with prior feedback.",
      createdAt: Date.now(),
      feedbackRating: 5,
    };

    render(<MessageBubble message={message} />);
    const thumbsUpBtn = screen.getByRole("button", { name: /^helpful response$/i });
    expect(thumbsUpBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("displays error message if onFeedback fails", async () => {
    const onFeedback = vi.fn().mockRejectedValue(new Error("Rate limit exceeded"));
    const message: ChatMessage = {
      id: "msg-asst-5",
      role: "assistant",
      content: "Test message.",
      createdAt: Date.now(),
    };

    render(<MessageBubble message={message} onFeedback={onFeedback} />);
    const thumbsUpBtn = screen.getByRole("button", { name: /^helpful response$/i });
    fireEvent.click(thumbsUpBtn);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Rate limit exceeded");
    });
  });
});
