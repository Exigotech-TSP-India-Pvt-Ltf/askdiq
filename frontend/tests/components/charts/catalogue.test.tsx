import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { chartTheme } from "@/lib/theme-tokens";
import {
  BulletChart,
  CalendarHeatmap,
  Funnel,
  Histogram,
  KpiTrendCard,
  PercentileBand,
  QuadrantScatter,
  Radar,
  RadialGauge,
  Sparkline,
  StackedArea,
  Waterfall,
  binValues,
} from "@/components/charts";

/**
 * Every catalogue chart renders with fixture data, an
 * empty array and a single point, and `onSelect(datum)` fires on click and on
 * Enter. Colours come from `chartTheme` and follow the theme class.
 */

type Datum = unknown;

interface ChartCase {
  name: string;
  fixture: readonly Datum[];
  render: (data: readonly Datum[], onSelect: (d: Datum) => void) => React.ReactElement;
  /** Datum passed to onSelect for mark `i` (default: `fixture[i]`). */
  datumAt?: (data: readonly Datum[], i: number) => Datum;
}

const spark = [
  { label: "Mon", value: 4 },
  { label: "Tue", value: 9 },
  { label: "Wed", value: 6 },
];
const outcomes = [
  { day: "Mon", answered: 40, fallback: 3, refused: 1 },
  { day: "Tue", answered: 52, fallback: 5, refused: 0 },
  { day: "Wed", answered: 47, fallback: 2, refused: 2 },
];
const outcomeSeries = [
  { key: "answered", label: "Answered" },
  { key: "fallback", label: "Fallback" },
  { key: "refused", label: "Refused" },
] as const;
const radarRows = [
  { dimension: "Groundedness", current: 82, previous: 76 },
  { dimension: "Recall", current: 91, previous: 88 },
  { dimension: "Citations", current: 70, previous: 74 },
];
const radarSeries = [
  { key: "current", label: "Current" },
  { key: "previous", label: "Previous" },
] as const;

