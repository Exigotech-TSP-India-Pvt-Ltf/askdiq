import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChartCard, SankeyChart, TreemapChart, toChartTable } from "@/components/charts";

/** One ChartCard with states and an accessible table toggle. */

const rows = [
  { stage: "Embedding", seconds: 0.12 },
  { stage: "LLM", seconds: 1.8 },
];
const table = toChartTable(rows, [
  { key: "stage", label: "Stage" },
  { key: "seconds", label: "Seconds", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
]);

describe("ChartCard", () => {
  it("renders title, subtitle with window, actions and the chart", () => {
    render(
      <ChartCard title="Pipeline Latency" subtitle="Per stage" window="Last 24h" actions={<span>badge</span>}>
        <div data-testid="chart" />
      </ChartCard>,
    );
    expect(screen.getByRole("region", { name: "Pipeline Latency" })).toBeInTheDocument();
    expect(screen.getByText("Per stage · Last 24h")).toBeInTheDocument();
    expect(screen.getByText("badge")).toBeInTheDocument();
    expect(screen.getByTestId("chart")).toBeInTheDocument();
  });

  it("keeps the title on one line and puts meta chips under the subtitle, not beside the title", () => {
    const title = "Month-end forecast";
    render(
      <ChartCard title={title} subtitle="Cumulative cost" meta={<span>$4.14 forecast</span>} table={table}>
        <div data-testid="chart" />
      </ChartCard>,
    );
    const heading = screen.getByRole("heading", { name: title });
    expect(heading).toHaveClass("truncate");
    expect(heading).toHaveAttribute("title", title);
    const meta = screen.getByTestId("chart-card-meta");
    expect(meta).toHaveTextContent("$4.14 forecast");
    // Subtitle, then meta, inside the title column; the toggle stays outside it (top right).
    const subtitle = screen.getByText("Cumulative cost");
    expect(subtitle.parentElement).toBe(meta.parentElement);
    expect(subtitle.compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.parentElement?.contains(meta)).toBe(false);
    expect(meta.parentElement?.contains(screen.getByRole("button", { name: /view as table/i }))).toBe(false);
  });

  it("renders no meta row without meta", () => {
    render(
      <ChartCard title="Plain">
        <div />
      </ChartCard>,
    );
    expect(screen.queryByTestId("chart-card-meta")).not.toBeInTheDocument();
  });

  it("toggles an accessible table fallback with aria-pressed and back", () => {
    render(
      <ChartCard title="Latency" table={table}>
        <div data-testid="chart" />
      </ChartCard>,
    );
    const toggle = screen.getByRole("button", { name: "View as table" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAttribute("aria-controls");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("chart")).toBeNull();
    const grid = screen.getByRole("table", { name: "Latency" });
    expect(grid).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Stage", "Seconds"]);
    expect(screen.getByText("1.80 s")).toBeInTheDocument();
    const back = screen.getByRole("button", { name: "View as chart" });
    expect(back).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(back);
    expect(screen.getByTestId("chart")).toBeInTheDocument();
  });

  it("is keyboard operable (the toggle is a native button)", () => {
    render(
      <ChartCard title="Latency" table={table}>
        <div />
      </ChartCard>,
    );
    const toggle = screen.getByRole("button", { name: "View as table" });
    toggle.focus();
    expect(toggle).toHaveFocus();
    expect(toggle.className).toContain("focus-visible:ring-2");
  });

  it("hides the table toggle without table data", () => {
    render(
      <ChartCard title="Latency">
        <div />
      </ChartCard>,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows a busy skeleton while loading", () => {
    render(
      <ChartCard title="Latency" loading table={table}>
        <div data-testid="chart" />
      </ChartCard>,
    );
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading chart")).toBeInTheDocument();
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(screen.queryByRole("button", { name: "View as table" })).toBeNull();
  });

  it("shows the error with a retry action", () => {
    const onRetry = vi.fn();
    render(
      <ChartCard title="Latency" error="Could not load latency" onRetry={onRetry}>
        <div data-testid="chart" />
      </ChartCard>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load latency");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows the empty message", () => {
    render(
      <ChartCard title="Latency" empty emptyMessage="No scored cases yet">
        <div data-testid="chart" />
      </ChartCard>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No scored cases yet");
    expect(screen.queryByTestId("chart")).toBeNull();
  });

  it("keeps the page variants: aurora glow and eyebrow title", () => {
    const { container } = render(
      <ChartCard title="Quality" variant="aurora" titleStyle="eyebrow">
        <div />
      </ChartCard>,
    );
    const section = container.querySelector("section");
    expect(section?.className).toContain("rounded-2xl");
    expect(container.querySelector('[aria-hidden="true"].blur-2xl')).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Quality" }).className).toContain("uppercase");
  });
});

describe("heavy charts are code-split", () => {
  it("TreemapChart and SankeyChart are next/dynamic components that render a skeleton first", () => {
    render(
      <>
        <TreemapChart data={[{ name: "billing", value: 4 }]} />
        <SankeyChart nodes={[{ name: "a" }, { name: "b" }]} links={[{ source: 0, target: 1, value: 1 }]} />
      </>,
    );
    expect(screen.getAllByText("Loading chart")).toHaveLength(2);
  });
});
