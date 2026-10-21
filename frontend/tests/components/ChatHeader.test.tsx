/** OR-4: the /chat header shows only the assistant name and subtitle. */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/brand-context", () => ({ useBrand: () => ({ assistantName: "AskDIQ" }) }));

import { ChatHeader } from "@/components/chat/ChatHeader";

describe("ChatHeader", () => {
  it("has the title and subtitle, without the logo or the account chip", () => {
    const { container } = render(<ChatHeader />);
    expect(screen.getByRole("heading", { level: 1, name: "AskDIQ" })).toBeInTheDocument();
    expect(screen.getByText(/grounded in your knowledge base/i)).toBeInTheDocument();
    expect(screen.queryByTestId("user-chip")).toBeNull();
    expect(container.querySelector("img, svg")).toBeNull();
    expect(container.querySelector("[data-tour='chat-header']")).toHaveClass("h-20");
  });
});
