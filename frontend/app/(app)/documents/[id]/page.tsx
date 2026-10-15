"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { ChevronDown, FileText, Flame, Hash, Layers, MessageSquareWarning, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { renderFormattedText } from "@/lib/format-text";
import { FormatBadge } from "@/components/documents/FormatBadge";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { ChartCard } from "@/components/charts/ChartCard";
import { CalendarHeatmap, type HeatCell } from "@/components/charts/CalendarHeatmap";
import { EntityLink } from "@/components/ui/EntityLink";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { focusRing } from "@/components/ui/styles";
import { SectionCard, ToneChip, errorMessage, formatDateTime, formatDay, ratingTone } from "@/components/admin/entity/shared";
import { useApiQuery } from "@/lib/query-cache";
import { useDeepLink } from "@/lib/use-deep-link";
import { usePrefersReducedMotion } from "@/lib/motion";
import {
  CHUNK_SEARCH_DEBOUNCE_MS,
  MAX_CHUNK_QUERY_CHARS,
  applyChunkQuery,
  getDocumentChunks,
  getLowRatedCitations,
  isChunkSort,
  type ChunkSort,
  type DocumentChunk,
} from "@/lib/entity-stats-api";

const PREVIEW_CHAR_LIMIT = 900;
const HEATMAP_CHUNKS = 15;

function strategyBadge(strategy: string) {
  const styles: Record<string, string> = {
    structure_aware: "bg-status-ok-bg text-status-ok-fg",
    semantic: "bg-status-info-bg text-status-info-fg",
  };
  return styles[strategy.toLowerCase()] ?? "bg-surface-muted text-fg-muted";
}

