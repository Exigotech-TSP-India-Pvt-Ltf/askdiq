/**
 * Regression: clicking a mark filters the chart down to fewer marks while the
 * pointer is still over it. The hovered index used to survive the re-render
 * and the tooltip read `layout[active]` of the shorter array, throwing a
 * TypeError that took the whole Analytics page into the error boundary
 * (e.g. clicking a bubble in "Conversations per person").
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartCard } from "@/components/charts";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PeopleBubble } from "@/components/admin/analytics/PeopleBubble";
import { FlowSankey } from "@/components/admin/analytics/FlowSankey";
import { DropoffChart } from "@/components/admin/analytics/DropoffChart";
import { RECORDED } from "../../journeys/analytics-harness";

afterEach(cleanup);

describe("hovered mark removed by a filter", () => {
  it("people bubble survives losing the hovered bubble", () => {
    const items = RECORDED.people.items;
    const target = items[items.length - 1];
    const { rerender } = render(<PeopleBubble items={items} onSelect={() => undefined} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: new RegExp(`^${target.person}: `) }));
    expect(() => rerender(<PeopleBubble items={[items[0]]} onSelect={() => undefined} />)).not.toThrow();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("flow sankey survives losing the hovered band", () => {
    const flow = RECORDED.flow;
    const { rerender } = render(<FlowSankey flow={flow} onSelect={() => undefined} />);
    const bands = screen.getAllByRole("button");
    fireEvent.mouseEnter(bands[bands.length - 1]);
    const one = flow.links[0];
    const narrowed = {
      total: one.conversations,
      user_types: [{ name: one.user_type, conversations: one.conversations }],
      outcomes: [{ name: one.outcome, conversations: one.conversations }],
      links: [one],
    };
    expect(() => rerender(<FlowSankey flow={narrowed} onSelect={() => undefined} />)).not.toThrow();
  });

  it("drop-off chart survives losing the hovered row", () => {
    const items = RECORDED.dropoff.items;
    const { rerender } = render(<DropoffChart items={items} onSelect={() => undefined} />);
    const rows = screen.getAllByRole("button");
    fireEvent.mouseEnter(rows[rows.length - 1]);
    expect(() => rerender(<DropoffChart items={items.slice(0, 1)} onSelect={() => undefined} />)).not.toThrow();
  });
});

describe("per-card error boundary", () => {
  function Boom(): React.ReactElement {
    throw new Error("bad chart");
  }

  it("keeps a failing chart inside its card and retries on new data", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <>
        <ChartCard title="Broken" resetKey={1}>
          <Boom />
        </ChartCard>
        <ChartCard title="Healthy">
          <p>fine</p>
        </ChartCard>
      </>,
    );
    expect(screen.getByText("This chart could not be displayed.")).toBeInTheDocument();
    expect(screen.getByText("fine")).toBeInTheDocument();
    rerender(
      <>
        <ChartCard title="Broken" resetKey={2}>
          <p>recovered</p>
        </ChartCard>
        <ChartCard title="Healthy">
          <p>fine</p>
        </ChartCard>
      </>,
    );
    expect(screen.getByText("recovered")).toBeInTheDocument();
    spy.mockRestore();
  });
});
