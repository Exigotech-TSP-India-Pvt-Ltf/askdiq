import React from "react";
import fs from "fs";
import path from "path";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Network, Upload } from "lucide-react";
import { PageToolbar } from "@/components/admin/PageToolbar";
import { HeaderIconButton } from "@/components/ui/HeaderIconButton";
import { RangeProvider } from "@/lib/range-context";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/admin",
  useSearchParams: () => new URLSearchParams(),
}));

const ROOT = path.resolve(__dirname, "../..");
const read = (file: string) => fs.readFileSync(path.join(ROOT, file), "utf-8");

describe("PageToolbar (OR-2)", () => {
  it("puts the tabs on the left and every action, the filters and the date filter right-aligned, in that order", () => {
    render(
      <RangeProvider>
        <PageToolbar
          tabsTour="page-tabs"
          tabs={<div role="tablist" aria-label="Sections" />}
          actions={<HeaderIconButton icon={Network} label="Test IPs" showLabel onClick={() => undefined} />}
          filters={<div data-testid="chips" />}
          dateRange
        />
      </RangeProvider>,
    );
    const row = screen.getByRole("tablist").closest("[data-page-toolbar]")!;
    expect(row).toHaveClass("flex", "flex-wrap", "items-center");
    expect(screen.getByRole("tablist").parentElement).toHaveAttribute("data-tour", "page-tabs");
    const right = row.querySelector("[data-toolbar-actions]")!;
    expect(right).toHaveClass("ml-auto", "justify-end", "flex-wrap");
    const kids = Array.from(right.children);
    const action = screen.getByRole("button", { name: "Test IPs" }).closest("[data-toolbar-actions] > *")!;
    expect(kids.indexOf(action)).toBe(0);
    expect(kids[1]).toBe(screen.getByTestId("chips"));
    expect(kids[2]).toHaveAttribute("data-tour", "shell-date-range");
  });

  it("keeps the actions on the tab row from lg up: one non-wrapping row, scrolling tabs, a fixed-size cluster", () => {
    render(
      <RangeProvider>
        <PageToolbar
          tabs={<div role="tablist" aria-label="Sections" className="flex flex-wrap" />}
          actions={<HeaderIconButton icon={Network} label="Compare with benchmark" text="Compare" showLabel onClick={() => undefined} />}
          dateRange
        />
      </RangeProvider>,
    );
    const tabs = screen.getByRole("tablist").parentElement!;
    const row = tabs.parentElement!;
    const right = row.querySelector("[data-toolbar-actions]")!;
    // Tabs and actions are siblings in the same row container.
    expect(row).toHaveAttribute("data-page-toolbar");
    expect(right.parentElement).toBe(row);
    expect(tabs).toHaveAttribute("data-toolbar-tabs");
    // lg+: the row never wraps; the tabs give way (one line, sideways scroll),
    // the action cluster keeps its size. Below lg the cluster wraps as one group.
    expect(row).toHaveClass("flex-wrap", "lg:flex-nowrap");
    expect(tabs).toHaveClass("min-w-0", "lg:overflow-x-auto", "lg:[&_[role=tablist]]:flex-nowrap", "lg:shrink-[4]");
    expect(right).toHaveClass("ml-auto", "justify-end", "lg:shrink-0");
    expect(right).toContainElement(screen.getByRole("button", { name: /date range/i }));
  });

  it("every action is icon + text from sm up (no icon-only buttons), collapsing to the icon on phones", () => {
    render(<PageToolbar actions={<HeaderIconButton icon={Upload} label="Upload document" variant="primary" showLabel onClick={() => undefined} />} />);
    const button = screen.getByRole("button", { name: "Upload document" });
    expect(button).toHaveTextContent("Upload document");
    expect(button.querySelector("[data-label]")).toHaveClass("hidden", "sm:inline");
    // 44px touch target on phones, 36px from sm.
    expect(button).toHaveClass("h-11", "sm:h-9");
  });

  it("pages without tabs keep the filter in the same corner, and an empty toolbar renders nothing", () => {
    const { container, unmount } = render(
      <RangeProvider>
        <PageToolbar dateRange />
      </RangeProvider>,
    );
    expect(container.querySelector("[data-toolbar-tabs]")).toBeNull();
    expect(container.querySelector("[data-toolbar-actions]")).toHaveClass("ml-auto");
    expect(screen.getByRole("button", { name: /date range/i })).toBeInTheDocument();
    unmount();
    const empty = render(<PageToolbar />);
    expect(empty.container).toBeEmptyDOMElement();
  });
});

describe("Page wiring (OR-2): actions and the date filter live on the tab row, never in the header", () => {
  const RANGED = [
    "app/(app)/admin/page.tsx",
    "components/admin/analytics/AnalyticsView.tsx",
    "app/(app)/admin/governance/page.tsx",
    "app/(app)/admin/usage/page.tsx",
    "app/(app)/admin/visitors/page.tsx",
    "app/(app)/admin/users/[id]/page.tsx",
    "app/(app)/documents/page.tsx",
    "app/(app)/evaluation-live/page.tsx",
    "components/admin/feedback/FeedbackWorkspace.tsx",
  ];

  it.each(RANGED)("%s renders the date filter through PageToolbar (shell-date-range tour anchor)", (file) => {
    const src = read(file);
    expect(src).toContain("<PageToolbar");
    expect(src).toMatch(/dateRange(=\{|\s|\n)/);
    expect(src).not.toContain("showDateRange");
  });

  it("DashboardHeader has no actions or date-range props any more", () => {
    const src = read("components/admin/DashboardHeader.tsx");
    expect(src).not.toMatch(/\bactions\?:/);
    expect(src).not.toContain("showDateRange");
    expect(src).not.toContain("DateRangePicker");
  });

  it("the detail pages put their links on a PageToolbar", () => {
    expect(read("app/(app)/admin/conversations/[id]/page.tsx")).toContain("<PageToolbar");
    expect(read("app/(app)/admin/users/[id]/page.tsx")).toContain("<PageToolbar");
  });
});
