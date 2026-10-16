import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { HealthBanner } from "@/components/admin/governance/HealthBanner";
import { JsonViewer } from "@/components/admin/governance/JsonViewer";
import { StatusTimeline, buildTimeline } from "@/components/admin/governance/StatusTimeline";
import { IncidentList, incidentLogsHref } from "@/components/admin/governance/IncidentList";
import { waterfallSteps } from "@/components/admin/governance/TraceDrawer";
import { LevelBadge, LiveIndicator, TraceStatusBadge } from "@/components/admin/governance/badges";
import { semanticTokens, hexToRgb } from "@/lib/theme-tokens";
import type { GovernanceLogItem, HealthState, HealthStatus } from "@/lib/governance-api";
import { stageRows, quotaUtilisation } from "@/app/(app)/admin/governance/monitoring/MonitoringView";

const html = () => document.documentElement;

afterEach(() => {
  html().classList.remove("dark");
});

function health(status: HealthState): HealthStatus {
  return {
    status,
    window_minutes: 15,
    signals: {
      error_rate: { value: 2, unit: "%", status, degraded_at: 2, incident_at: 10 },
      p95_latency: { value: 1200, unit: "ms", status: "Healthy", degraded_at: 4000, incident_at: 8000 },
      quota_utilization: { value: null, unit: "%", status: "Healthy", degraded_at: 80, incident_at: 95 },
    },
  };
}

const rgb = (hex: string) => `rgb(${hexToRgb(hex).join(", ")})`;

function incident(id: string, createdAt: string, level: GovernanceLogItem["level"] = "CRITICAL"): GovernanceLogItem {
  return {
    id,
    created_at: createdAt,
    level,
    service: "system",
    action: "server_error",
    summary: `Server error ${id}`,
    detail: {},
    user_id: null,
    ip_address: null,
    request_id: `req-${id}`,
  };
}

describe("HealthBanner", () => {
  const cases: [HealthState, string, keyof typeof semanticTokens.light][] = [
    ["Healthy", "All systems healthy", "status-ok-bg"],
    ["Degraded", "Service degraded", "status-warn-bg"],
    ["Incident", "Incident in progress", "status-danger-bg"],
  ];

  it.each(cases)("shows the %s banner with its token colour in light and dark", async (status, title, token) => {
    render(
      <MotionProvider>
        <HealthBanner health={health(status)} />
      </MotionProvider>,
    );
    const banner = screen.getByRole("status", { name: `System health: ${status}` });
    expect(banner).toHaveAttribute("data-health", status);
    expect(screen.getByText(title)).toBeInTheDocument();
    expect(banner.style.backgroundColor).toBe(rgb(semanticTokens.light[token]));

    act(() => html().classList.add("dark"));
    await vi_waitFor(() => expect(banner.style.backgroundColor).toBe(rgb(semanticTokens.dark[token])));
  });

  it("lists every signal with its thresholds", () => {
    render(<HealthBanner health={health("Degraded")} />);
    expect(screen.getByText("Error rate")).toBeInTheDocument();
    expect(screen.getByText("1.2 s")).toBeInTheDocument();
    expect(screen.getByText(/degraded ≥ 4 s · incident ≥ 8 s/)).toBeInTheDocument();
  });
});

// Small local polling helper so the colour assertion can wait for Motion.
async function vi_waitFor(assertion: () => void, timeout = 2000) {
  const start = Date.now();
  for (;;) {
    try {
      assertion();
      return;
    } catch (err) {
      if (Date.now() - start > timeout) throw err;
      await act(() => new Promise((r) => setTimeout(r, 20)));
    }
  }
}

