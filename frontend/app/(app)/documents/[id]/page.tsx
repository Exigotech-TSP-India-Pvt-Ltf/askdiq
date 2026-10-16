"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { AnimatePresence, m } from "motion/react";
import {
  AlertTriangle,
  ChevronDown,
  Code2,
  Download,
  Eye,
  FileText,
  Flame,
  Hash,
  History,
  Layers,
  MessageSquareWarning,
  Search,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { FormatBadge } from "@/components/documents/FormatBadge";
import { MarkdownView } from "@/components/documents/MarkdownView";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { ChartCard } from "@/components/charts/ChartCard";
import { CalendarHeatmap, type HeatCell } from "@/components/charts/CalendarHeatmap";
import { CopyButton } from "@/components/ui/CopyButton";
import { EntityLink } from "@/components/ui/EntityLink";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { focusRing, secondaryButton } from "@/components/ui/styles";
import { SectionCard, ToneChip, errorMessage, formatDateTime, formatDay, ratingTone } from "@/components/admin/entity/shared";
import { useApiQuery } from "@/lib/query-cache";
import { useDeepLink } from "@/lib/use-deep-link";
import { crossfade, fadeUp, stagger, useMotionVariants, usePrefersReducedMotion } from "@/lib/motion";
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
import {
  documentStatus,
  formatBytes,
  getCanonicalMarkdown,
  getDocumentDetail,
  ingestedAt,
  isOneOf,
  isStandardized,
  listDocumentInventory,
  originalFormat,
  shortHash,
  strategyLabel,
  type DocumentDetail,
  type DocumentInventoryItem,
} from "@/lib/documents-api";

const PREVIEW_CHAR_LIMIT = 900;
const HEATMAP_CHUNKS = 15;

const TABS = ["chunks", "markdown", "citations", "history"] as const;
type Tab = (typeof TABS)[number];

/** Chunk API items may carry fields beyond the contract type (word count, hash). */
type ChunkRow = DocumentChunk & { word_count?: number; content_hash?: string | null; base_strategy?: string | null };

function ChunkCard({ chunk, highlighted }: { chunk: ChunkRow; highlighted: boolean }) {
  const [expanded, setExpanded] = useState(highlighted);
  const variants = useMotionVariants(fadeUp);
  useEffect(() => {
    if (highlighted) setExpanded(true);
  }, [highlighted]);
  const isLong = chunk.content.length > PREVIEW_CHAR_LIMIT;
  const shown = !isLong || expanded ? chunk.content : `${chunk.content.slice(0, PREVIEW_CHAR_LIMIT)}…`;

  return (
    <m.article
      variants={variants}
      id={`chunk-${chunk.id}`}
      tabIndex={-1}
      aria-label={`Chunk ${chunk.chunk_index + 1}`}
      data-highlighted={highlighted || undefined}
      className={`overflow-hidden rounded-xl border bg-surface-card shadow-sm transition duration-150 focus:outline-none motion-reduce:transition-none ${
        highlighted ? "border-brand-500 ring-2 ring-brand-500/40" : "border-line hover:border-brand-300 hover:shadow-md dark:hover:border-brand-700/60"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface-muted/50 px-4 py-2.5">
        <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700 dark:bg-brand-950/40 dark:text-brand-300">
          <Hash className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> Chunk {chunk.chunk_index + 1}
        </span>
        <span className="text-[11px] text-fg-subtle">
          {chunk.char_count.toLocaleString()} chars
          {typeof chunk.word_count === "number" ? ` · ${chunk.word_count.toLocaleString()} words` : ""}
        </span>
        <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-fg-muted">
          {strategyLabel(chunk.base_strategy ?? chunk.strategy)}
        </span>
        {typeof chunk.hits === "number" ? (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-fg-muted" title="Answers that cited this chunk (last 8 weeks)">
            <Flame className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" /> {chunk.hits.toLocaleString()} hits
          </span>
        ) : null}
        {chunk.low_rated_hits ? <ToneChip tone="danger">{chunk.low_rated_hits} low-rated</ToneChip> : null}
        {chunk.last_hit_at ? <span className="text-[11px] text-fg-subtle">last cited {formatDateTime(chunk.last_hit_at)}</span> : null}
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-fg-subtle">
          <code className="font-mono" title={chunk.id}>
            {chunk.id.slice(0, 8)}
          </code>
          <CopyButton value={chunk.id} label={`Copy id of chunk ${chunk.chunk_index + 1}`} />
        </span>
      </div>
      {chunk.section_heading ? (
        <p className="border-b border-line px-4 py-1.5 text-[11px] text-fg-subtle">
          Section: <span className="font-medium text-fg-muted">{chunk.section_heading}</span>
        </p>
      ) : null}

      <div className="px-4 py-4">
        <MarkdownView source={shown} />
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className={`mt-3 inline-flex items-center gap-1 rounded text-xs font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
          >
            {expanded ? "Show less" : "Show full chunk"}
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </button>
        )}
      </div>
    </m.article>
  );
}

