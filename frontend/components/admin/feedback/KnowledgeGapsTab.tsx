"use client";

import React from "react";
import Link from "next/link";
import { m } from "motion/react";
import { ArrowUpRight, FilePlus2, FileSearch, ListFilter, SearchX } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { focusRing } from "@/components/ui/styles";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import type { FeedbackAnalytics, KnowledgeGap } from "@/lib/feedback-api";
import type { FilterPatch } from "@/lib/feedback-filters";
import { useCan } from "@/lib/use-can";
import { RelativeTime } from "@/components/ui/RelativeTime";

export interface KnowledgeGapsTabProps {
  analytics: FeedbackAnalytics | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onFilter: (patch: FilterPatch) => void;
}

const LINK = `group inline-flex items-center gap-1 rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-fg-muted transition-colors duration-150 hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 ${focusRing}`;

/** Documents search for the gap's question (the Documents page reads `q`). */
export function openDocumentHref(gap: KnowledgeGap): string {
  return `/documents?q=${encodeURIComponent(gap.query.slice(0, 120))}`;
}

export const ADD_DOCUMENT_HREF = "/documents?action=upload";

/**
 * Knowledge Gaps tab: topics ranked by negative feedback
 * and low retrieval confidence, with links into Documents.
 */
export function KnowledgeGapsTab({ analytics, loading, error, onRetry, onFilter }: KnowledgeGapsTabProps) {
  const container = useMotionVariants(stagger);
  const item = useMotionVariants(fadeUp);
  const canAddDocument = useCan("documents:write");

  if (error && !analytics) {
    return <ErrorState title="Could not load knowledge gaps" message={error instanceof Error ? error.message : undefined} onRetry={onRetry} />;
  }
  if (loading || !analytics) return <SkeletonRows rows={4} columns={3} label="Loading knowledge gaps" />;

  const gaps = [...analytics.knowledge_gaps].sort(
    (a, b) => b.count - a.count || a.avg_rating - b.avg_rating || (a.avg_confidence ?? 1) - (b.avg_confidence ?? 1),
  );
  if (gaps.length === 0) {
    return (
      <EmptyState
        icon={SearchX}
        title="No knowledge gaps detected"
        description="Gaps appear when low-rated answers also had low retrieval confidence."
      />
    );
  }

  return (
    <m.ol variants={container} initial="hidden" animate="visible" className="space-y-3" aria-label="Knowledge gaps">
      {gaps.map((gap, i) => (
        <m.li
          key={`${gap.message_id}-${i}`}
          variants={item}
          className="flex flex-col gap-3 rounded-2xl border border-line bg-surface-card p-4 shadow-xs transition duration-150 hover:border-brand-300 hover:shadow-md motion-safe:hover:-translate-y-0.5 motion-reduce:transition-none dark:hover:border-brand-700/60 sm:flex-row sm:items-center"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-muted text-xs font-bold tabular-nums text-fg-muted">
            {i + 1}
          </span>
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-sm font-medium text-fg">{gap.query}</p>
            <p className="mt-0.5 text-[11px] text-fg-subtle">
              {gap.count} low rating{gap.count === 1 ? "" : "s"} · avg {gap.avg_rating.toFixed(1)} stars · retrieval confidence{" "}
              {gap.avg_confidence === null ? "unknown" : gap.avg_confidence.toFixed(2)}
              {gap.last_seen ? <RelativeTime value={gap.last_seen} prefix=" · last seen " /> : null}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={LINK} onClick={() => onFilter({ message_id: gap.message_id, status: "all" })}>
              <ListFilter className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Show in queue
            </button>
            <Link href={openDocumentHref(gap)} className={LINK}>
              <FileSearch className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Open document
              <ArrowUpRight
                className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                strokeWidth={1.75}
                aria-hidden="true"
              />
            </Link>
            {canAddDocument ? (
              <Link href={ADD_DOCUMENT_HREF} className={LINK}>
                <FilePlus2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                Add document
              </Link>
            ) : null}
          </div>
        </m.li>
      ))}
    </m.ol>
  );
}