const CASES: ChartCase[] = [
  {
    name: "Sparkline",
    fixture: spark,
    render: (data, onSelect) => (
      <Sparkline data={data as typeof spark} onSelect={onSelect} label="Queries" />
    ),
  },
  {
    name: "KpiTrendCard",
    fixture: spark,
    render: (data, onSelect) => (
      <KpiTrendCard label="Queries" value="1,204" delta={0.12} trend={data as typeof spark} onSelect={onSelect} />
    ),
  },
  {
    name: "StackedArea",
    fixture: outcomes,
    render: (data, onSelect) => (
      <StackedArea
        data={data as typeof outcomes}
        xKey="day"
        series={outcomeSeries}
        onSelect={onSelect}
        label="Queries by outcome"
      />
    ),
  },
  {
    name: "PercentileBand",
    fixture: [
      { label: "Mon", p50: 0.8, p95: 2.1, p99: 3.4 },
      { label: "Tue", p50: 0.9, p95: 2.5 },
      { label: "Wed", p50: 0.7, p95: 1.9, p99: 2.8 },
    ],
    render: (data, onSelect) => (
      <PercentileBand data={data as { label: string; p50: number; p95: number }[]} onSelect={onSelect} />
    ),
  },
  {
    name: "Waterfall",
    fixture: [
      { label: "Embed", value: 120 },
      { label: "Retrieve", value: 340 },
      { label: "LLM", value: 1800, tone: "warn" },
    ],
    render: (data, onSelect) => <Waterfall data={data as { label: string; value: number }[]} onSelect={onSelect} />,
  },
  {
    name: "CalendarHeatmap",
    fixture: [
      { row: "Mon", col: "09", value: 4 },
      { row: "Mon", col: "10", value: 12 },
      { row: "Tue", col: "09", value: 0 },
    ],
    render: (data, onSelect) => (
      <CalendarHeatmap data={data as { row: string; col: string; value: number }[]} onSelect={onSelect} />
    ),
  },
  {
    name: "RadialGauge",
    fixture: [{ label: "Satisfaction", value: 82, max: 100 }],
    render: (data, onSelect) => {
      const d = data[0] as { label: string; value: number } | undefined;
      return (
        <RadialGauge
          label="Satisfaction"
          value={d ? d.value : null}
          bands={{ warn: 70, danger: 50 }}
          onSelect={onSelect}
        />
      );
    },
  },
  {
    name: "BulletChart",
    fixture: [
      { label: "Cost MTD", value: 420, target: 600, forecast: 710, ranges: [300, 500, 800] },
      { label: "Tokens", value: 80, target: 100 },
    ],
    render: (data, onSelect) => (
      <BulletChart data={data as { label: string; value: number }[]} onSelect={onSelect} />
    ),
  },
  {
    name: "Funnel",
    fixture: [
      { label: "New", value: 120 },
      { label: "Reviewed", value: 64 },
      { label: "Promoted", value: 12 },
    ],
    render: (data, onSelect) => <Funnel data={data as { label: string; value: number }[]} onSelect={onSelect} />,
  },
  {
    name: "QuadrantScatter",
    fixture: [
      { id: "a", label: "Q-1", x: 0.9, y: 0.8 },
      { id: "b", label: "Q-2", x: 0.2, y: 0.9, size: 4 },
      { id: "c", label: "Q-3", x: 0.1, y: 0.2 },
    ],
    render: (data, onSelect) => (
      <QuadrantScatter
        data={data as { id: string; label: string; x: number; y: number }[]}
        onSelect={onSelect}
        xThreshold={0.5}
        yThreshold={0.5}
        xMax={1}
        yMax={1}
        quadrants={{ topRight: "Agree", bottomLeft: "Agree (bad)" }}
      />
    ),
  },
  {
    name: "Histogram",
    fixture: binValues([1, 2, 2, 3, 3, 3, 8, 9], 4),
    render: (data, onSelect) => (
      <Histogram data={data as ReturnType<typeof binValues>} onSelect={onSelect} />
    ),
  },
  {
    name: "Radar",
    fixture: radarRows,
    render: (data, onSelect) => (
      <Radar data={data as typeof radarRows} axisKey="dimension" series={radarSeries} onSelect={onSelect} />
    ),
  },
];

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
});

const marks = () => screen.queryAllByRole("button");

describe.each(CASES)("$name", ({ fixture, render: renderChart, datumAt }) => {
  const expected = (i: number) => (datumAt ? datumAt(fixture, i) : fixture[i]);

  it("renders the fixture with one focusable mark per datum and a single tab stop", () => {
    render(renderChart(fixture, vi.fn()));
    const all = marks();
    expect(all).toHaveLength(fixture.length);
    expect(all.filter((m) => m.getAttribute("tabindex") === "0")).toHaveLength(1);
    for (const m of all) expect(m).toHaveAttribute("aria-label");
  });

  it("renders an empty state for an empty array", () => {
    render(renderChart([], vi.fn()));
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(marks()).toHaveLength(0);
  });

  it("renders a single point", () => {
    const onSelect = vi.fn();
    render(renderChart(fixture.slice(0, 1), onSelect));
    expect(marks()).toHaveLength(1);
    fireEvent.click(marks()[0]);
    expect(onSelect).toHaveBeenCalledWith(expected(0));
  });

  it("calls onSelect(datum) on click", () => {
    const onSelect = vi.fn();
    render(renderChart(fixture, onSelect));
    const last = fixture.length - 1;
    fireEvent.click(marks()[last]);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(expected(last));
  });

  it("calls onSelect(datum) on Enter and Space, and arrows move focus", () => {
    const onSelect = vi.fn();
    render(renderChart(fixture, onSelect));
    const [first] = marks();
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith(expected(0));
    fireEvent.keyDown(first, { key: " " });
    expect(onSelect).toHaveBeenCalledTimes(2);
    if (fixture.length > 1) {
      fireEvent.keyDown(first, { key: "ArrowRight" });
      expect(marks()[1]).toHaveFocus();
      fireEvent.keyDown(marks()[1], { key: "Enter" });
      expect(onSelect).toHaveBeenLastCalledWith(expected(1));
    }
  });

  it("is not focusable without onSelect", () => {
    render(renderChart(fixture, undefined as unknown as (d: Datum) => void));
    expect(marks()).toHaveLength(0);
  });
});

