"use client";

import type { LucideIcon } from "lucide-react";
import { CircleCheck, CircleHelp, MessagesSquare, Repeat, ShieldCheck, Users } from "lucide-react";
import type { InsightsSummary } from "@/lib/insights-api";
import { CountUp } from "./CountUp";
import { fmtDecimal, fmtInt, fmtPct } from "./format";

export interface KpiDef {
  key: string;
  label: string;
  icon: LucideIcon;
  value: (s: InsightsSummary) => number | null;
  format: (v: number) => string;
  hint: (s: InsightsSummary) => string;
}

/** The six KPIs of the HTML's `metrics()` / `kpiCards()` (newplan §6.4). */
export const KPIS: readonly KpiDef[] = [
  {
    key: "conversations",
    label: "Conversations",
    icon: MessagesSquare,
    value: (s) => s.conversations,
    format: fmtInt,
    hint: (s) => `${fmtInt(s.active_conversations)} with a question`,
  },
  {
    key: "questions",
    label: "Substantive questions",
    icon: CircleHelp,
    value: (s) => s.substantive_questions,
    format: fmtInt,
    hint: (s) => `of ${fmtInt(s.exchanges)} exchanges (${fmtInt(s.handled_exchanges)} handled)`,
  },
  {
    key: "handled",
    label: "Questions handled",
    icon: CircleCheck,
    value: (s) => s.handled_pct,
    format: (v) => fmtPct(v),
    hint: (s) => `${fmtInt(s.handled_questions)} of ${fmtInt(s.substantive_questions)}`,
  },
  {
    key: "served",
    label: "Conversations served",
    icon: ShieldCheck,
    value: (s) => s.served_pct,
    format: (v) => fmtPct(v),
    hint: (s) => `${fmtInt(s.served_conversations)} of ${fmtInt(s.active_conversations)}`,
  },
  {
    key: "people",
    label: "Probable people",
    icon: Users,
    value: (s) => s.people,
    format: fmtInt,
    hint: () => "Pseudonymous, no PII",
  },
  {
    key: "avg",
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
}

export function KpiStrip({ summary, loading = false }: KpiStripProps) {
  return (
    <section aria-label="Key figures" className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {KPIS.map((k) => {
        const Icon = k.icon;
        return (
          <article
            key={k.key}
            data-testid={`kpi-${k.key}`}
            aria-busy={loading || undefined}
            className="flex flex-col gap-2 rounded-2xl border border-line bg-surface-card p-4 shadow-sm transition duration-150 hover:border-brand-300 hover:shadow-md motion-safe:hover:-translate-y-0.5 motion-reduce:transition-none dark:hover:border-brand-700/60"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
              <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            </div>
            <p className="text-2xl font-semibold leading-none tabular-nums text-fg">
              {summary ? (
                <CountUp value={k.value(summary)} format={k.format} />
              ) : (
                <span className="inline-block h-6 w-12 rounded bg-surface-muted motion-safe:animate-pulse" aria-hidden="true" />
              )}
            </p>
            <h3 className="text-xs font-medium text-fg-muted">{k.label}</h3>
            {summary && <p className="text-[11px] text-fg-subtle">{k.hint(summary)}</p>}
          </article>
        );
      })}
    </section>
  );
}
