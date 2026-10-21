import React from "react";
import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import {
  TabGroup,
  USAGE_TABS,
  EVALUATION_TABS,
  GOVERNANCE_TABS,
} from "@/components/admin/TabGroup";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";
import { LayoutDashboard } from "lucide-react";

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({
    audience: "overview",
    setAudience: vi.fn(),
    counts: {
      overview: { queries: 100, sessions: 20, ips: 15 },
      users: { queries: 80, sessions: 16, ips: 12 },
      testing: { queries: 20, sessions: 4, ips: 3 },
    },
  }),
}));

describe("DashboardHeader Component (Task 5.2)", () => {
  it("renders the icon badge, title and centred audience tabs, and no actions (OR-2)", () => {
    render(<DashboardHeader title="Usage & Cost Analytics" icon={LayoutDashboard} badgeCount={42} />);

    expect(screen.getByText("Usage & Cost Analytics")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "Audience Scope" });
    expect(tabs).toBeInTheDocument();
    // VA-33: one switcher; below md it is a full-width row (not hidden).
    expect(tabs.closest("[data-header-centre]")).not.toHaveClass("hidden");
    expect(tabs).toHaveClass("w-full", "md:w-auto");
  });

  it("without the audience switcher it is a single column (no empty centre)", () => {
    const { container } = render(<DashboardHeader title="Settings" icon={LayoutDashboard} showAudienceTabs={false} />);
    expect(container.querySelector("[data-header-centre]")).toBeNull();
    expect(screen.getByRole("banner")).toHaveClass("h-20");
  });
});

describe("AudienceScopeTabs (Task 5.1)", () => {
  it("does not render the removed helper text 'History is dynamically classified by effective test IPs'", () => {
    render(<AudienceScopeTabs />);

    expect(
      screen.queryByText(/History is dynamically classified by effective test IPs/i)
    ).not.toBeInTheDocument();
  });

  it("renders Overview, Users, and Testing tabs in a boxed container", () => {
    render(<AudienceScopeTabs />);

    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByText("Testing")).toBeInTheDocument();

    // The brand gradient now lives on the sliding layoutId pill inside the active tab (PR-17).
    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    expect(within(overviewTab).getByTestId("tab-active-pill").className).toContain("bg-brand-gradient");
    expect(overviewTab.className).toContain("text-white");
    const usersTab = screen.getByRole("tab", { name: /users/i });
    expect(within(usersTab).queryByTestId("tab-active-pill")).toBeNull();
  });
});

describe("TabGroup Component & 20-Tab Icon Audit (Task 5.3)", () => {
  it("renders tabs with dedicated icons and applies brand gradient to active tab", () => {
    const handleChange = vi.fn();
    render(
      <TabGroup
        tabs={USAGE_TABS}
        activeTab="overview"
        onChange={handleChange}
      />
    );

    const activeBtn = screen.getByRole("tab", { name: /overview/i });
    expect(within(activeBtn).getByTestId("tab-active-pill").className).toContain("bg-brand-gradient");
    expect(activeBtn.className).toContain("text-white");

    const usersBtn = screen.getByRole("tab", { name: /all users/i });
    expect(usersBtn.className).toContain("text-fg-muted");
    expect(within(usersBtn).queryByTestId("tab-active-pill")).toBeNull();

    fireEvent.click(usersBtn);
    expect(handleChange).toHaveBeenCalledWith("users");
  });

  it("verifies the 20-tab catalog possesses valid icons across all domains", () => {
    expect(USAGE_TABS).toHaveLength(4);
    for (const tab of USAGE_TABS) {
      expect(tab.icon).toBeDefined();
    }

    expect(EVALUATION_TABS).toHaveLength(12);
    for (const tab of EVALUATION_TABS) {
      expect(tab.icon).toBeDefined();
    }

    expect(GOVERNANCE_TABS).toHaveLength(4);
    for (const tab of GOVERNANCE_TABS) {
      expect(tab.icon).toBeDefined();
    }
  });

  it("supports keyboard navigation with arrow keys", () => {
    const handleChange = vi.fn();
    render(
      <TabGroup
        tabs={USAGE_TABS}
        activeTab="overview"
        onChange={handleChange}
      />
    );

    const activeBtn = screen.getByRole("tab", { name: /overview/i });
    fireEvent.keyDown(activeBtn, { key: "ArrowRight" });
    expect(handleChange).toHaveBeenCalledWith("users");
  });
});
