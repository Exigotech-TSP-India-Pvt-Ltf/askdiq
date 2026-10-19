import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ClarificationChips } from "@/components/chat/ClarificationChips";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage, Clarification } from "@/lib/types";

const clarification: Clarification = {
  question: "Which pricing do you mean?",
  original_query: "pricing",
  allow_free_text: true,
  options: [
    { id: "T1", label: "Platform plans", source_name: "platform-pricing.md" },
    { id: "T2", label: "Support tiers", source_name: "support.md" },
  ],
};

describe("ClarificationChips", () => {
  it("renders the options as a labelled group of keyboard-reachable buttons", () => {
    render(<ClarificationChips clarification={clarification} onSelect={vi.fn()} />);
    const group = screen.getByRole("group", { name: "Which pricing do you mean?" });
    const buttons = within(group).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Platform plans", "Support tiers", "Something else…"]);
    for (const b of buttons) expect(b).toHaveAttribute("type", "button");
    expect(within(group).getByRole("button", { name: "Platform plans" })).toHaveAttribute("aria-pressed", "false");
    expect(group).toHaveAccessibleDescription(/Pick the closest option/);
  });

  it("sends the picked option and offers a free-text path", () => {
    const onSelect = vi.fn();
    const onSomethingElse = vi.fn();
    render(<ClarificationChips clarification={clarification} onSelect={onSelect} onSomethingElse={onSomethingElse} />);
    fireEvent.click(screen.getByRole("button", { name: "Support tiers" }));
    expect(onSelect).toHaveBeenCalledWith(clarification.options[1]);
    fireEvent.click(screen.getByRole("button", { name: "Something else…" }));
    expect(onSomethingElse).toHaveBeenCalledTimes(1);
  });

  it("disables every chip once an option is chosen and marks the choice", () => {
    const onSelect = vi.fn();
    render(<ClarificationChips clarification={clarification} onSelect={onSelect} chosenId="T2" />);
    const picked = screen.getByRole("button", { name: "Support tiers" });
    expect(picked).toHaveAttribute("aria-pressed", "true");
    for (const b of screen.getAllByRole("button")) expect(b).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Platform plans" }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByText("Option chosen.")).toBeInTheDocument();
  });

  it("hides the free-text chip when the server does not allow it", () => {
    render(<ClarificationChips clarification={{ ...clarification, allow_free_text: false }} onSelect={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Something else…" })).not.toBeInTheDocument();
  });
});

describe("MessageBubble with a clarifying question", () => {
  const message: ChatMessage = {
    id: "a1",
    role: "assistant",
    content: "Which pricing do you mean?\n\n1. Platform plans\n2. Support tiers\n\nReply with the number…",
    createdAt: Date.now(),
    clarification,
  };

  it("shows the question with chips instead of the numbered text fallback", () => {
    const onClarify = vi.fn();
    render(<MessageBubble message={message} onClarify={onClarify} onSomethingElse={vi.fn()} />);
    expect(screen.getByText("Which pricing do you mean?", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByText(/1\. Platform plans/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Platform plans" }));
    expect(onClarify).toHaveBeenCalledWith(clarification.options[0]);
  });

  it("locks the chips when the conversation has moved on", () => {
    render(<MessageBubble message={message} onClarify={vi.fn()} clarificationDisabled />);
    expect(screen.getByRole("button", { name: "Platform plans" })).toBeDisabled();
  });
});
