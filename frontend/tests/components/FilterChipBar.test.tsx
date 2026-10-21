import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FilterChipBar } from "@/components/admin/filters/FilterChipBar";
import { activeCount, type FilterDescriptor } from "@/components/admin/filters/types";
import { STATUS_CHOICES, feedbackFilterDescriptors } from "@/components/admin/feedback/filter-descriptors";
import { TRIAGE_STATUSES } from "@/lib/feedback-api";
import { parseFilters } from "@/lib/feedback-filters";

const status = (value: string): FilterDescriptor => ({
  key: "status",
  label: "Status",
  value,
  kind: "single",
  options: STATUS_CHOICES,
  defaultValue: "new",
  clearedValue: "all",
  showAtDefault: true,
});
const rating = (value: string): FilterDescriptor => ({
  key: "rating",
  label: "Rating",
  value,
  kind: "single",
  options: [{ value: "", label: "Any" }, ...["1", "2", "3"].map((v) => ({ value: v, label: v }))],
});
const search = (value: string): FilterDescriptor => ({ key: "q", label: "Search", value, kind: "text" });

/** Stateful host: onChange writes back, like the URL round trip. */
function Host({ initial, onChange = vi.fn(), onClearAll }: { initial: Record<string, string>; onChange?: (k: string, v: string) => void; onClearAll?: () => void }) {
  const [state, setState] = useState(initial);
  const filters = [status(state.status ?? "new"), rating(state.rating ?? ""), search(state.q ?? "")];
  return (
    <>
      <button type="button">Outside</button>
      <FilterChipBar
        filters={filters}
        onChange={(k, v) => {
          onChange(k, v);
          setState((s) => ({ ...s, [k]: v }));
        }}
        onClearAll={
          onClearAll ??
          (() => {
            setState({ status: "all" });
          })
        }
      />
    </>
  );
}

const statusChip = (label = "New") => screen.getByRole("button", { name: `Status: ${label}, change filter` });
const closed = () => waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());

describe("FilterChipBar (OR-13)", () => {
  it("BUG fix: clicking the Status chip opens its options with the current one selected; choosing updates the value", async () => {
    const onChange = vi.fn();
    render(<Host initial={{ status: "new" }} onChange={onChange} />);
    const chip = statusChip();
    expect(chip).toHaveAttribute("aria-haspopup", "listbox");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(chip);
    const list = screen.getByRole("listbox", { name: "Status" });
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["New", "Reviewed", "Promoted", "Dismissed", "All"]);
    expect(within(list).getByRole("option", { name: "New" })).toHaveAttribute("aria-selected", "true");
    // Focus starts on the current value.
    expect(within(list).getByRole("option", { name: "New" })).toHaveFocus();
    fireEvent.click(within(list).getByRole("option", { name: "Reviewed" }));
    expect(onChange).toHaveBeenCalledWith("status", "reviewed");
    await closed();
    expect(statusChip("Reviewed")).toHaveFocus();
  });

  it("status options come from TRIAGE_STATUSES plus All (no hard-coded list)", () => {
    expect(STATUS_CHOICES.map((c) => c.value)).toEqual([...TRIAGE_STATUSES, "all"]);
  });

  it("× removes a filter (Status resets to All) and is a separate button", () => {
    const onChange = vi.fn();
    render(<Host initial={{ status: "reviewed", rating: "2" }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove filter Rating: 2" }));
    expect(onChange).toHaveBeenCalledWith("rating", "");
    fireEvent.click(screen.getByRole("button", { name: "Remove filter Status: Reviewed" }));
    expect(onChange).toHaveBeenCalledWith("status", "all");
  });

  it("the count excludes defaults (status New) and cleared values (All); Clear all only shows when something is active", () => {
    expect(activeCount([status("new"), rating("")])).toBe(0);
    expect(activeCount([status("all"), rating("")])).toBe(0);
    expect(activeCount([status("reviewed"), rating("1"), search("x")])).toBe(3);

    const { unmount } = render(<Host initial={{ status: "new" }} />);
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear all" })).not.toBeInTheDocument();
    unmount();

    render(<Host initial={{ status: "reviewed", rating: "1" }} />);
    expect(screen.getByRole("button", { name: "Filters, 2 active" })).toBeInTheDocument();
    expect(screen.getByTestId("filters-count")).toHaveTextContent("2");
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.queryByRole("button", { name: /Status:/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filters" })).toBeInTheDocument();
  });

  it("text filters (search) show as a label with × only", () => {
    render(<Host initial={{ q: "refund" }} />);
    expect(screen.getByText("Search: refund")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Search: refund, change filter/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove filter Search: refund" })).toBeInTheDocument();
  });

  it("keyboard: ↓ opens, ↑ ↓ Home End move, Enter selects and closes, Esc closes and returns focus, Tab closes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Host initial={{ status: "new" }} onChange={onChange} />);
    statusChip().focus();
    await user.keyboard("{ArrowDown}");
    const list = screen.getByRole("listbox", { name: "Status" });
    const options = within(list).getAllByRole("option");
    expect(options[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(options[1]).toHaveFocus();
    await user.keyboard("{End}");
    expect(options[4]).toHaveFocus();
    await user.keyboard("{Home}");
    expect(options[0]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(options[4]).toHaveFocus();
    await user.keyboard("{Escape}");
    await closed();
    expect(statusChip()).toHaveFocus();
    expect(onChange).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith("status", "promoted");
    await closed();

    await user.keyboard("{Enter}");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.tab();
    await closed();
  });

  it("closes on an outside click", async () => {
    render(<Host initial={{ status: "new" }} />);
    fireEvent.click(statusChip());
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("button", { name: "Outside" }));
    await closed();
  });

  it("the Filters popover edits every field with options and shows the note", () => {
    const onChange = vi.fn();
    render(
      <FilterChipBar
        filters={[status("new"), rating(""), search("")]}
        onChange={onChange}
        note="Reason and sentiment narrow the loaded page."
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    expect(within(dialog).getByRole("group", { name: "Status" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("group", { name: "Search" })).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("Reason and sentiment narrow the loaded page.");
    const ratingGroup = within(dialog).getByRole("group", { name: "Rating" });
    expect(within(ratingGroup).getByRole("button", { name: "Any" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(ratingGroup).getByRole("button", { name: "2" }));
    expect(onChange).toHaveBeenCalledWith("rating", "2");
  });

  it("Feedback descriptors keep today's chip text and round-trip through the URL parser", () => {
    const filters = parseFilters(new URLSearchParams("status=reviewed&rating=1&grounded=false&sentiment=negative&q=late"));
    const chips = feedbackFilterDescriptors(filters);
    render(<FilterChipBar filters={chips} onChange={vi.fn()} />);
    for (const name of ["Status: Reviewed", "Rating: 1", "Ungrounded", "Negative (1-2)"]) {
      expect(screen.getByRole("button", { name: `${name}, change filter` })).toBeInTheDocument();
    }
    expect(screen.getByText("Search: late")).toBeInTheDocument();
  });
});
