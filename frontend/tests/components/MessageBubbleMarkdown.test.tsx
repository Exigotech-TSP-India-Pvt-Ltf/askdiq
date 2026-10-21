import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { MessageBubble } from "@/components/MessageBubble";
import type { ChatMessage, Citation } from "@/lib/types";

const answer = (content: string): ChatMessage => ({
  id: "msg-md-1",
  role: "assistant",
  content,
  createdAt: Date.now(),
});

const E8: Citation = {
  n: 1,
  document_id: "doc-1",
  title: "Essential Eight maturity",
  url: "https://docs.deployiq.com.au/e8",
  label: "deployiq",
  host: "docs.deployiq.com.au",
};
const ISO: Citation = { n: 2, document_id: "doc-2", title: "ISO 27001 scope", url: null, label: "ISO-Scope", host: null };

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

  it("renders source pills inside paragraphs, list items and table cells (OR-7 + OR-8)", () => {
    const { container } = render(
      <MessageBubble
        message={{
          ...answer(
            "We cover eight strategies.[1]\n\n## Key points\n- **MFA** for admins [1][2]\n- Backups [2]\n\n| Aspect | E8 |\n| --- | --- |\n| Scope | Eight [1] |",
          ),
          citations: [E8, ISO],
        }}
      />,
    );
    const bubble = container.querySelector('[data-tour="chat-answer"]') as HTMLElement;
    expect(screen.getByRole("heading", { level: 2, name: "Key points" })).toBeInTheDocument();
    expect(bubble).not.toHaveTextContent(/\[\d\]/);

    const items = within(bubble).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("MFA").closest("strong")).toBeInTheDocument();
    expect(within(items[0]).getByRole("link", { name: /^Source: deployiq/ })).toHaveAttribute("href", E8.url);
    expect(within(items[0]).getByRole("button", { name: "Source: ISO-Scope, ISO 27001 scope" })).toBeInTheDocument();
    expect(within(items[1]).getByRole("button", { name: /^Source: ISO-Scope/ })).toBeInTheDocument();

    const cell = within(screen.getByRole("region", { name: "Table" })).getByRole("cell", { name: /Eight/ });
    expect(within(cell).getByRole("link", { name: /^Source: deployiq/ })).toBeInTheDocument();
    expect(within(bubble.querySelector("p") as HTMLElement).getByRole("link", { name: /^Source: deployiq/ })).toBeInTheDocument();
  });
});
