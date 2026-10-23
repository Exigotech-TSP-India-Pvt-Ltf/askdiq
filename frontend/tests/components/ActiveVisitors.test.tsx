import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { Users } from "lucide-react";
import { ActiveVisitorsInfo, ActiveVisitorsPanel } from "@/components/admin/ActiveVisitorsPanel";
import { DASHBOARD_KPIS } from "@/components/admin/dashboard/DashboardKpis";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import {
  ACTIVE_VISITORS_DEFINITION,
  ACTIVE_VISITORS_LABEL,
  periodWindow,
  trendLabel,
  type ActiveVisitors,
} from "@/lib/active-visitors";

const DATA: ActiveVisitors = {
  definition: ACTIVE_VISITORS_DEFINITION,
  active_now: 3,
  active_now_minutes: 5,
  dau: { value: 12, previous: 8, delta: 0.5, days: 1 },
  wau: { value: 40, previous: 0, delta: null, days: 7 },
  mau: { value: 90, previous: 100, delta: -0.1, days: 30 },
};

describe("active visitors definition tip", () => {
  it("describes the button with the exact definition and toggles on focus / Escape", () => {
    render(<ActiveVisitorsInfo />);
    const button = screen.getByRole("button", { name: "About Active visitors" });
    expect(button).toHaveAccessibleDescription(ACTIVE_VISITORS_DEFINITION);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveAttribute("data-state", "closed");
    fireEvent.focus(button);
    expect(tip).toHaveAttribute("data-state", "open");
    fireEvent.keyDown(button, { key: "Escape" });
    expect(tip).toHaveAttribute("data-state", "closed");
    fireEvent.click(button);
    expect(tip).toHaveAttribute("data-state", "open");
  });

  it("states every rule of the definition", () => {
    const text = ACTIVE_VISITORS_DEFINITION.toLowerCase();
    for (const phrase of ["distinct", "at least one query", "client id", "ip address", "test ips", "excluded"]) {
      expect(text).toContain(phrase);
    }
  });
});

describe("ActiveVisitorsPanel", () => {
  it("shows active now, DAU, WAU and MAU with trends vs the previous window", () => {
    render(<ActiveVisitorsPanel data={DATA} />);
    const panel = screen.getByTestId("active-visitors");
    expect(within(panel).getByRole("heading", { name: ACTIVE_VISITORS_LABEL })).toBeInTheDocument();
    expect(within(panel).getByText("Active visitors now")).toBeInTheDocument();
    expect(within(panel).getByText("Last 5 minutes")).toBeInTheDocument();
    expect(within(panel).getByText("Daily active visitors")).toBeInTheDocument();
    expect(within(panel).getByText("12")).toBeInTheDocument();
    // VA-41: the shared delta pill; the comparison window is its tooltip.
    expect(panel.querySelector('[title="vs previous 24 hours"]')).toHaveTextContent("+50%");
    expect(within(panel).getByText("Weekly active visitors")).toBeInTheDocument();
    expect(within(panel).getByText("40")).toBeInTheDocument();
    expect(panel.querySelector('[title="vs previous 30 days"]')).toHaveTextContent("-10%");
    expect(panel.querySelector('[title="vs previous 7 days"]')).toBeNull();
    expect(within(panel).getByRole("button", { name: "About Active visitors" })).toBeInTheDocument();
  });

  it("can hide active now and renders nothing without data", () => {
    const { rerender, container } = render(<ActiveVisitorsPanel data={DATA} showNow={false} />);
    expect(screen.queryByText("Active visitors now")).not.toBeInTheDocument();
    expect(screen.getByText("Monthly active visitors")).toBeInTheDocument();
    rerender(<ActiveVisitorsPanel data={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("formats windows and trends", () => {
    expect(periodWindow(1)).toBe("Last 24 hours");
    expect(periodWindow(7)).toBe("Last 7 days");
    expect(trendLabel(DATA.wau)).toBeNull();
    expect(trendLabel({ value: 2, previous: 2, delta: 0, days: 7 })).toEqual({ value: "0% vs previous 7 days", isPositive: true });
  });
});

describe("dashboard cards", () => {
  it("labels both visitor KPIs 'Active visitors'", () => {
    const users = DASHBOARD_KPIS.find((d) => d.key === "users");
    const active = DASHBOARD_KPIS.find((d) => d.key === "active_users");
    expect(users?.label).toBe("Active visitors");
    expect(active?.label).toBe("Active visitors (24h)");
  });

  it("KpiTrendCard renders the tip outside its link", () => {
    render(
      <KpiTrendCard label="Active visitors" value="7" trend={[]} href="/admin/users" icon={Users} info={<ActiveVisitorsInfo />} />,
    );
    const link = screen.getByRole("link");
    const button = screen.getByRole("button", { name: "About Active visitors" });
    expect(link).not.toContainElement(button);
  });
});
