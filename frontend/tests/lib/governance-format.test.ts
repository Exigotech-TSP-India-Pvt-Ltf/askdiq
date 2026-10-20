import { describe, expect, it } from "vitest";
import {
  axisWidthFor,
  formatByUnit,
  formatMs,
  latencyTick,
  niceTicks,
  windowSourceLabel,
} from "@/lib/governance-format";
import { buildQuery, governanceStreamUrl, logsExportUrl } from "@/lib/governance-api";
import { hourWindow, logsHref, parseList, parseOne, rangeBounds } from "@/lib/governance-links";
import { LOG_LEVELS, TRACE_STATUSES } from "@/lib/governance-api";

describe("latency tick formatter (G1)", () => {
  it("formats 0.18 ms through 1.2 s with the right unit", () => {
    expect(latencyTick(0.18)).toBe("0.18 ms");
    expect(formatMs(0.5)).toBe("0.5 ms");
    expect(formatMs(4.25)).toBe("4.3 ms");
    expect(formatMs(180)).toBe("180 ms");
    expect(formatMs(999)).toBe("999 ms");
    expect(formatMs(1000)).toBe("1 s");
    expect(latencyTick(1200)).toBe("1.2 s");
    expect(formatMs(12_500)).toBe("13 s");
  });

  it("never shows tiny seconds (the old `0.00018 s` defect)", () => {
    for (const v of [0.18, 0.00018 * 1000, 1, 42]) expect(formatMs(v)).toMatch(/ ms$/);
  });

  it("handles zero and missing values", () => {
    expect(formatMs(0)).toBe("0 ms");
    expect(formatMs(null)).toBe("—");
    expect(formatMs(undefined)).toBe("—");
    expect(formatMs(Number.NaN)).toBe("—");
  });

  it("sizes the axis to its longest label instead of a fixed 40 px", () => {
    const narrow = axisWidthFor(["0 ms", "5 ms"]);
    const wide = axisWidthFor(["0 ms", "0.18 ms", "1.2 s", "250 ms"]);
    expect(wide).toBeGreaterThan(narrow);
    expect(wide).toBeGreaterThanOrEqual("0.18 ms".length * 6);
    expect(axisWidthFor([])).toBeGreaterThan(0);
  });

  it("builds nice ticks from 0", () => {
    expect(niceTicks(1180)).toEqual([0, 500, 1000, 1500, 2000]);
    expect(niceTicks(0.18)).toEqual([0, 0.05, 0.1, 0.15, 0.2]);
    expect(niceTicks(0)).toEqual([0, 1]);
  });
});

describe("KPI formatting and labels (G7)", () => {
  it("formats by unit", () => {
    expect(formatByUnit(12.345, "%")).toBe("12.3%");
    expect(formatByUnit(1200, "ms")).toBe("1.2 s");
    expect(formatByUnit(3.5, "USD")).toBe("$3.50");
    expect(formatByUnit(1234, null)).toBe("1,234");
  });

  it("labels each count with its window and source", () => {
    expect(windowSourceLabel("24h", "latency_metrics")).toBe("24h · latency records");
    // Overall p50 and p95 share this source, so both hints say the same population.
    expect(windowSourceLabel("24h", "latency_metrics.pipeline")).toBe("24h · RAG queries");
    expect(windowSourceLabel("since_process_start", "in_memory_counter")).toBe("since restart · process counter");
    expect(windowSourceLabel("today_utc", "chat_messages.cost_usd")).toMatch(/^since 00:00 UTC · /);
    expect(windowSourceLabel("24h", "chat_messages.outcome+audit_log.server_error")).toBe(
      "24h · chat messages.outcome + audit log.server error",
    );
  });
});

describe("query strings and links", () => {
  it("repeats array params and drops empty ones", () => {
    expect(buildQuery({ level: ["ERROR", "ALERT"], q: "", cursor: null, limit: 50 })).toBe(
      "?level=ERROR&level=ALERT&limit=50",
    );
    expect(buildQuery({})).toBe("");
  });

  it("omits the overview audience and encodes filters in export / stream URLs", () => {
    expect(governanceStreamUrl("overview")).toBe("/api/proxy/admin/governance/stream");
    expect(governanceStreamUrl("testing")).toBe("/api/proxy/admin/governance/stream?audience=testing");
    expect(logsExportUrl({ audience: "users", level: ["CRITICAL"], q: "a&b" })).toBe(
      "/api/proxy/admin/governance/logs/export.csv?audience=users&level=CRITICAL&q=a%26b",
    );
  });

  it("never trusts URL values", () => {
    expect(parseList("ERROR,bogus,<script>,ALERT,ERROR", LOG_LEVELS)).toEqual(["ERROR", "ALERT"]);
    expect(parseOne("failed", TRACE_STATUSES)).toBe("failed");
    expect(parseOne("drop table", TRACE_STATUSES)).toBeUndefined();
    expect(hourWindow("not-a-date")).toBeNull();
    expect(hourWindow("2026-10-15T03:00:00Z")).toEqual({
      from: "2026-10-15T03:00:00.000Z",
      to: "2026-10-15T04:00:00.000Z",
    });
  });

  it("keeps preset ranges open-ended and ends custom ranges after their last day", () => {
    const from = new Date(2026, 9, 1);
    const to = new Date(2026, 9, 14);
    expect(rangeBounds({ kind: "7d", from, to })).toEqual({ from: from.toISOString() });
    expect(rangeBounds({ kind: "custom", from, to })).toEqual({
      from: from.toISOString(),
      to: new Date(2026, 9, 15).toISOString(),
    });
    expect(rangeBounds(undefined)).toEqual({});
  });

  it("starts rolling presets on a whole minute (stable cache key)", () => {
    const from = new Date(Date.UTC(2026, 9, 1, 10, 30, 41, 123));
    expect(rangeBounds({ kind: "7d", from, to: new Date() })).toEqual({ from: "2026-10-01T10:30:00.000Z" });
  });

  it("builds Logs deep links", () => {
    expect(logsHref({ level: ["CRITICAL", "ALERT"], at: "2026-10-15T03:00:00Z" })).toBe(
      "/admin/governance?tab=Logs&level=CRITICAL%2CALERT&at=2026-10-15T03%3A00%3A00Z",
    );
  });
});