function Field({ label, children, mono = false }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</dt>
      <dd className={`mt-0.5 break-words text-xs text-fg ${mono ? "font-mono" : ""}`}>{children}</dd>
    </div>
  );
}

function HashValue({ value, label }: { value: string | null | undefined; label: string }) {
  if (!value) return <>—</>;
  return (
    <span className="inline-flex items-center gap-1">
      <code className="font-mono text-[11px]" title={value}>
        {shortHash(value)}…
      </code>
      <CopyButton value={value} label={`Copy ${label}`} />
    </span>
  );
}

const STATUS_TONE = { active: "ok", quarantined: "danger", empty: "warn" } as const;
const STATUS_LABEL = { active: "Active", quarantined: "Quarantined", empty: "No chunks" } as const;

function MetadataCard({ doc, detail }: { doc: DocumentInventoryItem; detail: DocumentDetail | null }) {
  const status = documentStatus(doc);
  const stats = detail?.chunk_stats;
  const labels = Object.entries(doc.labels ?? {});
  return (
    <section aria-label="Document metadata" className="rounded-xl border border-line bg-surface-card p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <FormatBadge
          convertedFrom={originalFormat(doc)}
          originalFilename={doc.original_filename}
          originalSha256={doc.original_sha256}
          converter={doc.converter}
        />
        <ToneChip tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</ToneChip>
        {doc.pii_redacted?.length ? <ToneChip tone="neutral">PII redacted: {doc.pii_redacted.join(", ")}</ToneChip> : null}
        <span className="ml-auto">
          <EntityLink entity={{ kind: "document", id: doc.document_id }} />
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Original file">{doc.original_filename ?? doc.source_name}</Field>
        <Field label="Original format">{originalFormat(doc) === "docx" ? "Word (.docx)" : "Markdown (.md)"}</Field>
        <Field label="Canonical format">Markdown (.md){isStandardized(doc) ? " · standardized" : ""}</Field>
        <Field label="Converter">
          {doc.converter ? `${doc.converter}${doc.converter_version ? ` ${doc.converter_version}` : ""}` : doc.converter_version ?? "—"}
        </Field>
        <Field label="Original size">{formatBytes(doc.original_size_bytes)}</Field>
        <Field label="Canonical size">{formatBytes(doc.canonical_size_bytes)}</Field>
        <Field label="Content SHA-256">
          <HashValue value={doc.content_sha256 ?? doc.content_hash} label="content hash" />
        </Field>
        <Field label="Original SHA-256">
          <HashValue value={doc.original_sha256} label="original file hash" />
        </Field>
        <Field label="Chunks">
          {doc.chunk_count.toLocaleString()} · {strategyLabel(doc.chunking_strategy ?? doc.chunk_strategies?.[0])}
        </Field>
        <Field label="Chunk size (chars)">
          {stats && stats.count
            ? `${stats.min_chars ?? "—"} min · ${Math.round(stats.avg_chars ?? 0)} avg · ${stats.max_chars ?? "—"} max`
            : typeof doc.total_chunk_chars === "number"
              ? `${doc.total_chunk_chars.toLocaleString()} total`
              : "—"}
        </Field>
        <Field label="Embedding">
          {doc.embedding_model ? `${doc.embedding_model}${doc.embedding_dim ? ` · ${doc.embedding_dim} dims` : ""}` : "—"}
        </Field>
        <Field label="Pipeline">{doc.pipeline_version ?? "—"}</Field>
        <Field label="Last ingested">{formatDateTime(ingestedAt(doc))}</Field>
        <Field label="Ingested by">{doc.ingested_by ?? "—"}</Field>
        <Field label="First added">{formatDateTime(doc.created_at)}</Field>
        <Field label="Ingests">{(doc.ingest_count ?? 1).toLocaleString()}</Field>
        {labels.length ? (
          <Field label="Labels">
            {labels.map(([k, v]) => (
              <span key={k} className="mr-2 inline-block">
                {k}: <span className="text-fg-muted">{v}</span>
              </span>
            ))}
          </Field>
        ) : null}
      </dl>
      {doc.warnings?.length ? (
        <div className="mt-4 rounded-lg border border-status-warn/40 bg-status-warn-bg p-3 text-xs text-status-warn-fg">
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            {doc.warnings.length} conversion / health warning{doc.warnings.length === 1 ? "" : "s"}
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {doc.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function downloadMarkdown(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name.toLowerCase().endsWith(".md") ? name : `${name}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

function CanonicalTab({ token, id, sourceName }: { token: string; id: string; sourceName: string }) {
  const [view, setView] = useState<"rendered" | "source">("rendered");
  const variants = useMotionVariants(crossfade);
  const canonical = useApiQuery(["document", id, "canonical"], (signal) => getCanonicalMarkdown(token, id, signal));
  const data = canonical.data;

  if (canonical.isLoading) return <SkeletonCard label="Loading canonical Markdown" />;
  if (canonical.error) {
    return (
      <ErrorState message={errorMessage(canonical.error, "Couldn't load the canonical Markdown.")} onRetry={() => void canonical.refetch()} />
    );
  }
  if (!data || !data.available || data.content === null) {
    return (
      <EmptyState
        icon={FileText}
        title="The canonical Markdown was not stored for this document"
        description="It was ingested before the standardized text was kept (only its chunks were). Upload the original .md or .docx again to store it; unchanged content is not re-embedded. The Chunks tab shows the indexed text."
      />
    );
  }
  const content = data.content;
  return (
    <SectionCard
      title="Canonical Markdown"
      description={`The standardized text every chunk was cut from · ${formatBytes(data.size_bytes)}`}
      actions={
        <>
          <div role="group" aria-label="Markdown view" className="inline-flex rounded-lg border border-line p-0.5">
            {(["rendered", "source"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${focusRing} ${
                  view === v ? "bg-brand-600 text-white" : "text-fg-muted hover:text-fg"
                }`}
              >
                {v === "rendered" ? (
                  <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <Code2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                )}
                {v === "rendered" ? "Rendered" : "Source"}
              </button>
            ))}
          </div>
          <CopyButton value={content} label="Copy canonical Markdown" />
          <button type="button" onClick={() => downloadMarkdown(sourceName, content)} className={secondaryButton}>
            <Download className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            .md
          </button>
        </>
      }
    >
      <AnimatePresence mode="wait" initial={false}>
        <m.div key={view} variants={variants} initial="hidden" animate="visible" exit="exit">
          {view === "rendered" ? (
            <MarkdownView source={content} className="max-w-3xl" />
          ) : (
            <pre
              aria-label="Canonical Markdown source"
              className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-surface-muted p-4 font-mono text-xs text-fg"
            >
              {content}
            </pre>
          )}
        </m.div>
      </AnimatePresence>
    </SectionCard>
  );
}

