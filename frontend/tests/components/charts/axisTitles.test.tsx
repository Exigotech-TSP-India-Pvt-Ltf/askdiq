import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  BulletChart,
  CalendarHeatmap,
  Histogram,
  PercentileBand,
  QuadrantScatter,
  StackedArea,
  Waterfall,
} from "@/components/charts";
import { BarList } from "@/components/charts/BarList";
import { LineTrend } from "@/components/charts/LineTrend";
import { StackedBar } from "@/components/charts/StackedBar";
import { fitText } from "@/components/charts/primitives";
import { plotArea, MARGIN, AXIS_TITLE_SPACE } from "@/components/charts/cartesian";

/** Axis titles (x / y with units), tooltip value labels, and the narrow-width ellipsis. */

afterEach(cleanup);

const rows = [
  { day: "Mon", a: 4, b: 1 },
  { day: "Tue", a: 9, b: 3 },
];
const series = [
  { key: "a", label: "Answered" },
  { key: "b", label: "Fallback" },
] as const;

function axisTitle(container: HTMLElement, axis: "x" | "y"): Element | null {
  return container.querySelector(`[data-testid="chart-axis-titles"] [data-axis="${axis}"]`);
}

const titled: [string, (x: string, y: string) => React.ReactElement][] = [
  ["LineTrend", (x, y) => <LineTrend data={rows} xKey="day" series={series} xAxisLabel={x} yAxisLabel={y} />],
  ["StackedArea", (x, y) => <StackedArea data={rows} xKey="day" series={series} xAxisLabel={x} yAxisLabel={y} />],
  ["StackedBar", (x, y) => <StackedBar data={rows} xKey="day" series={series} xAxisLabel={x} yAxisLabel={y} />],
  [
    "StackedBar horizontal",
    (x, y) => (
      <StackedBar data={rows} xKey="day" series={series} orientation="horizontal" xAxisLabel={x} yAxisLabel={y} />
    ),
  ],
  [
    "PercentileBand",
    (x, y) => <PercentileBand data={[{ label: "Mon", p50: 1, p95: 2 }]} xAxisLabel={x} yAxisLabel={y} />,
  ],
  ["Histogram", (x, y) => <Histogram data={[{ x0: 0, x1: 5, count: 3 }]} xAxisLabel={x} yAxisLabel={y} />],
  ["Waterfall", (x, y) => <Waterfall data={[{ label: "LLM", value: 120 }]} xAxisLabel={x} yAxisLabel={y} />],
  [
    "QuadrantScatter",
    (x, y) => <QuadrantScatter data={[{ id: "1", label: "Doc", x: 2, y: 3 }]} xAxisLabel={x} yAxisLabel={y} />,
  ],
  [
    "CalendarHeatmap",
    (x, y) => <CalendarHeatmap data={[{ row: "Mon", col: "09", value: 2 }]} xAxisLabel={x} yAxisLabel={y} />,
  ],
  ["BarList", (x, y) => <BarList data={[{ key: "a", label: "A", value: 2 }]} xAxisLabel={x} yAxisLabel={y} />],
  ["BulletChart", (x, y) => <BulletChart data={[{ label: "Cost", value: 2, target: 3 }]} xAxisLabel={x} yAxisLabel={y} />],
];

describe("chart axis titles", () => {
  it.each(titled)("%s renders x and y axis titles and describes them to assistive tech", (_name, make) => {
    const { container } = render(make("Date", "Queries per day"));
    expect(axisTitle(container, "x")).toHaveTextContent("Date");
    expect(axisTitle(container, "y")).toHaveTextContent("Queries per day");
    const svg = container.querySelector("svg[aria-describedby]");
    expect(svg).not.toBeNull();
    const desc = container.querySelector(`#${CSS.escape(svg!.getAttribute("aria-describedby")!)}`);
    expect(desc).toHaveTextContent("X axis: Date. Y axis: Queries per day.");
  });

  it("renders no titles and keeps the default margins when none are set", () => {
    const { container } = render(<LineTrend data={rows} xKey="day" series={series} />);
    expect(container.querySelector('[data-testid="chart-axis-titles"]')).toBeNull();
    expect(container.querySelector("svg[aria-describedby]")).toBeNull();
    expect(plotArea(400, 240)).toEqual({ x0: MARGIN.left, x1: 390, y0: MARGIN.top, y1: 240 - MARGIN.bottom });
  });

  it("grows only the margin of the axis that has a title", () => {
    const base = plotArea(400, 240);
    const x = plotArea(400, 240, { xAxisLabel: "Date" });
    const y = plotArea(400, 240, { yAxisLabel: "USD" });
    expect(x.y1).toBe(base.y1 - AXIS_TITLE_SPACE);
    expect(x.x0).toBe(base.x0);
    expect(y.x0).toBe(base.x0 + AXIS_TITLE_SPACE);
    expect(y.y1).toBe(base.y1);
  });

  it("ellipsizes long titles to the available width, keeping the full text in a <title>", () => {
    const long = "Average end-to-end answer latency in seconds per hour of day";
    expect(fitText(long, 60)).toMatch(/…$/);
    expect(fitText(long, 60).length).toBeLessThan(long.length);
    expect(fitText("USD", 60)).toBe("USD");
    const { container } = render(<LineTrend data={rows} xKey="day" series={series} yAxisLabel={long} height={80} />);
    const y = axisTitle(container, "y")!;
    expect(y.querySelector("title")).toHaveTextContent(long);
    expect(y.textContent).toContain("…");
  });

  it("names the tooltip value with valueLabel and shows the unit caption", () => {
    render(
      <Histogram
        data={[{ x0: 0, x1: 5, count: 3 }]}
        valueLabel="Conversations"
        xAxisLabel="Turns per conversation"
        onSelect={() => undefined}
      />,
    );
    fireEvent.mouseEnter(screen.getByRole("button", { name: /0–5: 3 Conversations/ }));
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Conversations");
    expect(tip).toHaveTextContent("Turns per conversation");
    expect(tip).not.toHaveTextContent("Count");
  });

  it("Waterfall ellipsizes long stage labels to labelWidth with the full label on hover", () => {
    const label = "A very long pipeline stage name that does not fit";
    const { container } = render(
      <Waterfall data={[{ label, value: 10 }]} labelWidth={60} valueLabel="Duration" onSelect={() => undefined} />,
    );
    const text = Array.from(container.querySelectorAll("text")).find((t) => t.querySelector("title"));
    expect(text?.querySelector("title")).toHaveTextContent(label);
    expect(text?.textContent).toContain("…");
    fireEvent.mouseEnter(screen.getByRole("button"));
    expect(screen.getByRole("tooltip")).toHaveTextContent(label);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Duration");
  });
});
