"use client";

import { ExternalLink, FileText } from "lucide-react";
import type { Citation, QueryResponse, SourceChunk } from "@/lib/types";
import { safeHttpsUrl } from "@/lib/safe-url";
import { focusRing } from "@/components/ui/styles";

/** Chips shown under an answer; more than this collapse into "+N". */
const MAX_CHIPS = 4;
const EXCERPT_CHARS = 160;
/** How long a chip stays highlighted after a source pill jumped to it. */
export const CHIP_HIGHLIGHT_MS = 1600;

export interface SourceChip {
  documentId: string;
  name: string;
  excerpt: string;
  /** Safe https link of the document (`null`: no link, or it failed the guard). */
  url?: string | null;
}

/**
 * One chip per cited document (best-scoring chunk first), named by its source
 * file. With inline `citations`, cited documents come first (in citation
 * order) and their fresh URL wins; history messages without retrieved
 * chunks get one chip per citation.
 */
export function sourceChips(
  sources: readonly SourceChunk[] | undefined,
  citations?: readonly Citation[] | null,
): SourceChip[] {
  const cited = new Map((citations ?? []).map((c) => [c.document_id, c]));
  const seen = new Map<string, SourceChip>();
  for (const source of [...(sources ?? [])].sort((a, b) => b.score - a.score)) {
    if (seen.has(source.document_id)) continue;
    const raw = source.metadata?.source_name;
    const name = typeof raw === "string" && raw.trim() ? raw.trim() : "Knowledge base";
    const text = source.content.replace(/\s+/g, " ").trim();
    const citation = cited.get(source.document_id);
    const url = safeHttpsUrl(citation ? citation.url : source.url);
    seen.set(source.document_id, {
      documentId: source.document_id,
      name,
      excerpt: text.length > EXCERPT_CHARS ? `${text.slice(0, EXCERPT_CHARS)}…` : text,
      ...(url ? { url } : {}),
    });
  }
  for (const c of citations ?? []) {
    if (seen.has(c.document_id)) continue;
    const url = safeHttpsUrl(c.url);
    seen.set(c.document_id, { documentId: c.document_id, name: c.title || c.label, excerpt: "", ...(url ? { url } : {}) });
  }
  const chips = [...seen.values()];
  if (!cited.size) return chips;
  const order = new Map((citations ?? []).map((c, i) => [c.document_id, i]));
  const rank = (chip: SourceChip) => order.get(chip.documentId) ?? Number.MAX_SAFE_INTEGER;
  return chips.sort((a, b) => rank(a) - rank(b));
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

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/**
 * Source pill without a link: scroll to, focus and briefly highlight the
 * matching chip of the same message (`[data-chat-message]`). `false` when the
 * chip is not shown.
 */
export function focusSourceChip(from: Element, documentId: string): boolean {
  const scope: ParentNode = from.closest("[data-chat-message]") ?? document;
  const chip = Array.from(scope.querySelectorAll<HTMLElement>("[data-source-doc]")).find(
    (el) => el.dataset.sourceDoc === documentId,
  );
  if (!chip) return false;
  const target = chip.querySelector<HTMLElement>("a") ?? chip;
  chip.scrollIntoView?.({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  target.focus({ preventScroll: true });
  chip.dataset.highlighted = "true";
  window.setTimeout(() => {
    delete chip.dataset.highlighted;
  }, CHIP_HIGHLIGHT_MS);
  return true;
}

const CHIP =
  "inline-flex max-w-[14rem] items-center gap-1 rounded-full border border-line-brand/70 bg-surface-studio/70 px-2 py-0.5 text-[11px] font-medium text-fg-muted transition-shadow dark:border-brand-500/25 data-[highlighted=true]:ring-2 data-[highlighted=true]:ring-brand-500 motion-reduce:transition-none";

/**
 * Citations for a live answer (pass `citedSources(response)`). Names and excerpts are rendered as React text
 * (escaped), never as HTML, so a crafted document name cannot inject markup. A chip is a link only when the
 * server gave a URL that passes `safeHttpsUrl`.
 */
export function SourceChips({
  sources,
  citations,
}: {
  sources: readonly SourceChunk[] | undefined;
  citations?: readonly Citation[] | null;
}) {
  const chips = sourceChips(sources, citations);
  if (chips.length === 0) return null;
  const shown = chips.slice(0, MAX_CHIPS);
  const hidden = chips.length - shown.length;
  return (
    <ul className="flex flex-wrap items-center gap-1.5" aria-label="Sources" data-tour="chat-sources">
      {shown.map((chip) => (
        <li
          key={chip.documentId}
          data-source-doc={chip.documentId}
          tabIndex={chip.url ? undefined : -1}
          title={chip.excerpt || chip.name}
          className={`${CHIP} focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500`}
        >
          {chip.url ? (
            <a
              href={chip.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${chip.name}, opens in a new tab`}
              className={`inline-flex min-w-0 items-center gap-1 rounded-full hover:text-fg ${focusRing}`}
            >
              <FileText className="h-3 w-3 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={2} aria-hidden="true" />
              <span className="truncate">{chip.name}</span>
              <ExternalLink className="h-3 w-3 shrink-0 text-fg-subtle" strokeWidth={2} aria-hidden="true" />
            </a>
          ) : (
            <>
              <FileText className="h-3 w-3 shrink-0 text-brand-600 dark:text-brand-300" strokeWidth={2} aria-hidden="true" />
              <span className="truncate">{chip.name}</span>
            </>
          )}
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
