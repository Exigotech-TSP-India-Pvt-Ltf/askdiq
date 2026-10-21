import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage } from "@/lib/types";

const answer = (content: string): ChatMessage => ({
  id: "msg-md-1",
  role: "assistant",
  content,
  createdAt: Date.now(),
});

describe("MessageBubble structured answers (OR-8)", () => {
  it("renders headings, steps and a scrollable table in the bubble", () => {
    render(
      <MessageBubble
        message={answer(
          "The main difference is scope.\n\n## Steps\n1. Connect\n2. Assess\n\n| Aspect | E8 | ISO |\n| --- | --- | --- |\n| Scope | Eight | Annex A |",
        )}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Steps" })).toBeInTheDocument();
    expect(screen.getByRole("list").tagName).toBe("OL");
    const region = screen.getByRole("region", { name: "Table" });
    expect(region).toHaveClass("overflow-x-auto");
    expect(within(region).getAllByRole("columnheader")).toHaveLength(3);
  });

  it("keeps an old plain-text answer as a single paragraph", () => {
    const { container } = render(<MessageBubble message={answer("DeployIQ pilots first.")} />);
    const bubble = container.querySelector('[data-tour="chat-answer"]');
    expect(bubble?.querySelectorAll("p")).toHaveLength(1);
    expect(screen.getByText("DeployIQ pilots first.")).toBeInTheDocument();
  });

  it("shows HTML from the model as text", () => {
    const { container } = render(<MessageBubble message={answer("<script>alert(1)</script>")} />);
    expect(container.querySelector('[data-tour="chat-answer"] script')).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
  });
});
