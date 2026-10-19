import React from "react";
import { Sparkles } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";

interface JudgeSummary {
  faithfulness?: unknown;
  relevance?: unknown;
  completeness?: unknown;
  /** Number of live answers the online LLM judge scored (absent on older servers). */
  n?: unknown;
}

const isScore = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;

/**
 * True when the live report holds at least one answer scored by the online
 * LLM judge. The server sends `judge.n` (judged samples); without it, any
 * positive score counts. With the judge off (ONLINE_EVAL_ENABLED=false) the
 * scores are null, or 0 on an empty report, so this is false.
 */
export function hasJudgedSamples(report: { n_cases?: unknown; judge?: JudgeSummary | null } | null | undefined): boolean {
  const judge = report?.judge;
  if (!judge || report?.n_cases === 0) return false;
  if (typeof judge.n === "number") return judge.n > 0;
  return [judge.faithfulness, judge.relevance, judge.completeness].some(isScore);
}

/** Shown on Evaluation (Live) > Semantic Quality instead of "— / 5.0" cards. */
export function JudgeDisabledState() {
  return (
    <div
      data-testid="judge-disabled"
      className="rounded-2xl border border-dashed border-line-brand/70 bg-surface-card/90 dark:border-brand-500/25"
    >
      <EmptyState
        icon={Sparkles}
        title="No LLM-judged answers in this range"
        description={
          <>
            Faithfulness, relevance and completeness come from the online LLM judge, which is off or has not scored
            any live answer yet. Set <code className="font-mono text-fg">ONLINE_EVAL_ENABLED=true</code> on the
            backend to sample live answers for semantic quality and citation scoring; scores appear here once the
            first sampled answers are judged.
          </>
        }
      />
    </div>
  );
}
