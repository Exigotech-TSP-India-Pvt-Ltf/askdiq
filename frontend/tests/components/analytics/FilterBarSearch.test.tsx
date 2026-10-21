/** The Analytics search collapses to an icon on the tab row below 1440 px. */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FilterBar } from "@/components/admin/analytics/FilterBar";
import { DEFAULT_STATE } from "@/components/admin/analytics/url-state";

afterEach(cleanup);

function renderBar() {
  return render(<FilterBar state={DEFAULT_STATE} update={vi.fn()} clearFilters={vi.fn()} />);
}

describe("Analytics search on the tab row", () => {
  it("is an icon button below 1440 px and always a field from 1440 px", () => {
    renderBar();
    const toggle = screen.getByRole("button", { name: "Show search" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveClass("inline-flex", "min-[1440px]:hidden");
    const field = screen.getByLabelText("Search").closest("[data-search-field]");
    expect(field).toHaveClass("hidden", "min-[1440px]:block", "min-w-[9rem]");
  });

  it("opens on click, keeps its value, and Esc collapses it back to the icon", () => {
    renderBar();
    const toggle = screen.getByRole("button", { name: "Show search" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const input = screen.getByLabelText("Search");
    expect(input.closest("[data-search-field]")).toHaveClass("block");
    fireEvent.change(input, { target: { value: "jobs" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveValue("jobs");
    expect(toggle).toHaveAttribute("title", "Search: jobs");
  });
});
