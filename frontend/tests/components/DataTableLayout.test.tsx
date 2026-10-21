import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";

interface Row {
  id: string;
  question: string;
}

const COLUMNS: ColumnDef<Row>[] = [
  { key: "question", header: "Question" },
  { key: "details", header: "Details", srOnlyHeader: true, render: (r) => <button type="button">{`Open ${r.id}`}</button> },
];
const DATA: Row[] = [{ id: "c1", question: "What is the Essential Eight?" }];

describe("DataTable column headers and phone layout", () => {
  it("srOnlyHeader keeps the column's accessible name with no visible text", () => {
    render(<DataTable columns={COLUMNS} data={DATA} />);
    const header = screen.getByRole("columnheader", { name: "Details" });
    expect(within(header).getByText("Details")).toHaveClass("sr-only");
    expect(within(screen.getByRole("columnheader", { name: "Question" })).getByText("Question")).not.toHaveClass("sr-only");
  });

  it("VA-34: below sm rows stack as cards, each cell labelled with its column (none for a hidden header)", () => {
    const { container } = render(<DataTable columns={COLUMNS} data={DATA} />);
    expect(container.querySelector("table")).toHaveClass("max-sm:block");
    expect(container.querySelector("thead")).toHaveClass("max-sm:hidden");
    const [question, details] = screen.getAllByRole("cell");
    expect(question).toHaveAttribute("data-label", "Question");
    expect(question.className).toContain("max-sm:before:content-[attr(data-label)]");
    expect(details).not.toHaveAttribute("data-label");
  });

  it("stackOnPhone={false} keeps the plain table", () => {
    const { container } = render(<DataTable columns={COLUMNS} data={DATA} stackOnPhone={false} />);
    expect(container.querySelector("table")).not.toHaveClass("max-sm:block");
  });
});

describe("DataTable pinned (sticky) column", () => {
  const PINNED: ColumnDef<Row>[] = [
    { key: "question", header: "Question" },
    { key: "actions", header: "Actions", sticky: "right", render: (r) => <button type="button">{`Delete ${r.id}`}</button> },
  ];

  it("uses the one shared sticky style: the row's themed colour, never its own white background", () => {
    render(<DataTable columns={PINNED} data={DATA} />);
    const head = screen.getByRole("columnheader", { name: "Actions" });
    const cell = screen.getByRole("button", { name: "Delete c1" }).closest("td")!;
    expect(head).toHaveClass("dt-sticky-head");
    expect(cell).toHaveClass("dt-sticky-cell");
    expect(`${head.className} ${cell.className}`).not.toMatch(/bg-surface-card|bg-white|bg-surface-studio/);
    // Same row tint as every other cell: the row carries it, the table sits on the themed surface.
    expect(cell.closest("tr")).toHaveClass("dt-row");
    expect(cell.closest(".dt-surface")).not.toBeNull();
  });

  it("shows the divider only while content scrolls under the pinned column", () => {
    const widths = { scrollWidth: 900, clientWidth: 600 };
    const spies = (Object.keys(widths) as (keyof typeof widths)[]).map((key) =>
      vi.spyOn(HTMLElement.prototype, key, "get").mockImplementation(function (this: HTMLElement) {
        return this.classList.contains("dt-surface") ? widths[key] : 0;
      }),
    );
    const { container } = render(<DataTable columns={PINNED} data={DATA} />);
    const scroller = container.querySelector(".dt-surface") as HTMLElement;
    expect(scroller).toHaveAttribute("data-scroll-under", "true");
    // Scrolled to the end: nothing is under the pin any more.
    scroller.scrollLeft = 300;
    fireEvent.scroll(scroller);
    expect(scroller).not.toHaveAttribute("data-scroll-under");
    spies.forEach((s) => s.mockRestore());
  });
});
