"use client";

import { ArrowUpRight } from "lucide-react";
import { ChartCard, toChartTable } from "@/components/charts";
import { LazyMount } from "@/components/charts/LazyMount";
import type { InsightsFilters } from "@/lib/insights-api";
import { DropoffChart, DropoffInsight } from "./DropoffChart";
import { FlowSankey } from "./FlowSankey";
import { dayLabel } from "./format";
import { HourOfDayChart } from "./HourOfDayChart";
import { PeopleBubble } from "./PeopleBubble";
import { QualityDonut, orderQuality } from "./QualityDonut";
import { TopicsTreemap } from "./TopicsTreemap";
import { TrendChart } from "./TrendChart";
import { errorText, pageOf, useInsightsAggregate } from "./use-insights";
import { toggleDay, toggleValue, type AnalyticsState } from "./url-state";

export type LabelKind = "intent" | "quality" | "issue" | "person";

export interface CardProps {
  state: AnalyticsState;
  filters: InsightsFilters;
  update: (patch: Partial<AnalyticsState>) => void;
  /** Remembers the readable name of a cross-filter value (chips). */
  learnLabel?: (kind: LabelKind, label: string) => void;
}

const TAG = "rounded-full border border-line bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-fg-muted";
const LINK_BUTTON =
  "group inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[11px] font-semibold text-brand-700 transition-colors duration-150 hover:border-brand-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300 dark:hover:border-brand-700/60";

function ViewConversations({ update }: { update: CardProps["update"] }) {
  return (
    <button type="button" className={LINK_BUTTON} onClick={() => update({ tab: "conversations" })}>
      View conversations
      <ArrowUpRight
        className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none"
        strokeWidth={1.75}
        aria-hidden="true"
      />
    </button>
  );
}

/** The day picked through the trend chart (from = to = day), if any. */
export function selectedDay(state: AnalyticsState): string | null {
  return state.period === "custom" && state.from && state.from === state.to ? state.from : null;
}

export function TrendCard({ state, filters, update }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("trend", filters, pageOf(state));
  const days = data?.days ?? [];
  const range = days.length ? `${dayLabel(days[0].day)} – ${dayLabel(days[days.length - 1].day)}` : "";
  return (
    <ChartCard
      title="Daily volume"
      subtitle="Substantive questions and conversations per day. Click a day to focus on it; click again to clear."
      meta={range ? <span className={TAG}>{range}</span> : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && days.length === 0}
      emptyMessage="Choose a valid date range."
      bodyClassName="h-[250px] w-full"
      table={toChartTable(days, [
        { key: "day", label: "Day" },
        { key: "questions", label: "Questions", numeric: true },
        { key: "conversations", label: "Conversations", numeric: true },
      ])}
    >
      <TrendChart days={days} selectedDay={selectedDay(state)} onSelectDay={(day) => update(toggleDay(state, day))} />
    </ChartCard>
  );
}

export function HourCard({ state, filters, update }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("hourly", filters, pageOf(state));
  const hours = data?.hours ?? [];
  return (
    <ChartCard
      title="Questions by hour of day"
      subtitle={data ? `Substantive questions by hour (${data.tz}, like the Visitors heatmap). Click an hour to filter.` : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && (data?.total ?? 0) === 0}
      emptyMessage="No questions in this selection."
      bodyClassName="h-[300px] w-full"
      table={toChartTable(
        hours.map((v, hour) => ({ hour: `${hour}:00`, questions: v })),
        [
          { key: "hour", label: "Hour" },
          { key: "questions", label: "Questions", numeric: true },
        ],
      )}
    >
      <HourOfDayChart hours={hours} selected={state.hour} onSelect={(hour) => update(toggleValue(state, "hour", hour))} />
    </ChartCard>
  );
}

export function FlowCard({ state, filters, update }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("flow", filters, pageOf(state));
  return (
    <ChartCard
      title="User type → outcome"
      subtitle={data ? `${data.total} conversations with a question` : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && (data?.total ?? 0) === 0}
      emptyMessage="No conversations in this selection."
      bodyClassName="min-h-[190px] w-full"
      table={toChartTable(data?.links ?? [], [
        { key: "user_type", label: "User type" },
        { key: "outcome", label: "Outcome" },
        { key: "conversations", label: "Conversations", numeric: true },
      ])}
    >
      {data && (
        <FlowSankey
          flow={data}
          onSelect={(l) => update({ ...toggleValue(state, "user_type", l.user_type), ...toggleValue(state, "outcome", l.outcome) })}
        />
      )}
    </ChartCard>
  );
}

