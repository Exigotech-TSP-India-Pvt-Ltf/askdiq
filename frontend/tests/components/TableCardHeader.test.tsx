import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { TableCard } from "@/components/admin/TableCard";
import { StatCard } from "@/components/admin/insights/StatCard";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";

describe("TableCard header", () => {
  it("puts the title and the toolbar on one header row", () => {
    render(
      <TableCard title="Sessions" description="From this visitor" toolbar={<input aria-label="Search sessions" />}>
        <table />
      </TableCard>,
    );
    const headers = screen.getAllByTestId("table-card-header");
    expect(headers).toHaveLength(1);
    expect(within(headers[0]).getByRole("heading", { name: "Sessions" })).toBeInTheDocument();
    expect(within(headers[0]).getByRole("textbox", { name: "Search sessions" })).toBeInTheDocument();
  });

  it("renders no header row without title or toolbar", () => {
    render(
      <TableCard>
        <table />
      </TableCard>,
    );
    expect(screen.queryByTestId("table-card-header")).toBeNull();
  });
});

describe("KPI cards share one layout", () => {
  it("StatCard and KpiTrendCard keep the value on one line", () => {
    render(
      <>
        <StatCard title="Sessions" value={315} subtitle="All time" spark={[{ label: "1 Oct", value: 3 }]} />
        <KpiTrendCard label="Cost" value="$0.0302" trend={[]} hint="LLM cost (USD) per day" />
      </>,
    );
    const values = screen.getAllByTestId("kpi-value");
    expect(values).toHaveLength(2);
    for (const v of values) expect(v).toHaveClass("whitespace-nowrap", "truncate");
    expect(screen.getByText("315")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Sessions trend" })).toBeInTheDocument();
    expect(screen.getByText("LLM cost (USD) per day")).toHaveAttribute("title", "LLM cost (USD) per day");
  });
});
