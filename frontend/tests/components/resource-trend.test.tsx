import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ResourceTrend } from "@/components/admin/governance/ResourceTrend";

const points = [
  { ts: "2026-10-20T11:50:00Z", value: 20, display: "20%" },
  { ts: "2026-10-20T11:55:00Z", value: 90, display: "90%" },
  { ts: "2026-10-20T12:00:00Z", value: 50, display: "50%" },
];

describe("ResourceTrend", () => {
  it("summarises now / min / max and draws the warning threshold", () => {
    render(<ResourceTrend points={points} warn={70} danger={90} label="Server CPU" />);
    expect(screen.getByRole("img", { name: "Server CPU, last 15 min: now 50%, min 20%, max 90%" })).toBeTruthy();
    expect(screen.getByTestId("resource-trend-threshold")).toBeTruthy();
  });

  it("shows min / max / now in the tooltip on focus", () => {
    render(<ResourceTrend points={points} warn={70} danger={90} label="Server CPU" />);
    fireEvent.focus(screen.getByRole("img"));
    expect(screen.getByRole("tooltip").textContent).toContain("Now 50% · Min 20% · Max 90%");
  });

  it("waits for two samples before drawing", () => {
    render(<ResourceTrend points={points.slice(0, 1)} warn={70} danger={90} label="Server CPU" />);
    expect(screen.getByTestId("resource-trend-empty")).toBeTruthy();
  });
});