export function TopicsCard({ state, filters, update, learnLabel }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("topics", filters, pageOf(state));
  const items = data?.items ?? [];
  return (
    <ChartCard
      title="Question topics"
      subtitle="Size = volume, shade = handled %. Click a topic to filter."
      actions={state.intent ? <ViewConversations update={update} /> : undefined}
      meta={!state.intent && data ? <span className={TAG}>{`${data.total} questions`}</span> : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && items.length === 0}
      emptyMessage="No questions in this selection."
      bodyClassName="h-[280px] w-full"
      table={toChartTable(items, [
        { key: "label", label: "Topic" },
        { key: "questions", label: "Questions", numeric: true },
        { key: "handled", label: "Handled", numeric: true },
        { key: "handled_pct", label: "Handled %", numeric: true, format: (v) => `${Number(v).toFixed(0)}%` },
      ])}
    >
      <TopicsTreemap
        items={items}
        selected={state.intent}
        onSelect={(t) => {
          learnLabel?.("intent", t.label);
          update(toggleValue(state, "intent", t.intent));
        }}
      />
    </ChartCard>
  );
}

export function QualityCard({ state, filters, update, learnLabel }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("quality", filters, pageOf(state));
  const items = orderQuality(data?.items ?? []);
  return (
    <ChartCard
      title="How questions were answered"
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && (data?.total ?? 0) === 0}
      emptyMessage="No questions in this selection."
      bodyClassName="min-h-[260px] w-full"
      table={toChartTable(items, [
        { key: "label", label: "Answer type" },
        { key: "questions", label: "Questions", numeric: true },
        { key: "pct", label: "Share", numeric: true, format: (v) => `${Number(v).toFixed(0)}%` },
      ])}
    >
      <QualityDonut
        items={items}
        selected={state.quality}
        onSelect={(q) => {
          learnLabel?.("quality", q.label);
          update(toggleValue(state, "quality", q.quality));
        }}
      />
    </ChartCard>
  );
}

export function DropoffCard({ state, filters, update, learnLabel }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("dropoff", filters, pageOf(state));
  const items = data?.items ?? [];
  return (
    <ChartCard
      title="Where conversations dropped"
      subtitle="Issue of the last unhandled question, Partly and Not served conversations"
      actions={state.issue ? <ViewConversations update={update} /> : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && items.length === 0}
      emptyMessage="No dropped conversations in this selection."
      bodyClassName="min-h-[120px] w-full"
      table={toChartTable(items, [
        { key: "label", label: "Issue" },
        { key: "conversations", label: "Conversations", numeric: true },
      ])}
    >
      <DropoffChart
        items={items}
        selected={state.issue}
        onSelect={(d) => {
          learnLabel?.("issue", d.label);
          update(toggleValue(state, "issue", d.issue));
        }}
      />
      {/* Fills the space beside the taller people chart with the headline numbers (VA-28). */}
      <DropoffInsight items={items} total={data?.total} />
    </ChartCard>
  );
}

export function PeopleCard({ state, filters, update, learnLabel }: CardProps) {
  const { data, error, isLoading, refetch } = useInsightsAggregate("people", filters, pageOf(state));
  const items = data?.items ?? [];
  return (
    <ChartCard
      title="Conversations per person"
      subtitle={data ? `${data.total} probable people (account e-mail, else network). Click a person for their visitors.` : undefined}
      loading={isLoading}
      error={errorText(error)}
      onRetry={() => void refetch()}
      resetKey={data}
      empty={!isLoading && !error && items.length === 0}
      emptyMessage="No people in this selection."
      bodyClassName="min-h-[260px] w-full"
      table={toChartTable(items, [
        { key: "person", label: "Person" },
        { key: "conversations", label: "Conversations", numeric: true },
        { key: "dominant_outcome", label: "Mostly" },
      ])}
    >
      {/* Thousands of marks, below the fold: mounted when scrolled near. */}
      <LazyMount minHeight={220}>
        <PeopleBubble
          items={items}
          selected={state.person}
          onSelect={(p) => {
            learnLabel?.("person", p.person);
            update(toggleValue(state, "person", p.person_id));
          }}
        />
      </LazyMount>
    </ChartCard>
  );
}