describe("chart colours come from the theme tokens", () => {
  const light = chartTheme.light;
  const dark = chartTheme.dark;

  it("Histogram bars switch from the light to the dark categorical colour on toggle", async () => {
    const { container } = render(<Histogram data={binValues([1, 2, 3, 4], 2)} />);
    expect(container.querySelector(`rect[fill="${light.categorical[0]}"]`)).not.toBeNull();
    act(() => document.documentElement.classList.add("dark"));
    await waitFor(() => expect(container.querySelector(`rect[fill="${dark.categorical[0]}"]`)).not.toBeNull());
    expect(container.querySelector(`rect[fill="${light.categorical[0]}"]`)).toBeNull();
  });

  it("Sparkline stroke and gauge status colour follow the theme", async () => {
    const { container } = render(
      <>
        <Sparkline data={spark} />
        <RadialGauge label="CPU" value={95} higherIsBetter={false} bands={{ warn: 70, danger: 90 }} />
      </>,
    );
    expect(screen.getByTestId("spark-line")).toHaveAttribute("stroke", light.categorical[0]);
    expect(screen.getByTestId("gauge-value")).toHaveAttribute("stroke", light.status.danger);
    act(() => document.documentElement.classList.add("dark"));
    await waitFor(() => expect(screen.getByTestId("spark-line")).toHaveAttribute("stroke", dark.categorical[0]));
    expect(screen.getByTestId("gauge-value")).toHaveAttribute("stroke", dark.status.danger);
    expect(container.innerHTML).not.toContain(light.categorical[0]);
  });

  it("gridlines and axis labels use the grid and axis tokens", () => {
    const { container } = render(<StackedArea data={outcomes} xKey="day" series={outcomeSeries} />);
    expect(container.querySelector(`line[stroke="${light.grid}"]`)).not.toBeNull();
    expect(container.querySelector(`text[fill="${light.axis}"]`)).not.toBeNull();
  });
});

describe("hover shows a themed tooltip and dims other marks", () => {
  it("Funnel tooltip lists the stage and dims the others to chartTheme.dimOpacity", () => {
    render(
      <Funnel
        data={[
          { label: "New", value: 100 },
          { label: "Reviewed", value: 50 },
        ]}
        onSelect={vi.fn()}
      />,
    );
    fireEvent.mouseEnter(marks()[1]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Reviewed");
    expect(screen.getByRole("tooltip")).toHaveTextContent("50%");
    expect(marks()[0]).toHaveAttribute("opacity", String(chartTheme.light.dimOpacity));
    expect(marks()[1]).toHaveAttribute("opacity", "1");
    fireEvent.mouseLeave(marks()[1]);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("KpiTrendCard links when given an href and labels the delta direction", () => {
    render(<KpiTrendCard label="Errors" value="3" delta={0.5} goodDirection="down" trend={spark} href="/admin/governance" />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/admin/governance");
    expect(screen.getByText("(worsening)")).toBeInTheDocument();
  });
});

describe("binValues", () => {
  it("splits values into equal-width bins and keeps the max in the last bin", () => {
    const bins = binValues([0, 1, 2, 3, 4], 2);
    expect(bins.map((b) => b.count)).toEqual([2, 3]);
    expect(binValues([], 3)).toEqual([]);
    expect(binValues([5, 5], 3)).toEqual([{ x0: 5, x1: 5, count: 2 }]);
  });
});