function HistoryTab({ detail }: { detail: DocumentDetail | null }) {
  if (!detail) {
    return <EmptyState icon={History} title="Ingest history is not available yet" />;
  }
  if (!detail.history.length) {
    return <EmptyState icon={History} title="No ingest events were recorded for this document" />;
  }
  return (
    <SectionCard
      title="Ingest history"
      description={`Every upload of this document, newest first${detail.history_truncated ? " (latest 50)" : ""}.`}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-line text-fg-subtle">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">When</th>
              <th scope="col" className="px-3 py-2 font-semibold">By</th>
              <th scope="col" className="px-3 py-2 font-semibold">Result</th>
              <th scope="col" className="px-3 py-2 font-semibold">From</th>
              <th scope="col" className="px-3 py-2 text-right font-semibold">Chunks</th>
              <th scope="col" className="px-3 py-2 font-semibold">Hash</th>
              <th scope="col" className="px-3 py-2 font-semibold">Warnings</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line text-fg-muted">
            {detail.history.map((ev, i) => (
              <tr key={`${ev.at}-${i}`}>
                <td className="whitespace-nowrap px-3 py-2">{formatDateTime(ev.at)}</td>
                <td className="max-w-[200px] truncate px-3 py-2" title={ev.actor}>
                  {ev.actor}
                </td>
                <td className="px-3 py-2">{ev.status ?? "—"}</td>
                <td className="px-3 py-2">{ev.original_filename ?? ev.converted_from ?? "—"}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  {ev.chunks_added !== null
                    ? `+${ev.chunks_added} / ${ev.chunks_kept ?? 0} / −${ev.chunks_removed ?? 0}`
                    : ev.total_chunks !== null
                      ? ev.total_chunks.toLocaleString()
                      : "—"}
                </td>
                <td className="px-3 py-2 font-mono text-[11px]">{ev.content_hash ?? "—"}</td>
                <td className="px-3 py-2">{ev.warnings.length || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

function DocumentDetailContent() {
  const { token } = useAuth();
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params?.id ?? "");
  const reducedMotion = usePrefersReducedMotion();
  const listVariants = useMotionVariants(stagger);
  const { get, set } = useDeepLink();

  const urlQuery = get("q") ?? "";
  const sortRaw = get("sort");
  const sort: ChunkSort = isChunkSort(sortRaw) ? sortRaw : "index";
  const focusChunk = get("chunk");
  const tabRaw = get("tab");
  const tab: Tab = focusChunk ? "chunks" : isOneOf(TABS, tabRaw) ? tabRaw : "chunks";

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

  const detailQ = useApiQuery(token && id ? ["document", id, "detail"] : null, async (signal) => {
    try {
      return await getDocumentDetail(token ?? "", id, signal);
    } catch {
      return null; // fall back to the inventory row
    }
  });
  const docs = useApiQuery(token ? ["documents", "inventory"] : null, (signal) => listDocumentInventory(token ?? "", signal));
  const listMatch = useMemo(() => docs.data?.find((d) => d.document_id === id) ?? null, [docs.data, id]);
  const detail = detailQ.data ?? null;
  const doc: DocumentInventoryItem | null = detail ?? listMatch;
  const notFound = !doc && !detailQ.isLoading && !docs.isLoading && (docs.data !== undefined || Boolean(docs.error));

  const chunks = useApiQuery(token && id ? ["document", id, "chunks", urlQuery, sort] : null, (signal) =>
    getDocumentChunks(token ?? "", id, { q: urlQuery, sort }, signal),
  );
  // Keep the previous result on screen while a new search loads (no flash).
  const lastChunks = useRef<ChunkRow[] | null>(null);
  if (chunks.data) lastChunks.current = chunks.data;
  const rawChunks = chunks.data ?? lastChunks.current;
  const shown = useMemo(() => (rawChunks ? (applyChunkQuery(rawChunks, urlQuery, sort) as ChunkRow[]) : null), [rawChunks, urlQuery, sort]);

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
  const selectChunk = useCallback((chunkId: string) => set({ chunk: chunkId, tab: null }), [set]);
  const title = doc?.source_name ?? (notFound ? "Document not found" : "Document");
  const lowRatedTotal = lowRated.data?.total ?? null;

  const tabs: TabItem<Tab>[] = [
    { id: "chunks", label: "Chunks", icon: Layers, badgeCount: rawChunks ? rawChunks.length : null },
    { id: "markdown", label: "Canonical Markdown", icon: FileText },
    { id: "citations", label: "Citations", icon: MessageSquareWarning, badgeCount: lowRatedTotal },
    { id: "history", label: "History", icon: History, badgeCount: detail ? detail.history.length : null },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title={title}
        icon={FileText}
        showAudienceTabs={false}
        breadcrumb={[{ label: "Documents", href: "/documents" }, { label: title }]}
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        {notFound ? (
          <EmptyState icon={FileText} title="This document does not exist" description="It may have been deleted. Go back to Documents to see the inventory." />
        ) : doc ? (
          <MetadataCard doc={doc} detail={detail} />
        ) : (
          <SkeletonCard label="Loading document metadata" />
        )}

        {notFound ? null : (
          <>
            <TabGroup<Tab> tabs={tabs} activeTab={tab} onChange={(next) => set({ tab: next === "chunks" ? null : next, chunk: null })} ariaLabel="Document sections" />

            {tab === "chunks" ? (
              <div className="space-y-6">
                <ChartCard
                  title="Chunk hits by week"
                  subtitle="Most cited chunks over the last 8 weeks; select a cell to jump to the chunk"
                  loading={chunks.isLoading && !rawChunks}
                  empty={heatData.length === 0}
                  emptyMessage={hasHits ? "No chunk of this document was cited in the last 8 weeks" : "Chunk hit counts are not available yet"}
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

                <section aria-label="Chunks" className="space-y-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="relative min-w-[240px] flex-1">
                      <span className="sr-only">Search chunks</span>
                      <Search
                        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle"
                        strokeWidth={1.75}
                        aria-hidden="true"
                      />
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
                    {rawChunks ? (
                      <span className="text-[11px] text-fg-subtle">
                        {shown?.length ?? 0} of {rawChunks.length} chunk{rawChunks.length === 1 ? "" : "s"} · {totalChars.toLocaleString()} chars
                      </span>
                    ) : null}
                  </div>

                  {chunks.error && !rawChunks ? (
                    <ErrorState message={errorMessage(chunks.error, "Couldn't load chunks.")} onRetry={() => void chunks.refetch()} />
                  ) : !shown ? (
                    <SkeletonCard label="Loading chunks" />
                  ) : shown.length === 0 ? (
                    <EmptyState title={urlQuery ? "No chunks match this search" : "This document has no chunks"} />
                  ) : (
                    <m.div variants={listVariants} initial="hidden" animate="visible" className="space-y-4">
                      {shown.map((c) => (
                        <ChunkCard key={c.id} chunk={c} highlighted={c.id === focusChunk} />
                      ))}
                    </m.div>
                  )}
                </section>
              </div>
            ) : null}

            {tab === "markdown" && token ? <CanonicalTab token={token} id={id} sourceName={doc?.source_name ?? "document.md"} /> : null}

            {tab === "citations" ? (
              <SectionCard
                title="Low-rated answers citing this document"
                description="Answers rated 2 or lower that used a chunk from this document. Open one in Feedback to triage it."
              >
                {lowRated.isLoading ? (
                  <SkeletonCard label="Loading low-rated citations" />
                ) : lowRated.error ? (
                  <ErrorState
                    compact
                    message={errorMessage(lowRated.error, "Couldn't load low-rated citations.")}
                    onRetry={() => void lowRated.refetch()}
                  />
                ) : lowRatedAbsent ? (
                  <EmptyState compact icon={MessageSquareWarning} title="Low-rated citations are not available yet" />
                ) : (lowRated.data?.items.length ?? 0) === 0 ? (
                  <EmptyState compact icon={MessageSquareWarning} title="No low-rated answers cite this document" />
                ) : (
                  <ul className="space-y-3" aria-label="Low-rated citations">
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
                        {item.comment ? <p className="mt-1 text-fg-muted">“{item.comment}”</p> : null}
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
                {lowRated.data && lowRated.data.total > lowRated.data.items.length ? (
                  <p className="mt-3 text-[11px] text-fg-subtle">
                    Showing the latest {lowRated.data.items.length} of {lowRated.data.total}.
                  </p>
                ) : null}
              </SectionCard>
            ) : null}

            {tab === "history" ? <HistoryTab detail={detail} /> : null}
          </>
        )}
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
