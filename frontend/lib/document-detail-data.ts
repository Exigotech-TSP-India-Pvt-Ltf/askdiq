"use client";

/**
 * Query keys and fetchers of the document detail page, shared with the
 * Knowledge list so a row hover (or idle time) warms the page a click opens.
 *
 * Keys live under `documents/` so the console live stream refreshes them with
 * the rest of the Knowledge data. Chunks are fetched once, unfiltered: the
 * search box filters them in the browser (`applyChunkQuery`, same match as the
 * server), so typing never waits for a request.
 */
import { getDocumentDetail, getCanonicalMarkdown } from "@/lib/documents-api";
import { getDocumentChunks, getDocumentCitationStats, getLowRatedCitations } from "@/lib/entity-stats-api";
import { prefetch } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange, rangeUtcDays } from "@/lib/range-context";

export const TOP_CITED_LIMIT = 10;

export const documentKeys = {
  detail: (id: string) => ["documents", "detail", id] as const,
  chunks: (id: string) => ["documents", "chunks", id] as const,
  lowRated: (id: string) => ["documents", "low-rated", id] as const,
  canonical: (id: string) => ["document", id, "canonical"] as const,
  citations: (from: string | undefined, to: string | undefined) => ["documents", "stats", "citations", from, to] as const,
};

export const documentFetchers = {
  /** `null` when the detail endpoint fails: the page falls back to the inventory row. */
  detail: (token: string, id: string) => async (signal: AbortSignal) => {
    try {
      return await getDocumentDetail(token, id, signal);
    } catch {
      return null;
    }
  },
  chunks: (token: string, id: string) => (signal: AbortSignal) => getDocumentChunks(token, id, {}, signal),
  lowRated: (token: string, id: string) => (signal: AbortSignal) => getLowRatedCitations(token, id, signal),
  canonical: (token: string, id: string) => (signal: AbortSignal) => getCanonicalMarkdown(token, id, signal),
  citations: (token: string, from: string | undefined, to: string | undefined) => (signal: AbortSignal) =>
    getDocumentCitationStats(token, { from, to, limit: TOP_CITED_LIMIT }, signal),
};

/** Warms everything the detail page shows first (metadata, chunks, citations). */
export function prefetchDocument(token: string | null, id: string): void {
  if (!token) return;
  void prefetch(documentKeys.detail(id), documentFetchers.detail(token, id));
  void prefetch(documentKeys.chunks(id), documentFetchers.chunks(token, id));
  void prefetch(documentKeys.lowRated(id), documentFetchers.lowRated(token, id));
}

/** Warms the citation charts of every range preset (precomputed server side). */
export function prefetchCitationPresets(token: string | null): void {
  if (!token) return;
  for (const preset of RANGE_PRESETS) {
    const { from, to } = rangeUtcDays(presetRange(preset));
    void prefetch(documentKeys.citations(from, to), documentFetchers.citations(token, from, to));
  }
}
