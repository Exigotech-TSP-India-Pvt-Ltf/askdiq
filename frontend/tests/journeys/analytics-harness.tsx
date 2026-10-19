/**
 * Shared harness for the Analytics journey tests: a fetch mock that serves
 * the recorded `/admin/insights/*` responses and records every request, and
 * a stand-in for the code-split Recharts treemap (jsdom has no layout, so
 * ResponsiveContainer renders nothing there).
 */
import React from "react";
import { vi } from "vitest";
import recorded from "../fixtures/analytics/insights_responses.json";
import type {
  ConversationDetail,
  ConversationList,
  InsightsDropoff,
  InsightsFlow,
  InsightsHourly,
  InsightsPeople,
  InsightsQuality,
  InsightsSummary,
  InsightsTopics,
  InsightsTrend,
  PendingClassification,
} from "@/lib/insights-api";
import type { TreemapChartProps } from "@/components/charts";

export interface RecordedResponses {
  summary: InsightsSummary;
  trend: InsightsTrend;
  hourly: InsightsHourly;
  flow: InsightsFlow;
  topics: InsightsTopics;
  quality: InsightsQuality;
  dropoff: InsightsDropoff;
  people: InsightsPeople;
  conversations: ConversationList;
  conversation_detail: ConversationDetail;
  pending: PendingClassification;
}

export const RECORDED = recorded as unknown as RecordedResponses;

export interface RecordedCall {
  endpoint: string;
  params: URLSearchParams;
}

const PREFIX = "/api/proxy/admin/insights/";

/**
 * Installs `global.fetch`; `overrides` replace a recorded response per endpoint
 * (a function receives the request's query; `undefined` answers 404).
 */
export function mockInsightsFetch(overrides: Partial<Record<string, unknown>> = {}) {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length) : url.pathname;
    const endpoint = path.startsWith("conversations/") ? "conversation_detail" : path;
    calls.push({ endpoint, params: url.searchParams });
    const override = overrides[endpoint];
    const body =
      endpoint in overrides
        ? typeof override === "function"
          ? (override as (params: URLSearchParams) => unknown)(url.searchParams)
          : override
        : (RECORDED as unknown as Record<string, unknown>)[endpoint];
    if (body === undefined) return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    fetchMock,
    last: (endpoint: string) => [...calls].reverse().find((c) => c.endpoint === endpoint),
  };
}

/** Stand-in for the next/dynamic Recharts treemap: one button per tile, same names. */
export function TreemapStandIn({ data, onSelect, label }: TreemapChartProps) {
  return (
    <div role="group" aria-label={label}>
      {data.map((d) => (
        <button
          key={d.name}
          type="button"
          aria-pressed={d.selected}
          aria-label={`${d.name}: ${d.value}${d.detail ? `, ${d.detail}` : ""}`}
          data-color={d.color}
          onClick={() => onSelect?.(d)}
        >
          {d.name}
        </button>
      ))}
    </div>
  );
}

/** Puts the page at `/admin/analytics` with the given query string. */
export function setUrl(search = ""): void {
  window.history.replaceState(null, "", `/admin/analytics${search}`);
}

export function currentParams(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}
