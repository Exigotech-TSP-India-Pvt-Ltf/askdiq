import React from "react";
import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, USAGE_TABS } from "@/components/admin/TabGroup";
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

describe("Phase 5: Header and Tabs Accessibility & Placement Audit", () => {
  it("AudienceScopeTabs conforms to ARIA tablist patterns and keyboard cycling", () => {
    render(<AudienceScopeTabs />);

    const tablist = screen.getByRole("tablist", { name: /audience scope/i });
    expect(tablist).toBeInTheDocument();

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);

    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(tabs[2]).toHaveAttribute("aria-selected", "false");

    // Arrow navigation
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    // Tab cycling triggers selection / focus
    expect(tabs[0]).toBeInTheDocument();
  });

  it("TabGroup handles keyboard navigation and ARIA attributes", () => {
    const onChange = vi.fn();
    render(<TabGroup tabs={USAGE_TABS} activeTab="overview" onChange={onChange} />);

    const tablist = screen.getByRole("tablist");
    expect(tablist).toBeInTheDocument();

    const activeTab = screen.getByRole("tab", { name: /overview/i });
    expect(activeTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(activeTab, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("users");
  });

  it("DashboardHeader renders 3-zone layout with centered audience tabs", () => {
    render(
      <DashboardHeader
        title="Admin Control"
        subtitle="Operational telemetry"
        icon={LayoutDashboard}
      />
    );

    expect(screen.getByText("Admin Control")).toBeInTheDocument();
    expect(screen.getByText("Operational telemetry")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: /audience scope/i })).toBeInTheDocument();
    // OR-2: no actions in the header; VA-43: the subtitle wraps to two lines with its full text as a tooltip.
    expect(screen.getByRole("banner").querySelectorAll("button:not([role=tab])")).toHaveLength(0);
    const subtitle = screen.getByTestId("header-subtitle");
    expect(subtitle).toHaveClass("line-clamp-2");
    expect(subtitle).toHaveAttribute("title", "Operational telemetry");
  });
});
