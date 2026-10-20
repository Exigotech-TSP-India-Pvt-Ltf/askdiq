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
const BUNDLE_PARTS = ["summary", "trend", "hourly", "flow", "topics", "quality", "dropoff", "people", "conversations", "pending"];

/**
 * The recorded table page as the server pages it: `limit` / `offset` slice
 * the recorded rows (the first 25 of 49) and the page echoes its params.
 */
export function pagedConversations(params: URLSearchParams, base: ConversationList = RECORDED.conversations): ConversationList {
  const limit = Number(params.get("limit") ?? 10);
  const offset = Number(params.get("offset") ?? 0);
  return {
    ...base,
    items: base.items.slice(offset, offset + limit),
    limit,
    offset,
    sort: (params.get("sort") ?? "start") as ConversationList["sort"],
    direction: (params.get("dir") ?? "desc") as ConversationList["direction"],
  };
}

/**
 * Installs `global.fetch`; `overrides` replace a recorded response per endpoint
 * (a function receives the request's query; `undefined` answers 404).
 */
export function mockInsightsFetch(overrides: Partial<Record<string, unknown>> = {}) {
  const calls: RecordedCall[] = [];
  const answer = (endpoint: string, params: URLSearchParams): unknown => {
    const override = overrides[endpoint];
    return endpoint in overrides
      ? typeof override === "function"
        ? (override as (params: URLSearchParams) => unknown)(params)
        : override
      : endpoint === "conversations"
        ? pagedConversations(params)
        : (RECORDED as unknown as Record<string, unknown>)[endpoint];
  };
  const json = (body: unknown) =>
    body === undefined
      ? new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })
      : new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length) : url.pathname;
    if (path === "bundle") {
      // The page's one request: answered (and recorded) per widget.
      const asked = url.searchParams.getAll("parts");
      const parts = asked.length ? asked : BUNDLE_PARTS;
      const bundle: Record<string, unknown> = { as_of: "2026-06-30T00:00:00+00:00" };
      for (const part of BUNDLE_PARTS) bundle[part] = null;
      for (const part of parts) {
        calls.push({ endpoint: part, params: url.searchParams });
        const body = answer(part, url.searchParams);
        if (body === undefined) return json(undefined);
        bundle[part] = body;
      }
      return json(bundle);
    }
    const endpoint = path.startsWith("conversations/") ? "conversation_detail" : path;
    calls.push({ endpoint, params: url.searchParams });
    return json(answer(endpoint, url.searchParams));
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