function ChunkCard({ chunk, highlighted }: { chunk: DocumentChunk; highlighted: boolean }) {
  const [expanded, setExpanded] = useState(highlighted);
  useEffect(() => {
    if (highlighted) setExpanded(true);
  }, [highlighted]);
  const isLong = chunk.content.length > PREVIEW_CHAR_LIMIT;
  const shown = !isLong || expanded ? chunk.content : `${chunk.content.slice(0, PREVIEW_CHAR_LIMIT)}…`;

  return (
    <article
      id={`chunk-${chunk.id}`}
      tabIndex={-1}
      aria-label={`Chunk ${chunk.chunk_index + 1}`}
      data-highlighted={highlighted || undefined}
      className={`overflow-hidden rounded-xl border bg-surface-card shadow-sm transition duration-150 focus:outline-none motion-reduce:transition-none ${
        highlighted ? "border-brand-500 ring-2 ring-brand-500/40" : "border-line hover:border-brand-300 dark:hover:border-brand-700/60"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-muted/50 px-4 py-2.5">
        <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700 dark:bg-brand-950/40 dark:text-brand-300">
          <Hash className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> Chunk {chunk.chunk_index + 1}
        </span>
        <span className="text-[11px] text-fg-subtle">{chunk.char_count.toLocaleString()} chars</span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${strategyBadge(chunk.strategy)}`}>
          {chunk.strategy.replace(/_/g, " ")}
        </span>
        {typeof chunk.hits === "number" ? (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-fg-muted" title="Times cited in answers">
            <Flame className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> {chunk.hits.toLocaleString()} hits
          </span>
        ) : null}
        {chunk.low_rated_hits ? <ToneChip tone="danger">{chunk.low_rated_hits} low-rated</ToneChip> : null}
        {chunk.section_heading && (
          <span className="truncate text-[11px] text-fg-subtle" title={chunk.section_heading}>
            · {chunk.section_heading}
          </span>
        )}
      </div>

      <div className="px-4 py-4 text-sm text-fg">
        {renderFormattedText(shown)}
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className={`mt-3 inline-flex items-center gap-1 rounded text-xs font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            {expanded ? "Show less" : "Show full chunk"}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} strokeWidth={1.75} aria-hidden="true" />
          </button>
        )}
      </div>
    </article>
  );
}

function DocumentDetailContent() {
  const { token } = useAuth();
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params?.id ?? "");
  const reducedMotion = usePrefersReducedMotion();
  const { get, set } = useDeepLink();

  const urlQuery = get("q") ?? "";
  const sortRaw = get("sort");
  const sort: ChunkSort = isChunkSort(sortRaw) ? sortRaw : "index";
  const focusChunk = get("chunk");

  const [searchInput, setSearchInput] = useState(urlQuery);
  const written = useRef(urlQuery);
  // Back / forward or a shared link changed `?q=`: reflect it in the box.
  useEffect(() => {
    if (urlQuery !== written.current) {
      written.current = urlQuery;
      setSearchInput(urlQuery);
    }
  }, [urlQuery]);
  // Debounce typing into the URL (and so into the request).
  useEffect(() => {
    const value = searchInput.trim().slice(0, MAX_CHUNK_QUERY_CHARS);
    if (value === written.current) return undefined;
    const timer = setTimeout(() => {
      written.current = value;
      set({ q: value || null });
    }, CHUNK_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, set]);

  const docs = useApiQuery(token ? ["documents", "list"] : null, () => api.listDocuments(token ?? ""));
  const doc = useMemo(() => docs.data?.find((d) => d.document_id === id) ?? null, [docs.data, id]);

  const chunks = useApiQuery(token && id ? ["document", id, "chunks", urlQuery, sort] : null, (signal) =>
    getDocumentChunks(token ?? "", id, { q: urlQuery, sort }, signal),
  );
  // Keep the previous result on screen while a new search loads (no flash).
  const lastChunks = useRef<DocumentChunk[] | null>(null);
  if (chunks.data) lastChunks.current = chunks.data;
  const rawChunks = chunks.data ?? lastChunks.current;
  const shown = useMemo(() => (rawChunks ? applyChunkQuery(rawChunks, urlQuery, sort) : null), [rawChunks, urlQuery, sort]);

  const lowRated = useApiQuery(token && id ? ["document", id, "low-rated"] : null, (signal) =>
    getLowRatedCitations(token ?? "", id, signal),
  );
  const lowRatedAbsent = !lowRated.isLoading && !lowRated.error && lowRated.data === null;

  const hasHits = (rawChunks ?? []).some((c) => Array.isArray(c.weekly_hits));
  const heatChunks = useMemo(
    () =>
      [...(rawChunks ?? [])]
        .filter((c) => (c.hits ?? 0) > 0)
        .sort((a, b) => (b.hits ?? 0) - (a.hits ?? 0))
        .slice(0, HEATMAP_CHUNKS),
    [rawChunks],
  );
  const { heatData, weeks } = useMemo(() => {
    const weekSet = new Set<string>();
    const cells: (HeatCell & { chunkId: string })[] = [];
    for (const c of heatChunks) {
      for (const w of c.weekly_hits ?? []) {
        weekSet.add(w.week_start);
        cells.push({ row: `#${c.chunk_index + 1}`, col: formatDay(w.week_start), value: w.hits, chunkId: c.id });
      }
    }
    const sortedWeeks = [...weekSet].sort().map(formatDay);
    return { heatData: cells, weeks: sortedWeeks };
  }, [heatChunks]);

  // `?chunk=` scrolls to and highlights the chunk once it is rendered.
  useEffect(() => {
    if (!focusChunk || !shown) return;
    const el = document.getElementById(`chunk-${focusChunk}`);
    if (!el) return;
    el.scrollIntoView?.({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    el.focus({ preventScroll: true });
  }, [focusChunk, shown, reducedMotion]);

  const totalChars = useMemo(() => rawChunks?.reduce((sum, c) => sum + c.char_count, 0) ?? 0, [rawChunks]);
  const selectChunk = useCallback((chunkId: string) => set({ chunk: chunkId }), [set]);
  const title = doc?.source_name ?? "Document";

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title={title}
        icon={FileText}
        showAudienceTabs={false}
        breadcrumb={[{ label: "Documents", href: "/documents" }, { label: title }]}
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface-card px-4 py-3 text-xs text-fg-subtle">
          {doc && (
            <FormatBadge convertedFrom={doc.converted_from} originalFilename={doc.original_filename} originalSha256={doc.original_sha256} />
          )}
          <span className="inline-flex items-center gap-1">
            <Layers className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            {rawChunks ? `${rawChunks.length} chunk${rawChunks.length === 1 ? "" : "s"}` : "…"}
          </span>
          {rawChunks && <span>· {totalChars.toLocaleString()} total chars</span>}
          {doc && <span>· ingested {formatDateTime(doc.created_at)}</span>}
          <span className="ml-auto">
            <EntityLink entity={{ kind: "document", id }} />
          </span>
        </div>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <ChartCard
            title="Chunk hits by week"
            subtitle="Most cited chunks; select a cell to jump to the chunk"
            className="xl:col-span-2"
            loading={chunks.isLoading && !rawChunks}
            empty={heatData.length === 0}
            emptyMessage={hasHits ? "No chunk of this document was cited yet" : "Chunk hit counts are not available yet"}
            table={{
              columns: [
                { key: "chunk", label: "Chunk" },
                { key: "week", label: "Week of" },
                { key: "hits", label: "Hits", numeric: true },
              ],
              rows: heatData.map((c) => ({ chunk: c.row, week: c.col, hits: c.value })),
            }}
          >
            <CalendarHeatmap
              data={heatData}
              rows={heatChunks.map((c) => `#${c.chunk_index + 1}`)}
              cols={weeks}
              onSelect={(cell) => {
                const match = heatData.find((c) => c.row === cell.row && c.col === cell.col);
                if (match) selectChunk(match.chunkId);
              }}
              label="Chunk hits by week"
            />
          </ChartCard>

          <SectionCard title="Low-rated answers citing this document" description="Thumbs-down answers that used a chunk from here.">
            {lowRated.isLoading ? (
              <SkeletonCard label="Loading low-rated citations" />
            ) : lowRated.error ? (
              <ErrorState compact message={errorMessage(lowRated.error, "Couldn't load low-rated citations.")} onRetry={() => void lowRated.refetch()} />
            ) : lowRatedAbsent ? (
              <EmptyState compact icon={MessageSquareWarning} title="Low-rated citations are not available yet" />
            ) : (lowRated.data?.items.length ?? 0) === 0 ? (
              <EmptyState compact icon={MessageSquareWarning} title="No low-rated answers cite this document" />
            ) : (
              <ul className="max-h-80 space-y-3 overflow-y-auto" aria-label="Low-rated citations">
                {lowRated.data?.items.map((item) => (
                  <li key={`${item.message_id}-${item.chunk_id ?? ""}`} className="rounded-lg border border-line p-3 text-xs">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <ToneChip tone={ratingTone(item.rating)}>{item.rating} / 5</ToneChip>
                      {item.reason_tags.map((r) => (
                        <ToneChip key={r} tone="neutral">
                          {r.replace(/_/g, " ")}
                        </ToneChip>
                      ))}
                      <span className="ml-auto text-[11px] text-fg-subtle">{formatDateTime(item.created_at)}</span>
                    </div>
                    {item.query ? <p className="mt-1.5 line-clamp-2 text-fg">{item.query}</p> : null}
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px]">
                      <EntityLink entity={{ kind: "feedback", id: item.message_id }} mono={false} copyable={false}>
                        Open in Feedback
                      </EntityLink>
                      {item.chunk_id ? (
                        <button
                          type="button"
                          onClick={() => selectChunk(item.chunk_id ?? "")}
                          className={`rounded font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
                        >
                          Chunk {item.chunk_index !== null ? item.chunk_index + 1 : ""}
                        </button>
                      ) : null}
                      {item.turn_index !== null ? (
                        <EntityLink
                          entity={{ kind: "turn", id: String(item.turn_index), conversationId: item.session_id }}
                          mono={false}
                          copyable={false}
                        >
                          View turn
                        </EntityLink>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>

        <section aria-label="Chunks" className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <label className="relative min-w-[240px] flex-1">
              <span className="sr-only">Search chunks</span>
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
              <input
                type="search"
                value={searchInput}
                maxLength={MAX_CHUNK_QUERY_CHARS}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search chunk text or section…"
                className={`w-full rounded-lg border border-line bg-surface-card py-2 pl-9 pr-3 text-xs text-fg placeholder:text-fg-subtle ${focusRing}`}
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-fg-muted">
              Sort
              <select
                value={sort}
                onChange={(e) => set({ sort: e.target.value === "index" ? null : e.target.value })}
                className={`rounded-lg border border-line bg-surface-card px-2.5 py-2 text-xs text-fg ${focusRing}`}
              >
                <option value="index">Document order</option>
                <option value="hits">Most hits</option>
              </select>
            </label>
          </div>

          {chunks.error && !rawChunks ? (
            <ErrorState message={errorMessage(chunks.error, "Couldn't load chunks.")} onRetry={() => void chunks.refetch()} />
          ) : !shown ? (
            <SkeletonCard label="Loading chunks" />
          ) : shown.length === 0 ? (
            <EmptyState title={urlQuery ? "No chunks match this search" : "No chunks"} />
          ) : (
            shown.map((c) => <ChunkCard key={c.id} chunk={c} highlighted={c.id === focusChunk} />)
          )}
        </section>
      </div>
    </div>
  );
}

export default function DocumentChunksPage() {
  return (
    <React.Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading document…</div>}>
      <DocumentDetailContent />
    </React.Suspense>
  );
}
