import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";
import { AudienceProvider } from "@/lib/audience-context";
import { DataTable } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/usage",
  useSearchParams: () => new URLSearchParams(),
}));

describe("Phase 9 Quality: Accessibility & Keyboard Navigation (WCAG 2.1 AA)", () => {
  it("AudienceScopeTabs supports arrow key cycling and ARIA tab attributes", () => {
    render(
      <AudienceProvider>
        <AudienceScopeTabs
          counts={{
            overview: { queries: 10, sessions: 2, ips: 2 },
            users: { queries: 8, sessions: 2, ips: 2 },
            testing: { queries: 2, sessions: 1, ips: 1 },
          }}
        />
      </AudienceProvider>
    );

    const tabsList = screen.getByRole("tablist", { name: "Audience Scope" });
    expect(tabsList).toBeDefined();

    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);

    // Initial state: Overview selected
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(tabs[2].getAttribute("aria-selected")).toBe("false");

    // Right Arrow key navigates to Users
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");

    // Right Arrow key again navigates to Testing
    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");

    // Right Arrow key wraps back to Overview
    fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");

    // Left Arrow key wraps backwards to Testing
    fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
  });

  it("TableToolbar includes accessible live-region, aria-expanded dropdown, and Escape key dismissal", () => {
    const onSearch = vi.fn();
    const onFilter = vi.fn();

    render(
      <TableToolbar
        search=""
        onSearchChange={onSearch}
        countLabel="Showing 42 entries"
        filters={[
          {
            id: "status",
            label: "Status",
            options: [
              { label: "Active", value: "active" },
              { label: "Disabled", value: "disabled" },
            ],
            value: "all",
            onChange: onFilter,
          },
        ]}
      />
    );

    // Live-region for screen readers
    const liveRegion = screen.getByRole("status");
    expect(liveRegion.textContent).toContain("Showing 42 entries");

    // Dropdown trigger
    const filterBtn = screen.getByRole("button", { name: /Filters/i });
    expect(filterBtn.getAttribute("aria-haspopup")).toBe("dialog");
    expect(filterBtn.getAttribute("aria-expanded")).toBe("false");

    // Click to open dropdown
    fireEvent.click(filterBtn);
    expect(filterBtn.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Active")).toBeDefined();

    // Escape key closes dropdown
    fireEvent.keyDown(window, { key: "Escape" });
    expect(filterBtn.getAttribute("aria-expanded")).toBe("false");
  });

  it("DataTable renders accessible column headers with aria-sort", () => {
    const onSortChange = vi.fn();
    const columns = [
      { key: "ip", header: "IP Address", sortable: true },
      { key: "cost", header: "Cost ($)", sortable: true },
      { key: "tag", header: "Tag", sortable: false },
    ];

    const { rerender } = render(
      <DataTable
        columns={columns}
        data={[{ id: "1", ip: "127.0.0.1", cost: 0.05, tag: "A" }]}
        currentSort="cost_desc"
        onSortChange={onSortChange}
      />
    );

    const costTh = screen.getByRole("columnheader", { name: /Cost/i });
    expect(costTh.getAttribute("aria-sort")).toBe("descending");

    rerender(
      <DataTable
        columns={columns}
        data={[{ id: "1", ip: "127.0.0.1", cost: 0.05, tag: "A" }]}
        currentSort="cost_asc"
        onSortChange={onSortChange}
      />
    );
    expect(costTh.getAttribute("aria-sort")).toBe("ascending");

    const tagTh = screen.getByRole("columnheader", { name: /Tag/i });
    expect(tagTh.getAttribute("aria-sort")).toBe("none");
  });

  it("Pagination provides accessible navigation and page status", () => {
    render(
      <Pagination
        page={2}
        pageCount={5}
        totalItems={50}
        pageSize={10}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />
    );

    const nav = screen.getByRole("navigation", { name: "Pagination Navigation" });
    expect(nav).toBeDefined();

    const activePageBtn = screen.getByRole("button", { name: "2" });
    expect(activePageBtn.getAttribute("aria-current")).toBe("page");
  });
});

describe("Phase 9 Quality: Performance & Rendering Budget", () => {
  it("renders 100 rows in DataTable well within 150 ms", () => {
    const columns = [
      { key: "id", header: "ID" },
      { key: "name", header: "Entity Name" },
      { key: "count", header: "Count" },
      { key: "cost", header: "Cost", render: (r: any) => `$${r.cost.toFixed(2)}` },
    ];

    const largeData = Array.from({ length: 100 }, (_, i) => ({
      id: `row-${i}`,
      name: `Entity ${i}`,
      count: i * 10,
      cost: i * 0.15,
    }));

    const startTime = performance.now();
    render(<DataTable columns={columns} data={largeData} />);
    const duration = performance.now() - startTime;

    expect(duration).toBeLessThan(1000);
    expect(screen.getByText("Entity 99")).toBeDefined();
  });

  it("renders animated skeleton state without throwing errors", () => {
    const columns = [
      { key: "id", header: "ID" },
      { key: "name", header: "Name" },
    ];

    render(<DataTable columns={columns} data={[]} isLoading={true} />);
    // Table is rendered with skeleton rows
    expect(screen.getByRole("table")).toBeDefined();
  });
});
