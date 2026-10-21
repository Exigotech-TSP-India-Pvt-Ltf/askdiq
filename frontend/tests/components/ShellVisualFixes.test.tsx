import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Pagination } from "@/components/admin/Pagination";
import { CursorPagination } from "@/components/admin/CursorPagination";
import { SPOTLIGHT_PADDING, spotlightBox } from "@/lib/tour/dom";

describe("VA-50: tour spotlight", () => {
  it("pads the target uniformly inside its column", () => {
    const box = spotlightBox({ top: 100, left: 300, right: 500, bottom: 140 }, { top: 0, left: 256, right: 1366, bottom: 768 });
    expect(box).toEqual({
      top: 100 - SPOTLIGHT_PADDING,
      left: 300 - SPOTLIGHT_PADDING,
      width: 200 + 2 * SPOTLIGHT_PADDING,
      height: 40 + 2 * SPOTLIGHT_PADDING,
    });
    expect(SPOTLIGHT_PADDING).toBeGreaterThanOrEqual(6);
    expect(SPOTLIGHT_PADDING).toBeLessThanOrEqual(8);
  });

  it("clamps a full-width header to the main column (never over the sidebar or past the page)", () => {
    const box = spotlightBox({ top: 0, left: 256, right: 1366, bottom: 80 }, { top: 0, left: 256, right: 1366, bottom: 768 });
    expect(box.left).toBe(256);
    expect(box.left + box.width).toBe(1366);
    expect(box.top).toBe(0);
  });
});

describe("VA-35: pagination footers on phones", () => {
  it("Pagination hides Rows per page and the 'Showing' words below sm, keeping x–y of N", () => {
    render(<Pagination page={1} pageCount={44} pageSize={25} totalItems={1100} onPageChange={vi.fn()} onPageSizeChange={vi.fn()} />);
    const nav = screen.getByRole("navigation");
    expect(nav).toHaveTextContent("Showing 1–25 of 1,100 entries");
    expect(screen.getByText("Rows per page:").parentElement).toHaveClass("hidden", "sm:flex");
    expect(screen.getByText(/Showing/)).toHaveClass("hidden", "sm:inline");
  });

  it("CursorPagination hides Rows per page and Page n of m below sm", () => {
    render(
      <CursorPagination
        hasPrev
        hasNext
        onPrev={vi.fn()}
        onNext={vi.fn()}
        pageItemCount={25}
        page={1}
        totalItems={1100}
        pageSize={25}
        onPageSizeChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("cursor-page")).toHaveClass("hidden", "sm:inline");
    expect(screen.getByText("Rows per page:").parentElement).toHaveClass("hidden", "sm:flex");
    expect(screen.getByRole("button", { name: /older/i })).toBeInTheDocument();
  });
});