describe("JsonViewer", () => {
  it("renders detail as escaped text, never as HTML", () => {
    const { container } = render(
      <JsonViewer value={{ query: "<script>alert('x')</script>", nested: { img: "<img src=x onerror=alert(1)>" } }} />,
    );
    const toggle = screen.getByRole("button", { name: /show event detail/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const pre = screen.getByTestId("json-viewer");
    expect(pre.textContent).toContain("<script>alert('x')</script>");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(pre.innerHTML).toContain("&lt;script&gt;");
    fireEvent.click(screen.getByRole("button", { name: /hide event detail/i }));
    expect(screen.queryByTestId("json-viewer")).toBeNull();
  });
});

describe("StatusTimeline", () => {
  const hours = ["2026-10-15T01:00:00Z", "2026-10-15T02:00:00Z", "2026-10-15T03:00:00Z"];
  const signal = health("Healthy").signals.error_rate;

  it("classifies hours and buckets incidents into them", () => {
    const cells = buildTimeline(hours, [0, 5, 12], [10, 10, 0], [incident("a", "2026-10-15T03:20:00")], signal);
    expect(cells.map((c) => c.state)).toEqual(["Healthy", "Degraded", "Incident"]);
    expect(cells[2].incidents.map((i) => i.id)).toEqual(["a"]);
    const quiet = buildTimeline(hours, [0, 0, 0], [0, 0, 0], [], signal);
    expect(quiet.every((c) => c.state === "empty")).toBe(true);
  });

  it("links an incident marker to Logs filtered to that hour", () => {
    render(
      <StatusTimeline
        hours={hours}
        errorRates={[0, 0, 0]}
        requests={[1, 1, 1]}
        incidents={[incident("a", "2026-10-15T02:10:00Z"), incident("b", "2026-10-15T02:40:00Z", "ALERT")]}
        signal={signal}
      />,
    );
    const marker = screen.getByTestId("incident-marker");
    expect(marker).toHaveTextContent("2");
    expect(marker).toHaveAttribute(
      "href",
      "/admin/governance?tab=Logs&level=CRITICAL%2CALERT&at=2026-10-15T02%3A00%3A00Z",
    );
  });
});

describe("IncidentList", () => {
  it("highlights fresh rows and links each row to Logs", () => {
    const items = [incident("new", "2026-10-15T02:10:00Z"), incident("old", "2026-10-15T01:10:00Z", "ALERT")];
    render(<IncidentList items={items} freshIds={new Set(["new"])} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveAttribute("data-fresh", "true");
    expect(rows[1]).not.toHaveAttribute("data-fresh");
    const href = incidentLogsHref(items[0]);
    expect(href).toContain("tab=Logs");
    expect(href).toContain("level=CRITICAL");
    expect(href).toContain("q=req-new");
    expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", href);
  });

  it("shows an empty state", () => {
    render(<IncidentList items={[]} freshIds={new Set()} />);
    expect(screen.getByText("No incidents")).toBeInTheDocument();
  });
});

describe("badges", () => {
  it("use status tokens in both themes", () => {
    render(
      <>
        <LevelBadge level="CRITICAL" />
        <TraceStatusBadge status="ok" />
        <LiveIndicator status="reconnecting" />
      </>,
    );
    expect(screen.getByText("CRITICAL").className).toContain("bg-status-danger-bg");
    expect(screen.getByText("Ok").className).toContain("bg-status-ok-bg");
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
  });
});

describe("monitoring helpers", () => {
  it("keeps stages without samples as null, never 0 (G2)", () => {
    const rows = stageRows({
      stage_latency: {
        embedding: { avg_ms: null, sample_count: 0, unit: "ms" },
        llm: { avg_ms: 950.5, sample_count: 12, unit: "ms" },
      },
      stage_percentiles: { llm: { p50_ms: 900, p95_ms: 1400, p99_ms: 1800, sample_count: 12, unit: "ms" } },
    });
    const byStage = Object.fromEntries(rows.map((r) => [r.stage, r]));
    expect(byStage.embedding.avg).toBeNull();
    expect(byStage.reranker.avg).toBeNull();
    expect(byStage.llm).toMatchObject({ avg: 950.5, p95: 1400, samples: 12 });
  });

  it("derives quota utilisation from the worst headroom", () => {
    expect(quotaUtilisation({ chat: { tpm_headroom_pct: 30 }, embedding: { tpm_headroom_pct: 80 } }, "tpm_headroom_pct")).toBe(70);
    expect(quotaUtilisation({}, "rpm_headroom_pct")).toBeNull();
  });

  it("builds waterfall steps from offsets and skips stages without data", () => {
    const steps = waterfallSteps({
      stages: {
        unit: "ms",
        total_ms: 1300,
        other_ms: 100,
        stages: [
          { stage: "embedding", start_ms: 0, duration_ms: 50 },
          { stage: "reranker", start_ms: null, duration_ms: null },
          { stage: "llm", start_ms: 200, duration_ms: 950 },
        ],
      },
    });
    expect(steps.map((s) => [s.label, s.start, s.value])).toEqual([
      ["Embedding", 0, 50],
      ["LLM", 200, 950],
      ["Other", 1150, 100],
    ]);
  });
});
