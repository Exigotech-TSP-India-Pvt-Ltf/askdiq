import { describe, expect, it } from "vitest";
import { quotaTrend, resourceGauges } from "@/lib/resource-gauges";

describe("resource gauges", () => {
  it("labels scope: container limits win over the server", () => {
    const gauges = resourceGauges({
      effective: { cpu_percent: 62, cpu_scope: "container", memory_percent: 71, memory_scope: "container" },
      container: { scope: "container", cpu_percent: 62, memory_percent: 71, memory_mb: 1450, memory_limit_mb: 2048 },
      host: { scope: "host", cpu_percent: 20, memory_percent: 30, memory_total_mb: 16000, hosts: 1 },
      process: { scope: "process", rss_mb: 512, cpu_percent: 80 },
      event_loop: { scope: "process", lag_p95_ms: 250, lag_max_ms: 400 },
      db_pool: { scope: "process", in_use_percent: 40, checked_out: 12, capacity: 30 },
      queries: { scope: "process", in_use_percent: 6.3, active: 8, capacity: 128 },
      workers_live: 2,
    });
    const byKey = Object.fromEntries(gauges.map((g) => [g.key, g]));
    expect(byKey.cpu.label).toBe("Container CPU");
    expect(byKey.cpu.value).toBe(62);
    expect(byKey.memory.label).toBe("Container memory");
    expect(byKey.memory.caption).toContain("1450 MB");
    expect(byKey.rss.label).toBe("API workers RSS");
    expect(byKey.rss.value).toBeCloseTo(25); // 512 of the 2048 MB limit
    expect(byKey.rss.caption).toContain("2 workers");
    expect(byKey.loop.value).toBe(25); // 250 ms on a 1 s dial
    expect(byKey.pool.caption).toBe("DB pool · 12/30 in use");
    expect(byKey.queries.value).toBe(6.3);
    expect(byKey.queries.caption).toBe("Chat in flight · 8/128 LLM slots");
  });

  it("falls back to server scope and shows no data without samples", () => {
    const gauges = resourceGauges(undefined);
    expect(gauges.map((g) => g.label)).toEqual([
      "Server CPU",
      "Server memory",
      "API workers RSS",
      "Event-loop lag",
      "DB pool",
      "Chat in flight",
    ]);
    expect(gauges.every((g) => g.value === null)).toBe(true);
  });

  it("builds 15-minute trends on the gauge scale (older samples dropped)", () => {
    const now = Date.parse("2026-10-20T12:00:00Z");
    const at = (min: number) => new Date(now - min * 60_000).toISOString();
    const point = (min: number, v: number) => ({
      ts: at(min),
      cpu_percent: v,
      memory_percent: v / 2,
      rss_mb: 1024,
      loop_lag_p95_ms: 100,
      db_pool_percent: null,
      queries_percent: v / 10,
    });
    const gauges = resourceGauges(
      {
        host: { scope: "host", cpu_percent: 1, memory_percent: 1, memory_total_mb: 4096, hosts: 1 },
        history: [point(20, 99), point(10, 40), point(5, 60)],
      },
      now,
    );
    const byKey = Object.fromEntries(gauges.map((g) => [g.key, g]));
    expect(byKey.cpu.trend.map((p) => p.value)).toEqual([40, 60]);
    expect(byKey.memory.trend.map((p) => p.display)).toEqual(["20%", "30%"]);
    expect(byKey.rss.trend[0]).toMatchObject({ value: 25, display: "1024 MB" });
    expect(byKey.loop.trend[0]).toMatchObject({ value: 10, display: "100 ms" });
    expect(byKey.pool.trend).toEqual([]);
    expect(byKey.queries.trend.map((p) => p.value)).toEqual([4, 6]);
  });

  it("quota trend is the busiest of chat and embedding", () => {
    const now = Date.parse("2026-10-20T12:00:00Z");
    const ts = new Date(now - 60_000).toISOString();
    const trend = quotaTrend(
      [
        {
          ts,
          utilization_pct: 0,
          chat_tpm_headroom_pct: 70,
          chat_rpm_headroom_pct: 90,
          embedding_tpm_headroom_pct: 40,
          embedding_rpm_headroom_pct: null,
          azure_429_count_60s: 0,
        },
      ],
      "tpm",
      now,
    );
    expect(trend).toEqual([{ ts, value: 60, display: "60%" }]);
    expect(quotaTrend(undefined, "rpm", now)).toEqual([]);
  });
});
