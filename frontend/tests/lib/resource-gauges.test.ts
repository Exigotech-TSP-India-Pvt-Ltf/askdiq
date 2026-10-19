import { describe, expect, it } from "vitest";
import { resourceGauges } from "@/lib/resource-gauges";

describe("resource gauges", () => {
  it("labels scope: container limits win over the server", () => {
    const gauges = resourceGauges({
      effective: { cpu_percent: 62, cpu_scope: "container", memory_percent: 71, memory_scope: "container" },
      container: { scope: "container", cpu_percent: 62, memory_percent: 71, memory_mb: 1450, memory_limit_mb: 2048 },
      host: { scope: "host", cpu_percent: 20, memory_percent: 30, memory_total_mb: 16000, hosts: 1 },
      process: { scope: "process", rss_mb: 512, cpu_percent: 80 },
      event_loop: { scope: "process", lag_p95_ms: 250, lag_max_ms: 400 },
      db_pool: { scope: "process", in_use_percent: 40, checked_out: 12, capacity: 30 },
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
  });

  it("falls back to server scope and shows no data without samples", () => {
    const gauges = resourceGauges(undefined);
    expect(gauges.map((g) => g.label)).toEqual(["Server CPU", "Server memory", "API workers RSS", "Event-loop lag", "DB pool"]);
    expect(gauges.every((g) => g.value === null)).toBe(true);
  });
});
