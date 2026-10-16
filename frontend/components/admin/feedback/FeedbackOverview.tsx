"use client";

import React, { useMemo } from "react";
import { m } from "motion/react";
import { AlertTriangle, ArrowUpRight, MessageSquare, Percent, ThumbsDown, ThumbsUp } from "lucide-react";
import { ChartCard } from "@/components/charts/ChartCard";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import { StackedArea } from "@/components/charts/StackedArea";
import { Funnel, type FunnelStage } from "@/components/charts/Funnel";
import { QuadrantScatter, type ScatterPoint } from "@/components/charts/QuadrantScatter";
import { BarList, type BarDatum } from "@/components/charts/BarList";
import type { SparkPoint } from "@/components/charts/Sparkline";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { focusRing } from "@/components/ui/styles";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import {
  OUTCOMES,
  TRIAGE_LABELS,
  TRIAGE_STATUSES,
  isReasonTag,
  reasonLabel,
  type FeedbackAnalytics,
  type TrendPoint,
} from "@/lib/feedback-api";
import { outcomeLabel, type FilterPatch } from "@/lib/feedback-filters";

/** Spike alert: negative share above this (%) with at least `SPIKE_MIN_TOTAL` ratings. */
export const SPIKE_NEGATIVE_RATIO = 25;
export const SPIKE_MIN_TOTAL = 5;

/** Disagreement quadrant coordinates (x = human rating, y = groundedness). */
const LOW = 0.25;
const HIGH = 0.75;

type QuadrantId = "positive_grounded" | "positive_ungrounded" | "negative_grounded" | "negative_ungrounded";

interface QuadrantPoint extends ScatterPoint {
  id: QuadrantId;
}

const QUADRANTS: readonly { id: QuadrantId; label: string; x: number; y: number }[] = [
  { id: "positive_grounded", label: "Liked and grounded", x: HIGH, y: HIGH },
  { id: "positive_ungrounded", label: "Liked but ungrounded", x: HIGH, y: LOW },
  { id: "negative_grounded", label: "Disliked but grounded", x: LOW, y: HIGH },
  { id: "negative_ungrounded", label: "Disliked and ungrounded", x: LOW, y: LOW },
];

const percent = (v: number) => `${Math.round(v * 10) / 10}%`;
const shortDay = (day: string) => day.slice(5);

export interface FeedbackOverviewProps {
  analytics: FeedbackAnalytics | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  /** Every chart mark calls this: it filters the queue and opens it. */
  onFilter: (patch: FilterPatch) => void;
}

