"use client";

import React, { useCallback, useMemo, useState } from "react";
import { AlertTriangle, Download, FileJson, Lock } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { primaryButton } from "@/components/ui/styles";
import { useApiQuery } from "@/lib/query-cache";
import { downloadPairs, feedbackKeys, previewPairs, reasonLabel, type PreferencePair } from "@/lib/feedback-api";

export const PREVIEW_LIMIT = 50;

export interface PreferenceDataTabProps {
  token: string;
  canExport: boolean;
}

function Cell({ text, missing }: { text: string | null; missing: boolean }) {
  if (missing || !text) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-warn-bg px-2 py-0.5 text-[11px] font-semibold text-status-warn-fg">
        <AlertTriangle className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
        Missing
      </span>
    );
  }
  return <p className="line-clamp-3 text-fg">{text}</p>;
}

/** Preference Data tab (planchanges4 F5.4): preview, incomplete pairs flagged, JSONL download. */
export function PreferenceDataTab({ token, canExport }: PreferenceDataTabProps) {
  const toast = useToast();
  const [completeOnly, setCompleteOnly] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const fetcher = useCallback(
    (signal: AbortSignal) => previewPairs(token, { complete_only: completeOnly }, PREVIEW_LIMIT, signal),
    [token, completeOnly],
  );
  const { data, error, isLoading, refetch } = useApiQuery(canExport ? feedbackKeys.pairs(completeOnly) : null, fetcher);

  const counts = useMemo(() => {
    const pairs = data?.pairs ?? [];
    const complete = pairs.filter((p) => p.complete).length;
    return { total: pairs.length, complete, incomplete: pairs.length - complete };
  }, [data]);

  if (!canExport) {
    return (
      <EmptyState
        icon={Lock}
        title="Export needs the feedback:export permission"
        description="Admins and superadmins can preview and download preference pairs."
      />
    );
  }

  const download = async () => {
    setDownloading(true);
    try {
      const filename = await downloadPairs(token, { complete_only: completeOnly });
      toast.show({ title: "Export downloaded", description: filename, tone: "success" });
    } catch (err) {
      toast.show({ title: "Export failed", description: err instanceof Error ? err.message : undefined, tone: "error" });
    } finally {
      setDownloading(false);
    }
  };

  let body: React.ReactNode;
  if (error && !data) {
    body = <ErrorState title="Could not load the preview" message={error instanceof Error ? error.message : undefined} onRetry={() => void refetch()} />;
  } else if (isLoading || !data) {
    body = <SkeletonRows rows={4} columns={4} label="Loading preference pairs" />;
  } else if (data.pairs.length === 0) {
    body = <EmptyState icon={FileJson} title="No preference pairs yet" description="Ratings with an answer become pairs; a corrected answer completes them." />;
  } else {
    body = (
      <table className="w-full text-left text-xs" aria-label="Preference pairs preview">
        <thead className="border-b border-line bg-surface-muted/40 text-[11px] uppercase tracking-wide text-fg-subtle">
          <tr>
            <th scope="col" className="px-4 py-2.5">Prompt</th>
            <th scope="col" className="px-3 py-2.5">Chosen</th>
            <th scope="col" className="px-3 py-2.5">Rejected</th>
            <th scope="col" className="px-3 py-2.5">Rating</th>
            <th scope="col" className="px-4 py-2.5">Quality</th>
          </tr>
        </thead>
        <tbody>
          {data.pairs.map((p: PreferencePair) => (
            <tr
              key={p.feedback_id}
              data-testid="pair-row"
              data-complete={p.complete ? "true" : "false"}
              className={`border-b border-line/70 align-top last:border-0 ${p.complete ? "" : "bg-status-warn-bg/40"}`}
            >
              <td className="max-w-xs px-4 py-3">
                <Cell text={p.prompt} missing={p.missing.includes("prompt")} />
              </td>
              <td className="max-w-xs px-3 py-3">
                <Cell text={p.chosen} missing={p.missing.includes("chosen")} />
              </td>
              <td className="max-w-xs px-3 py-3">
                <Cell text={p.rejected} missing={p.missing.includes("rejected")} />
              </td>
              <td className="px-3 py-3 tabular-nums text-fg-muted">
                {p.rating}
                {p.reason_tags.length > 0 ? <p className="text-[11px] text-fg-subtle">{p.reason_tags.map(reasonLabel).join(", ")}</p> : null}
              </td>
              <td className="px-4 py-3">
                {p.complete ? (
                  <span className="rounded-full bg-status-ok-bg px-2 py-0.5 text-[11px] font-semibold text-status-ok-fg">Complete</span>
                ) : (
                  <span className="rounded-full bg-status-warn-bg px-2 py-0.5 text-[11px] font-semibold text-status-warn-fg">
                    Incomplete: {p.missing.join(", ")}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <TableCard
      title="Preference data"
      description={`Chosen vs rejected answers for a later DPO / SFT run. Preview of the first ${PREVIEW_LIMIT} pairs; text is PII-masked.`}
      badge={
        data ? (
          <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted" aria-label="Preview counts">
            {counts.complete} complete · {counts.incomplete} incomplete{data.truncated ? " (preview)" : ""}
          </span>
        ) : null
      }
      headerActions={
        <>
          <label className="flex items-center gap-1.5 text-xs text-fg-muted">
            <input
              type="checkbox"
              checked={completeOnly}
              onChange={(e) => setCompleteOnly(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-line accent-brand-600"
            />
            Complete pairs only
          </label>
          <button type="button" onClick={() => void download()} disabled={downloading} className={primaryButton}>
            <Download className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            {downloading ? "Downloading…" : "Download JSONL"}
          </button>
        </>
      }
    >
      {body}
    </TableCard>
  );
}
