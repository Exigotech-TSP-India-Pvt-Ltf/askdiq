/** VA-4: a sparse heatmap keeps visible cells (capped size, gaps) and a value legend. */
import React from "react";
import { describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
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
});