/** Overview tab (planchanges4 F5.2): KPIs, trend, distribution, breakdowns, funnel, quadrant. */
export function FeedbackOverview({ analytics, loading, error, onRetry, onFilter }: FeedbackOverviewProps) {
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);

  const derived = useMemo(() => {
    if (!analytics) return null;
    const trend = analytics.daily_trend;
    const spark = (pick: (p: TrendPoint) => number): SparkPoint[] =>
      trend.map((p) => ({ label: p.day, value: pick(p) }));
    const ratings: BarDatum[] = ["5", "4", "3", "2", "1"].map((r) => ({
      key: r,
      label: `${r} star${r === "1" ? "" : "s"}`,
      value: analytics.rating_distribution[r] ?? 0,
    }));
    const outcomes: BarDatum[] = Object.entries(analytics.feedback_by_outcome)
      .filter(([key]) => (OUTCOMES as readonly string[]).includes(key))
      .map(([key, b]) => ({ key, label: outcomeLabel(key), value: b.total }))
      .sort((a, b) => b.value - a.value);
    const reasons: BarDatum[] = analytics.top_reasons
      .filter((r) => isReasonTag(r.key))
      .map((r) => ({ key: r.key, label: reasonLabel(r.key), value: r.count }));
    const funnel: (FunnelStage & { key: string })[] = TRIAGE_STATUSES.filter((s) => s !== "dismissed").map((s) => ({
      key: s,
      label: TRIAGE_LABELS[s],
      // Rows currently in this status: exactly what a click filters the queue to.
      value: analytics.triage_funnel[s] ?? 0,
    }));
    const quadrant: QuadrantPoint[] = QUADRANTS.map((q) => ({
      id: q.id,
      label: `${q.label}: ${analytics.disagreement[q.id]}`,
      x: q.x,
      y: q.y,
      size: Math.max(analytics.disagreement[q.id], 0.5),
    }));
    // Daily coverage (ratings / answers, in %), not the ratings count.
    const coverage: SparkPoint[] = trend
      .filter((p) => typeof p.coverage === "number")
      .map((p) => ({ label: p.day, value: Math.round((p.coverage as number) * 1000) / 10 }));
    return { trend, spark, coverage, ratings, outcomes, reasons, funnel, quadrant };
  }, [analytics]);

  if (error && !analytics) {
    return <ErrorState title="Could not load feedback analytics" message={messageOf(error)} onRetry={onRetry} />;
  }
  if (loading || !analytics || !derived) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <SkeletonCard key={i} label={i === 0 ? "Loading feedback analytics" : undefined} />
        ))}
      </div>
    );
  }

  const spike = analytics.negative_ratio > SPIKE_NEGATIVE_RATIO && analytics.total >= SPIKE_MIN_TOTAL;
  const dayFilter = (p: SparkPoint) => onFilter({ day: p.label });

  return (
    <m.div variants={container} initial="hidden" animate="visible" className="space-y-6">
      {spike ? (
        <m.div
          data-tour="feedback-spike"
          variants={item}
          role="alert"
          className="flex flex-wrap items-start gap-3 rounded-xl border border-status-danger/40 bg-status-danger-bg p-4"
        >
          <AlertTriangle className="mt-0.5 h-[18px] w-[18px] shrink-0 text-status-danger" strokeWidth={1.75} aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-status-danger-fg">
              Negative feedback spike: {percent(analytics.negative_ratio)} of ratings are 1-2 stars
            </p>
            <p className="mt-0.5 text-xs text-fg-muted">
              Above the {SPIKE_NEGATIVE_RATIO}% threshold. Review the low-rated answers and promote failing turns to golden cases.
            </p>
          </div>
          <button
            type="button"
            onClick={() => onFilter({ sentiment: "negative" })}
            className={`group inline-flex items-center gap-1 rounded-lg border border-status-danger/40 px-2.5 py-1.5 text-xs font-semibold text-status-danger-fg transition-colors hover:bg-surface-card ${focusRing}`}
          >
            Review negative feedback
            <ArrowUpRight
              className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </button>
        </m.div>
      ) : null}

      <m.div data-tour="feedback-kpis" variants={item} className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTrendCard
          label="Total feedback"
          value={analytics.total.toLocaleString()}
          trend={derived.spark((p) => p.total)}
          onSelect={dayFilter}
          icon={MessageSquare}
          hint="All ratings for this audience (not the queue filters)"
        />
        <KpiTrendCard
          label="Positive (4-5 stars)"
          value={percent(analytics.positive_ratio)}
          trend={derived.spark((p) => p.positive)}
          onSelect={dayFilter}
          icon={ThumbsUp}
          tone="ok"
          hint={`${analytics.positive_count.toLocaleString()} ratings`}
        />
        <KpiTrendCard
          label="Needs improvement (1-2 stars)"
          value={percent(analytics.negative_ratio)}
          trend={derived.spark((p) => p.negative)}
          onSelect={dayFilter}
          goodDirection="down"
          icon={ThumbsDown}
          tone={spike ? "danger" : "warn"}
          hint={`${analytics.negative_count.toLocaleString()} ratings`}
        />
        <KpiTrendCard
          label="Coverage"
          value={percent(analytics.feedback_rate)}
          trend={derived.coverage}
          onSelect={dayFilter}
          icon={Percent}
          hint="Share of answers that received a rating"
        />
      </m.div>

      <m.div data-tour="feedback-charts-main" variants={item} className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          title="Satisfaction trend"
          subtitle="Positive and negative ratings per day. Click a day to filter the queue."
          className="xl:col-span-2"
          empty={derived.trend.length === 0}
          table={{
            columns: [
              { key: "day", label: "Day" },
              { key: "positive", label: "Positive", numeric: true },
              { key: "negative", label: "Negative", numeric: true },
              { key: "avg", label: "Avg rating", numeric: true },
            ],
            rows: derived.trend.map((p) => ({ day: p.day, positive: p.positive, negative: p.negative, avg: p.avg_rating ?? "—" })),
          }}
        >
          <StackedArea
            data={derived.trend.map((p) => ({ ...p, label: shortDay(p.day) }))}
            xKey="label"
            series={[
              { key: "positive", label: "Positive" },
              { key: "negative", label: "Negative" },
            ]}
            label="Satisfaction trend"
            onSelect={(row) => onFilter({ day: row.day })}
          />
        </ChartCard>
        <ChartCard
          title="Rating distribution"
          subtitle="Click a rating to filter."
          table={{
            columns: [
              { key: "label", label: "Rating" },
              { key: "value", label: "Count", numeric: true },
            ],
            rows: derived.ratings.map((r) => ({ label: r.label, value: r.value })),
          }}
        >
          <BarList
            data={derived.ratings}
            label="Rating distribution"
            onSelect={(d) => onFilter({ rating: Number(d.key) })}
            colorFor={(d, _i, ct) => (Number(d.key) >= 4 ? ct.status.ok : Number(d.key) === 3 ? ct.status.warn : ct.status.danger)}
          />
        </ChartCard>
      </m.div>

      <m.div data-tour="feedback-charts-breakdown" variants={item} className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        <ChartCard
          title="Feedback by outcome"
          subtitle="What the pipeline did on rated answers."
          empty={derived.outcomes.length === 0}
          table={{
            columns: [
              { key: "label", label: "Outcome" },
              { key: "value", label: "Ratings", numeric: true },
            ],
            rows: derived.outcomes.map((r) => ({ label: r.label, value: r.value })),
          }}
        >
          <BarList data={derived.outcomes} label="Feedback by outcome" onSelect={(d) => onFilter({ outcome: d.key })} />
        </ChartCard>
        <ChartCard
          title="Top reasons"
          subtitle="Thumbs-down reason chips."
          empty={derived.reasons.length === 0}
          emptyMessage="No reasons given yet"
          table={{
            columns: [
              { key: "label", label: "Reason" },
              { key: "value", label: "Count", numeric: true },
            ],
            rows: derived.reasons.map((r) => ({ label: r.label, value: r.value })),
          }}
        >
          <BarList
            data={derived.reasons}
            label="Top reasons"
            onSelect={(d) => onFilter({ reason: d.key })}
            colorFor={(_d, _i, ct) => ct.status.warn}
          />
        </ChartCard>
        <ChartCard
          title="Triage funnel"
          subtitle="Feedback in each triage status. Click a stage to filter the queue to it."
          table={{
            columns: [
              { key: "label", label: "Stage" },
              { key: "value", label: "In status", numeric: true },
            ],
            rows: derived.funnel.map((s) => ({ label: s.label, value: s.value })),
          }}
        >
          <Funnel
            data={derived.funnel}
            label="Triage funnel"
            onSelect={(stage) => {
              const match = derived.funnel.find((s) => s.label === stage.label);
              if (match) onFilter({ status: match.key });
            }}
          />
        </ChartCard>
        <ChartCard
          title="Human vs groundedness"
          subtitle="Disagreement quadrant. Click a quadrant to filter."
          table={{
            columns: [
              { key: "label", label: "Quadrant" },
              { key: "value", label: "Ratings", numeric: true },
            ],
            rows: QUADRANTS.map((q) => ({ label: q.label, value: analytics.disagreement[q.id] })),
          }}
        >
          <QuadrantScatter
            data={derived.quadrant}
            label="Disagreement quadrant"
            xMax={1}
            yMax={1}
            xThreshold={0.5}
            yThreshold={0.5}
            xLabel="Human rating"
            yLabel="Grounded"
            formatX={(v) => (v >= 0.5 ? "positive" : "negative")}
            formatY={(v) => (v >= 0.5 ? "yes" : "no")}
            quadrants={{ topLeft: "Disagree", bottomRight: "Disagree" }}
            onSelect={(p) => {
              const [sentiment, grounding] = p.id.split("_");
              onFilter({ sentiment, grounded: grounding === "grounded" });
            }}
          />
        </ChartCard>
      </m.div>
    </m.div>
  );
}

export function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Please try again.";
}
