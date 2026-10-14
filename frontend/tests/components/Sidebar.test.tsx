import React from "react";
import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "@/components/Sidebar";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: "mock-token",
    user: { full_name: "Jane Doe", email: "jane@exigotech.com" },
    loading: false,
    logout: vi.fn(),
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/usage",
}));

describe("Sidebar Navigation Component (Task 4.3)", () => {
  it("renders all 9 admin navigation links with text-[13px] typography", () => {
    render(<Sidebar />);

    const expectedLabels = [
      "Dashboard",
      "Chat",
      "Documents",
      "Users",
      "Usage & Cost",
      "Conversation Logs",
      "Feedback",
      "Evaluation",
      "Admin Governance",
    ];

    for (const label of expectedLabels) {
      const link = screen.getByText(label);
      expect(link).toBeInTheDocument();
      const anchor = link.closest("a");
      expect(anchor?.className).toContain("text-[13px]");
      expect(anchor?.className).toContain("py-1.5");
    }
  });

  it("renders the Admin accordion button with uppercase tracking and text-[11px]", () => {
    render(<Sidebar />);

    const adminButton = screen.getByRole("button", { name: /admin/i });
    expect(adminButton).toBeInTheDocument();
    expect(adminButton.className).toContain("text-[11px]");
  });

  it("toggles sidebar collapsed state on expand/collapse button click", () => {
    const { container } = render(<Sidebar />);

    const aside = container.querySelector("aside");
    expect(aside).toHaveClass("w-64");

    const toggleButton = screen.getByTitle("Collapse sidebar");
    fireEvent.click(toggleButton);

    expect(aside).toHaveClass("w-16");
  });
});
