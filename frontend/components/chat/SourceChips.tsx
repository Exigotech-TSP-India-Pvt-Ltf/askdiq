"use client";

import { FileText } from "lucide-react";
import type { QueryResponse, SourceChunk } from "@/lib/types";

/** Chips shown under an answer; more than this collapse into "+N". */
const MAX_CHIPS = 4;
const EXCERPT_CHARS = 160;

export interface SourceChip {
  documentId: string;
  name: string;
  excerpt: string;
}

/** One chip per cited document (best-scoring chunk first), named by its source file. */
export function sourceChips(sources: readonly SourceChunk[] | undefined): SourceChip[] {
  if (!sources?.length) return [];
  const seen = new Map<string, SourceChip>();
  for (const source of [...sources].sort((a, b) => b.score - a.score)) {
    if (seen.has(source.document_id)) continue;
    const raw = source.metadata?.source_name;
    const name = typeof raw === "string" && raw.trim() ? raw.trim() : "Knowledge base";
    const text = source.content.replace(/\s+/g, " ").trim();
    seen.set(source.document_id, {
      documentId: source.document_id,
      name,
      excerpt: text.length > EXCERPT_CHARS ? `${text.slice(0, EXCERPT_CHARS)}…` : text,
    });
  }
  return [...seen.values()];
}

/**
 * Sources are citations only for a grounded, answered reply. A fallback /
 * no-answer reply (ungrounded or low-confidence, llm_failed, blocked,
 * conversational) still carries the retrieved chunks, but they did not back
 * the text shown, so no chips are rendered for it.
 */
export function citedSources(response: QueryResponse | undefined): SourceChunk[] {
  if (!response?.grounded) return [];
  if ((response.outcome ?? "answered") !== "answered") return [];
  return response.sources ?? [];
}

/**
 * Citations for a live answer (pass `citedSources(response)`). Names and excerpts are rendered as React text
 * (escaped), never as HTML, so a crafted document name cannot inject markup.
 */
export function SourceChips({ sources }: { sources: readonly SourceChunk[] | undefined }) {
  const chips = sourceChips(sources);
  if (chips.length === 0) return null;
  const shown = chips.slice(0, MAX_CHIPS);
  const hidden = chips.length - shown.length;
  return (
    <ul className="flex flex-wrap items-center gap-1.5" aria-label="Sources">
      {shown.map((chip) => (
        <li
          key={chip.documentId}
          title={chip.excerpt}
          className="inline-flex max-w-[14rem] items-center gap-1 rounded-full border border-line-brand/70 bg-surface-studio/70 px-2 py-0.5 text-[11px] font-medium text-fg-muted dark:border-brand-500/25"
        >
          <FileText className="h-3 w-3 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={2} aria-hidden="true" />
          <span className="truncate">{chip.name}</span>
        </li>
      ))}
      {hidden > 0 ? (
        <li className="text-[11px] text-fg-subtle" title={chips.slice(MAX_CHIPS).map((c) => c.name).join(", ")}>
          +{hidden} more
        </li>
      ) : null}
    </ul>
  );
}
