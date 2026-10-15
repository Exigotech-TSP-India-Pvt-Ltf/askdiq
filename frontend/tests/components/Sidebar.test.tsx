import React from "react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NAV_ITEMS, Sidebar, isNavItemEnabled } from "@/components/Sidebar";
import { MobileTopBar } from "@/components/shell/MobileTopBar";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { MobileNavProvider } from "@/lib/mobile-nav-context";

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

describe("Sidebar nav registry, icons and active indicator (PR-17)", () => {
  // The collapse test above stores "collapsed"; start each case expanded.
  beforeEach(() => window.localStorage.clear());

  it("pre-registers Analytics and User Management but does not render them yet (Settings is live, PR-26)", () => {
    render(<Sidebar />);
    const planned = NAV_ITEMS.filter((item) => !isNavItemEnabled(item)).map((item) => item.label);
    expect(planned).toEqual(["Analytics", "User Management"]);
    for (const label of planned) expect(screen.queryByText(label)).not.toBeInTheDocument();
    expect(NAV_ITEMS.find((item) => item.label === "User Management")?.badgeKey).toBe("pendingAccess");
    expect(NAV_ITEMS.find((item) => item.label === "Settings")?.permission).toBe("branding:write");
  });

  it("marks only the current page active, with the layoutId indicator and 18px / 1.75 icons", () => {
    render(<Sidebar />);
    const active = screen.getByRole("link", { current: "page" });
    expect(active).toHaveTextContent("Usage & Cost");
    expect(screen.getAllByRole("link", { current: "page" })).toHaveLength(1);
    expect(screen.getAllByTestId("nav-active-indicator")).toHaveLength(1);
    const icon = active.querySelector("svg");
    expect(icon).toHaveClass("h-[18px]", "w-[18px]");
    expect(icon).toHaveAttribute("stroke-width", "1.75");
  });

  it("names the icon-only controls", () => {
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});

describe("Mobile off-canvas sidebar (F0.6.6)", () => {
  beforeEach(() => window.localStorage.clear());

  it("opens from the hamburger, traps focus, closes on Esc and returns focus", async () => {
    const user = userEvent.setup();
    render(
      <MotionProvider>
        <MobileNavProvider>
          <MobileTopBar />
          <Sidebar />
        </MobileNavProvider>
      </MotionProvider>,
    );
    const hamburger = screen.getByRole("button", { name: "Open navigation" });
    expect(hamburger).toHaveAttribute("aria-expanded", "false");
    hamburger.focus();
    fireEvent.click(hamburger);

    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    expect(hamburger).toHaveAttribute("aria-expanded", "true");
    expect(within(drawer).getByRole("link", { name: /feedback/i })).toHaveAttribute("href", "/admin/feedback");
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "Close navigation" })).toHaveFocus());

    // Shift+Tab from the first control wraps to the last link inside the drawer.
    await user.tab({ shift: true });
    expect(within(drawer).getByRole("link", { name: /^settings$/i })).toHaveFocus();
    await user.tab();
    expect(within(drawer).getByRole("button", { name: "Close navigation" })).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
    expect(hamburger).toHaveFocus();
  });

  it("closes when a link is chosen", async () => {
    render(
      <MotionProvider>
        <MobileNavProvider>
          <MobileTopBar />
          <Sidebar />
        </MobileNavProvider>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    fireEvent.click(within(drawer).getByRole("link", { name: /documents/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
  });

  it("the desktop aside is hidden below md", () => {
    const { container } = render(<Sidebar />);
    expect(container.querySelector("aside")).toHaveClass("hidden", "md:flex");
  });
});
