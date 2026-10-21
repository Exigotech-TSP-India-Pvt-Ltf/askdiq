"use client";

import React from "react";
import { FileText, Flag, MessagesSquare } from "lucide-react";
import { governanceApi, governanceKeys, type GovernanceTraceDetail, type WaterfallStage } from "@/lib/governance-api";
import { formatCountExact, formatDateTime, formatMs, formatUsd, humanize } from "@/lib/governance-format";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { useApiQuery } from "@/lib/query-cache";
import type { Audience } from "@/lib/types";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { EntityLink } from "@/components/ui/EntityLink";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonText } from "@/components/ui/Skeleton";
import { CopyButton } from "@/components/ui/CopyButton";
import { Waterfall, type WaterfallStep } from "@/components/charts/Waterfall";
import { TraceStatusBadge } from "./badges";

const STAGE_LABEL: Record<string, string> = {
  embedding: "Embedding",
  retrieval: "Retrieval",
  reranker: "Reranker",
  llm: "LLM",
  guardrail_pre: "Guardrail (pre)",
  guardrail_post: "Guardrail (post)",
};

export const stageLabel = (stage: string) => STAGE_LABEL[stage] ?? humanize(stage);

/** Waterfall steps from the API offsets; stages without timings are left out. */
export function waterfallSteps(detail: Pick<GovernanceTraceDetail, "stages">): WaterfallStep[] {
  const steps: WaterfallStep[] = detail.stages.stages
    .filter((s): s is WaterfallStage & { duration_ms: number } => s.duration_ms !== null)
    .map((s) => ({ label: stageLabel(s.stage), value: s.duration_ms, start: s.start_ms ?? undefined }));
  if (detail.stages.other_ms > 0) {
    const end = steps.reduce((max, s) => Math.max(max, (s.start ?? 0) + s.value), 0);
    steps.push({ label: "Other", value: detail.stages.other_ms, start: end, tone: "info" });
  }
  return steps;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg border border-line bg-surface-card px-3 py-2">
      <dt className="text-[11px] font-medium text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-semibold tabular-nums text-fg">{children}</dd>
    </div>
  );
}

function Section({ title, icon: Icon, children }: { title: string; icon?: typeof FileText; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-fg-muted">
        {Icon ? <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" /> : null}
        {title}
      </h3>
      {children}
    </section>
  );
}

function TraceBody({ detail }: { detail: GovernanceTraceDetail }) {
  const steps = waterfallSteps(detail);
  const missing = detail.stages.stages.filter((s) => s.duration_ms === null);
  const tokens = detail.tokens.total;

  return (
    <>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Field label="Outcome">{detail.outcome ? humanize(detail.outcome) : "—"}</Field>
        <Field label="Total latency">{formatMs(detail.total_ms)}</Field>
        <Field label="Slowest stage">
          {detail.slowest_stage ? `${stageLabel(detail.slowest_stage.stage)} · ${formatMs(detail.slowest_stage.ms)}` : "—"}
        </Field>
        <Field label="Tokens">{formatCountExact(tokens)}</Field>
        <Field label="Cost">{formatUsd(detail.cost_usd)}</Field>
        <Field label="Grounded">{detail.grounded === null ? "—" : detail.grounded ? "Yes" : "No"}</Field>
        <Field label="User">{detail.user_id ?? "—"}</Field>
        <Field label="Client IP">{detail.ip_address ?? "—"}</Field>
        <Field label="Time">
          {/* Short date-time so the tile never truncates; timezone in the tooltip (VA-44). */}
          <RelativeTime value={detail.timestamp} display="short" />
        </Field>
      </dl>

      <Section title="Conversation" icon={MessagesSquare}>
        {detail.conversation_id ? (
          <EntityLink entity={{ kind: "conversation", id: detail.conversation_id }} />
        ) : (
          <p className="text-xs text-fg-subtle">No conversation was persisted for this request.</p>
        )}
      </Section>

      {detail.query ? (
        <Section title="Query (redacted)">
          <p className="whitespace-pre-wrap break-words rounded-lg border border-line bg-surface-muted p-3 text-sm text-fg">
            {detail.query}
          </p>
        </Section>
      ) : null}

      <Section title="Stage waterfall">
        {steps.length > 0 ? (
          <Waterfall
            data={steps}
            label="Pipeline stages"
            height={Math.max(120, steps.length * 34) + 16}
            formatValue={formatMs}
            valueLabel="Duration"
            xAxisLabel="Elapsed time since request start"
          />
        ) : (
          <p className="text-xs text-fg-subtle">No stage timings were recorded.</p>
        )}
        {missing.length > 0 ? (
          <p className="mt-2 text-[11px] text-fg-subtle">
            No data: {missing.map((s) => stageLabel(s.stage)).join(", ")}
          </p>
        ) : null}
      </Section>

      <Section title={`Sources (${detail.sources.length})`} icon={FileText}>
        {detail.sources.length === 0 ? (
          <p className="text-xs text-fg-subtle">No sources were retrieved.</p>
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {detail.sources.map((src) => (
              <li key={src.chunk_id} className="flex items-center justify-between gap-3 px-3 py-2">
                <span className="min-w-0">
                  {src.document_id ? (
                    <EntityLink entity={{ kind: "chunk", id: src.chunk_id, documentId: src.document_id }} copyable={false}>
                      {src.source_name ?? src.document_id}
                    </EntityLink>
                  ) : (
                    <span className="truncate text-sm text-fg">{src.source_name ?? src.chunk_id}</span>
                  )}
                </span>
                <span className="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
                  {src.score === null ? "score —" : `score ${src.score.toFixed(3)}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Flags" icon={Flag}>
        {detail.flags.length === 0 ? (
          <p className="text-xs text-fg-subtle">No guardrail flags.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {detail.flags.map((flag) => (
              <li
                key={flag}
                className="rounded-full border border-status-warn/40 bg-status-warn-bg px-2 py-0.5 text-[11px] font-medium text-status-warn-fg"
              >
                {humanize(flag)}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

export interface TraceDrawerProps {
  requestId: string | null;
  audience: Audience;
  token: string | null;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

/** Trace inspector: waterfall, sources with scores, flags, conversation link. */
export function TraceDrawer({ requestId, audience, token, onClose, onPrev, onNext }: TraceDrawerProps) {
  const query = useApiQuery<GovernanceTraceDetail>(
    requestId ? governanceKeys.trace(requestId, audience) : null,
    (signal) => governanceApi.trace(token, requestId ?? "", audience, signal),
    { enabled: Boolean(token && requestId) },
  );
  const detail = query.data;
  const notFound = query.error && (query.error as { status?: number }).status === 404;

  return (
    <DrawerShell
      open={requestId !== null}
      onClose={onClose}
      onPrev={onPrev}
      onNext={onNext}
      title={
        <>
          Trace <span className="truncate font-mono text-xs text-fg-muted">{requestId}</span>
          {requestId ? <CopyButton value={requestId} label="Copy request id" /> : null}
        </>
      }
      description={detail ? `${formatDateTime(detail.timestamp)} · ${formatMs(detail.total_ms)}` : undefined}
      headerExtra={detail ? <TraceStatusBadge status={detail.status} /> : null}
    >
      {query.isLoading ? <SkeletonText lines={6} label="Loading trace" /> : null}
      {query.error && !detail ? (
        <ErrorState
          title={notFound ? "Trace not found" : "Couldn't load this trace"}
          message={notFound ? "It may be outside the selected audience." : undefined}
          requestId={requestId}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
      ) : null}
      {detail ? <TraceBody detail={detail} /> : null}
    </DrawerShell>
  );
}
