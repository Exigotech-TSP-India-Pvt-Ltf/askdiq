"use client";

import type { LucideIcon } from "lucide-react";
import { ArrowUpRight, CircleCheck, CircleHelp, MessagesSquare, Repeat, ShieldCheck, Users } from "lucide-react";
import type { InsightsSummary } from "@/lib/insights-api";
import type { AnalyticsState } from "./url-state";
import { CountUp } from "@/components/ui/CountUp";
import { fmtDecimal, fmtInt, fmtPct } from "./format";

export interface KpiDef {
  key: string;
  label: string;
  icon: LucideIcon;
  value: (s: InsightsSummary) => number | null;
  format: (v: number) => string;
  hint: (s: InsightsSummary) => string;
  /** Where the records behind the number are (a tab, pre-filtered). */
  target: Partial<AnalyticsState>;
  targetLabel: string;
}

/** The six KPIs of the HTML's `metrics()` / `kpiCards()`. */
export const KPIS: readonly KpiDef[] = [
  {
    key: "conversations",
    target: { tab: "conversations" },
    targetLabel: "the conversations",
    label: "Conversations",
    icon: MessagesSquare,
    value: (s) => s.conversations,
    format: fmtInt,
    hint: (s) => `${fmtInt(s.active_conversations)} with a question`,
  },
  {
    key: "questions",
    target: { tab: "topics" },
    targetLabel: "questions by topic",
    label: "Substantive questions",
    icon: CircleHelp,
    value: (s) => s.substantive_questions,
    format: fmtInt,
    hint: (s) => `of ${fmtInt(s.exchanges)} exchanges (${fmtInt(s.handled_exchanges)} handled)`,
  },
  {
    key: "handled",
    target: { tab: "topics" },
    targetLabel: "how questions were answered",
    label: "Questions handled",
    icon: CircleCheck,
    value: (s) => s.handled_pct,
    format: (v) => fmtPct(v),
    hint: (s) => `${fmtInt(s.handled_questions)} of ${fmtInt(s.substantive_questions)}`,
  },
  {
    key: "served",
    target: { tab: "conversations", outcome: "Served" },
    targetLabel: "the served conversations",
    label: "Conversations served",
    icon: ShieldCheck,
    value: (s) => s.served_pct,
    format: (v) => fmtPct(v),
    hint: (s) => `${fmtInt(s.served_conversations)} of ${fmtInt(s.active_conversations)}`,
  },
  {
    key: "people",
    target: { tab: "dropoff" },
    targetLabel: "conversations per person",
    label: "Probable people",
    icon: Users,
    value: (s) => s.people,
    format: fmtInt,
    hint: () => "Account e-mail, else network; pseudonymous",
  },
  {
    key: "avg",
    target: { tab: "conversations", sort: "exchanges", dir: "desc" },
    targetLabel: "the longest conversations",
    label: "Avg. questions / conv.",
    icon: Repeat,
    value: (s) => s.avg_questions_per_conversation,
    format: (v) => fmtDecimal(v),
    hint: () => "Per conversation with a question",
  },
];

export interface KpiStripProps {
  summary: InsightsSummary | undefined;
  loading?: boolean;
  /** Opens the records behind a KPI (its `target`). */
  onOpen?: (target: Partial<AnalyticsState>) => void;
  /** Hover / focus on "View records": warm what it opens. */
  onIntent?: (target: Partial<AnalyticsState>) => void;
}

export function KpiStrip({ summary, loading = false, onOpen, onIntent }: KpiStripProps) {
  return (
    <section aria-label="Key figures" data-tour="analytics-kpis" className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {KPIS.map((k) => {
        const Icon = k.icon;
        return (
          <article
            key={k.key}
            data-testid={`kpi-${k.key}`}
            aria-busy={loading || undefined}
            className="flex flex-col gap-2 rounded-2xl border border-line bg-surface-card p-4 shadow-xs transition duration-150 hover:border-brand-300 hover:shadow-md motion-safe:hover:-translate-y-0.5 motion-reduce:transition-none dark:hover:border-brand-700/60"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
              <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            </div>
            <p className="text-2xl font-semibold leading-none tabular-nums text-fg">
              {summary ? (
                <CountUp value={k.value(summary)} format={k.format} fallback="n/a" />
              ) : (
                <span className="inline-block h-6 w-12 rounded-sm bg-surface-muted motion-safe:animate-pulse" aria-hidden="true" />
              )}
            </p>
            <h3 className="text-xs font-medium text-fg-muted">{k.label}</h3>
            {summary && <p className="text-[11px] text-fg-subtle">{k.hint(summary)}</p>}
            {onOpen && (
              <button
                type="button"
                onClick={() => onOpen(k.target)}
                onMouseEnter={onIntent ? () => onIntent(k.target) : undefined}
                onFocus={onIntent ? () => onIntent(k.target) : undefined}
                aria-label={`${k.label}: open ${k.targetLabel}`}
                className="mt-auto inline-flex items-center gap-1 self-start rounded-sm text-[11px] font-semibold text-brand-700 hover:underline focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300"
              >
                View records
                <ArrowUpRight className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
              </button>
            )}
          </article>
        );
      })}
    </section>
  );
}
