import React from "react";
import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";

describe("Phase 6: Unified TableCard and Single Filters Architecture", () => {
  it("renders TableCard with integrated header, toolbar, table slot, and pagination", () => {
    render(
      <TableCard
        title="Admin User Management"
        description="Active system operators"
        badge={<span className="badge">Pro</span>}
        headerActions={<button>Export</button>}
        toolbar={<div data-testid="card-toolbar">Toolbar Controls</div>}
        pagination={<div data-testid="card-pagination">Pagination Footer</div>}
      >
        <div data-testid="table-body">Row 1: Admin</div>
      </TableCard>
    );

    expect(screen.getByText("Admin User Management")).toBeInTheDocument();
    expect(screen.getByText("Active system operators")).toBeInTheDocument();
    expect(screen.getByText("Pro")).toBeInTheDocument();
    expect(screen.getByText("Export")).toBeInTheDocument();
    expect(screen.getByTestId("card-toolbar")).toBeInTheDocument();
    expect(screen.getByTestId("table-body")).toBeInTheDocument();
    expect(screen.getByTestId("card-pagination")).toBeInTheDocument();
  });

  it("handles single filter button popover with active pills strip and dismissal", () => {
    const handleStatusChange = vi.fn();
    const { rerender } = render(
      <TableToolbar
        search="alice"
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
        ]}
      />
    );

    // Active pill rendered
    expect(screen.getByText("Status: Active")).toBeInTheDocument();

    // Dismiss pill via X
    const removePillBtn = screen.getByLabelText("Remove filter Active");
    fireEvent.click(removePillBtn);
    expect(handleStatusChange).toHaveBeenCalledWith("all");

    // Single Filters button
    const filtersBtn = screen.getByRole("button", { name: /Filters/i });
    expect(filtersBtn).toBeInTheDocument();
    fireEvent.click(filtersBtn);

    // Popover dialog open
    expect(screen.getByRole("dialog", { name: "Filter Options" })).toBeInTheDocument();

    // Escape closes popover
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("supports next-page prefetching on hover in Pagination", () => {
    const prefetchSpy = vi.fn();
    render(
      <Pagination
        page={2}
        pageCount={5}
        totalItems={50}
        pageSize={10}
        onPageChange={vi.fn()}
        onPrefetchNext={prefetchSpy}
      />
    );

    const nextBtn = screen.getByRole("button", { name: "Next page" });
    fireEvent.mouseEnter(nextBtn);
    expect(prefetchSpy).toHaveBeenCalledTimes(1);
  });
});
