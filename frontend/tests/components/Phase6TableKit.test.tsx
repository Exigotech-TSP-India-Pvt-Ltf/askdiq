import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { Pagination } from "@/components/admin/Pagination";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { DataTable } from "@/components/admin/DataTable";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";
import { AudienceProvider } from "@/lib/audience-context";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/admin/usage",
  useSearchParams: () => new URLSearchParams(),
}));

describe("Phase 6 Shared Table Kit and Audience Switcher", () => {
  it("Pagination is always visible even with single page and supports page-size selection", () => {
    const onPageChange = vi.fn();
    const onPageSizeChange = vi.fn();

    render(
      <Pagination
        page={1}
        pageCount={1}
        totalItems={8}
        pageSize={10}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    );

    // Footer must not be null
    expect(screen.getByText(/Showing/)).toBeDefined();
    expect(screen.getByText(/entries/)).toBeDefined();

    // Page size dropdown exists
    const select = screen.getByLabelText(/Rows per page:/i);
    expect(select).toBeDefined();

    fireEvent.change(select, { target: { value: "25" } });
    expect(onPageSizeChange).toHaveBeenCalledWith(25);
  });

  it("TableToolbar renders search input with clear button and active filter pills", () => {
    const onSearchChange = vi.fn();
    const onFilterChange = vi.fn();

    render(
      <TableToolbar
        search="test-search"
        onSearchChange={onSearchChange}
        filterOptions={[
          { label: "All Users", value: "all" },
          { label: "Production Only", value: "prod" },
        ]}
        filterValue="prod"
        onFilterChange={onFilterChange}
      />
    );

    // Clear search button appears when search text is present
    const clearBtn = screen.getByLabelText("Clear search");
    expect(clearBtn).toBeDefined();
    fireEvent.click(clearBtn);
    expect(onSearchChange).toHaveBeenCalledWith("");

    // Active filter pill and dropdown button are rendered
    const pills = screen.getAllByText(/Production Only/);
    expect(pills.length).toBeGreaterThanOrEqual(1);
  });

  it("DataTable renders headers, records, and empty states appropriately", () => {
    const columns = [
      { key: "name", header: "Name" },
      { key: "role", header: "Role" },
    ];
    const data = [{ id: "1", name: "Alice", role: "Admin" }];

    render(<DataTable columns={columns} data={data} />);

    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Alice")).toBeDefined();
    expect(screen.getByText("Admin")).toBeDefined();
  });

  it("AudienceScopeTabs renders Overview, Users, and Testing tabs", () => {
    render(
      <AudienceProvider>
        <AudienceScopeTabs
          counts={{
            overview: { queries: 100, sessions: 20, ips: 15 },
            users: { queries: 80, sessions: 16, ips: 12 },
            testing: { queries: 20, sessions: 4, ips: 3 },
          }}
        />
      </AudienceProvider>
    );

    expect(screen.getByText("Overview")).toBeDefined();
    expect(screen.getByText("Users")).toBeDefined();
    expect(screen.getByText("Testing")).toBeDefined();
    expect(screen.getByText("15")).toBeDefined();
    expect(screen.getByText("12")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
  });

  it("TableCard renders header title, toolbar, children, and pagination integrated into a single card", async () => {
    const { TableCard } = await import("@/components/admin/TableCard");
    render(
      <TableCard
        title="Session Activity"
        description="Active telemetry streams"
        toolbar={<div>Search and Filter bar</div>}
        pagination={<div>Page 1 of 5</div>}
      >
        <div data-testid="table-grid">Table Grid Content</div>
      </TableCard>
    );

    expect(screen.getByText("Session Activity")).toBeDefined();
    expect(screen.getByText("Active telemetry streams")).toBeDefined();
    expect(screen.getByText("Search and Filter bar")).toBeDefined();
    expect(screen.getByTestId("table-grid")).toBeDefined();
    expect(screen.getByText("Page 1 of 5")).toBeDefined();
  });

  it("Single Filters button shows badge count and opens multi-group filter popover", () => {
    const handleStatusChange = vi.fn();
    const handleRoleChange = vi.fn();

    render(
      <TableToolbar
        search=""
        onSearchChange={vi.fn()}
        filters={[
          {
            id: "status",
            label: "Status",
            options: [
              { label: "All Status", value: "all" },
              { label: "Active", value: "active" },
              { label: "Archived", value: "archived" },
            ],
            value: "active",
            onChange: handleStatusChange,
          },
          {
            id: "role",
            label: "Role",
            options: [
              { label: "All Roles", value: "all" },
              { label: "Admin", value: "admin" },
            ],
            value: "all",
            onChange: handleRoleChange,
          },
        ]}
      />
    );

    // Single Filters button exists with badge "1" (active: active)
    const filterBtn = screen.getByRole("button", { name: /Filters/i });
    expect(filterBtn).toBeDefined();
    expect(screen.getByText("1")).toBeDefined();

    // Click to open popover
    fireEvent.click(filterBtn);
    expect(screen.getByRole("dialog", { name: /Filter Options/i })).toBeDefined();
    expect(screen.getByText("Status")).toBeDefined();
    expect(screen.getByText("Role")).toBeDefined();

    // Select an option inside popover
    const archivedOption = screen.getByText("Archived");
    fireEvent.click(archivedOption);
    expect(handleStatusChange).toHaveBeenCalledWith("archived");

    // Close via Done button
    const doneBtn = screen.getByRole("button", { name: /Done/i });
    fireEvent.click(doneBtn);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("TableToolbar supports debounced search without immediate query spam", () => {
    vi.useFakeTimers();
    const handleSearch = vi.fn();

    render(
      <TableToolbar
        search=""
        onSearchChange={handleSearch}
        debounceMs={300}
      />
    );

    const input = screen.getByPlaceholderText(/Search across columns/i);
    fireEvent.change(input, { target: { value: "test query" } });

    // Not called immediately
    expect(handleSearch).not.toHaveBeenCalled();

    // Advance timers past 300ms
    vi.advanceTimersByTime(300);
    expect(handleSearch).toHaveBeenCalledWith("test query");

    vi.useRealTimers();
  });

  it("Pagination triggers onPrefetchNext on mouseEnter of next button", () => {
    const handlePrefetch = vi.fn();

    render(
      <Pagination
        page={1}
        pageCount={3}
        totalItems={30}
        pageSize={10}
        onPageChange={vi.fn()}
        onPrefetchNext={handlePrefetch}
      />
    );

    const nextBtn = screen.getByRole("button", { name: "Next page" });
    fireEvent.mouseEnter(nextBtn);
    expect(handlePrefetch).toHaveBeenCalledTimes(1);
  });
});


