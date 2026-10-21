import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
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
