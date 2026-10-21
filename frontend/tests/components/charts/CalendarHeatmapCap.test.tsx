/** VA-4: a sparse heatmap keeps visible cells (capped size, gaps) and a value legend. */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { CalendarHeatmap } from "@/components/charts/CalendarHeatmap";

const DATA = [
  { row: "#1", col: "6 Oct", value: 4 },
  { row: "#1", col: "13 Oct", value: 1 },
  { row: "#2", col: "13 Oct", value: 2 },
];

describe("CalendarHeatmap cell cap and legend", () => {
  it("caps cell size, draws gaps and a 0..max legend when asked", () => {
    const { container } = render(
      <CalendarHeatmap data={DATA} maxCellWidth={40} maxCellHeight={20} cellGap={3} legend valueLabel="Hits" label="Chunk hits" />,
    );
    const rects = Array.from(container.querySelectorAll("rect"));
    expect(rects.length).toBeGreaterThan(0);
    for (const rect of rects) {
      expect(Number(rect.getAttribute("width"))).toBeLessThanOrEqual(37);
      expect(Number(rect.getAttribute("height"))).toBeLessThanOrEqual(17);
    }
    expect(screen.getByRole("img", { name: "Hits scale: 0 to 4" })).toBeInTheDocument();
  });

  it("without the new props it renders as before (no legend)", () => {
    render(<CalendarHeatmap data={DATA} label="Chunk hits" />);
    expect(screen.queryByRole("img", { name: /scale/ })).not.toBeInTheDocument();
  });

  // jsdom measures nothing, so the chart lays out at the 400 px fallback width.
  const marks = (container: HTMLElement) =>
    Array.from(container.querySelectorAll<SVGRectElement>("rect.chart-mark")).map((r) => ({
      x: Number(r.getAttribute("x")),
      w: Number(r.getAttribute("width")),
    }));

  it("stretches cells to the width, then centres a capped grid with its titles and legend", () => {
    const wide = render(
      <CalendarHeatmap data={DATA} maxCellWidth={1000} maxCellHeight={20} cellGap={2} legend xAxisLabel="Week starting" yAxisLabel="Chunk" />,
    );
    // Uncapped by width: 2 columns share the 400 px minus the 52 px label gutter.
    expect(marks(wide.container)[0].w).toBeCloseTo((400 - 52) / 2 - 2, 0);
    wide.unmount();

    const { container } = render(
      <CalendarHeatmap data={DATA} maxCellWidth={100} maxCellHeight={20} cellGap={2} legend xAxisLabel="Week starting" yAxisLabel="Chunk" />,
    );
    const svg = container.querySelector("svg")!;
    // Height fits the content: 2 rows x 20 + tick labels (18) + x title (16); no dead space.
    expect(svg.getAttribute("height")).toBe(String(2 * 20 + 18 + 16));
    // A 2 x 100 px grid in 400 - 52 px is centred: (348 - 200) / 2 = 74 px shift.
    expect(container.querySelector("svg > g")).toHaveAttribute("transform", "translate(74 0)");
    // The x title is centred on the grid (52 + 200 / 2), the y title on its rows.
    const x = container.querySelector('[data-axis="x"]')!;
    expect(Number(x.getAttribute("x"))).toBe(152);
    expect(Number(x.getAttribute("y"))).toBe(2 * 20 + 18 + 16 - 3);
    expect(Number(container.querySelector('[data-axis="y"]')!.getAttribute("y"))).toBe(20);
    // The legend sits right under the grid, aligned with it.
    const legend = screen.getByTestId("heatmap-legend");
    expect(legend.style.marginLeft).toBe("126px");
    expect(legend.style.width).toBe("200px");
  });

  it("scrolls inside its card when the cells would get narrower than minCellWidth (phones)", () => {
    const cols = Array.from({ length: 24 }, (_, i) => String(i));
    const data = cols.map((col) => ({ row: "Mon", col, value: 1 }));
    const { container } = render(<CalendarHeatmap data={data} cols={cols} minCellWidth={28} />);
    const root = screen.getByTestId("calendar-heatmap");
    expect(root).toHaveClass("overflow-x-auto");
    expect((root.firstElementChild as HTMLElement).style.width).toBe(`${36 + 24 * 28}px`);
    expect(container.querySelectorAll("rect.chart-mark")).toHaveLength(24);
  });

  it("keeps click-to-select on a cell", () => {
    const onSelect = vi.fn();
    const { container } = render(<CalendarHeatmap data={DATA} onSelect={onSelect} maxCellWidth={60} maxCellHeight={20} />);
    fireEvent.click(container.querySelector("rect.chart-mark")!);
    expect(onSelect).toHaveBeenCalledWith(DATA[0]);
  });
});
