import type { ReactNode } from "react";
import type { Citation } from "@/lib/types";
import { SourcePills } from "@/components/chat/SourcePill";
import { CITATION_MARKER, stripCitationMarkers } from "@/lib/citation-markers";

// Lightweight formatter (no markdown dependency): renders **bold** spans,
// "- "/"• " bullet lines, "#"-style headings, and auto-detects loosely
// structured "Label:" + one-point-per-line answers (common from the RAG
// pipeline) as bullet lists even when the model didn't add "-" markers.
//
// With `citations` (OR-7), each run of `[n]` markers ("[1]", "[1][3]",
// "[1] [3]") becomes grey source pills. Links come only from the server's
// citation list (never from the answer text). Without citations the text
// renders exactly as before: markers are not parsed at all.

export interface FormatTextOptions {
  /** Server citation list; `[n]` maps to the citation with that `n`. */
  citations?: readonly Citation[] | null;
}

type CitationIndex = ReadonlyMap<number, Citation>;

function citationIndex(citations: readonly Citation[] | null | undefined): CitationIndex | null {
  return citations?.length ? new Map(citations.map((c) => [c.n, c])) : null;
}

/** Text with every marker run replaced by its pills (unknown numbers are dropped). */
function renderCited(text: string, keyPrefix: string, index: CitationIndex): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let run: Citation[] | null = null;
  let runs = 0;
  const flush = () => {
    if (run?.length) out.push(<SourcePills key={`${keyPrefix}-src-${runs}`} citations={run} />);
    runs += 1;
    run = null;
  };
  for (const match of text.matchAll(CITATION_MARKER)) {
    const start = match.index ?? 0;
    const gap = text.slice(last, start);
    // Markers separated only by spaces belong to the same run.
    if (run === null || gap.trim() !== "") {
      flush();
      if (gap) out.push(gap);
      run = [];
    }
    const citation = index.get(Number(match[1]));
    const current: Citation[] = run ?? [];
    if (citation && !current.some((c) => c.n === citation.n)) current.push(citation);
    run = current;
    last = start + match[0].length;
  }
  flush();
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderInline(text: string, keyPrefix: string, index: CitationIndex | null): ReactNode[] {
  const body = (part: string, key: string): ReactNode => (index ? renderCited(part, key, index) : part);
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter((part) => part.length > 0)
    .map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={`${keyPrefix}-${i}`}>{body(part.slice(2, -2), `${keyPrefix}-${i}`)}</strong>
      ) : (
        <span key={`${keyPrefix}-${i}`}>{body(part, `${keyPrefix}-${i}`)}</span>
      )
    );
}

// A short line ending in ":" (e.g. "Key points:") is treated as a label that
// introduces a run of one-point-per-line statements below it.
function isLabelLine(trimmed: string): boolean {
  return trimmed.endsWith(":") && trimmed.length <= 80;
}

/**
 * Inline renderer for a span of answer text: `[n]` runs become source pills
 * when `citations` are given, otherwise the markers are stripped (stored
 * answers in admin views, or a payload without citations). Matches the
 * `renderInline(text, key)` hook of the Markdown renderer (PR-E).
 */
export function createCitationInlineRenderer(
  citations: readonly Citation[] | null | undefined,
): (text: string, key: string) => ReactNode {
  const index = citationIndex(citations);
  function renderCitationInline(text: string, key: string): ReactNode {
    return index ? <span key={key}>{renderCited(text, key, index)}</span> : stripCitationMarkers(text);
  }
  return renderCitationInline;
}

/** One stored answer as plain text with pills (with citations) or without markers. */
export function renderCitedText(text: string, citations?: readonly Citation[] | null): ReactNode {
  return createCitationInlineRenderer(citations)(text, "cited");
}

export function renderFormattedText(content: string, options: FormatTextOptions = {}): ReactNode {
  const index = citationIndex(options.citations);
  const blocks: ReactNode[] = [];
  let currentList: string[] = [];
  let autoListMode = false;

  const flushList = (key: string) => {
    if (currentList.length === 0) return;
    blocks.push(
      <ul key={key} className="list-disc space-y-1.5 pl-4">
        {currentList.map((item, i) => (
          <li key={i}>{renderInline(item, `${key}-li-${i}`, index)}</li>
        ))}
      </ul>
    );
    currentList = [];
  };

  const lines = content.split("\n");

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    const bulletMatch = trimmed.match(/^[-•]\s+(.*)/);

    if (bulletMatch) {
      autoListMode = false;
      currentList.push(bulletMatch[1]);
      return;
    }

    if (trimmed === "") {
      flushList(`list-${idx}`);
      autoListMode = false;
      return;
    }

    const headingMatch = trimmed.match(/^(#{1,3})\s+(.*)/);
    if (headingMatch) {
      flushList(`list-${idx}`);
      autoListMode = false;
      const level = headingMatch[1].length;
      const sizeClass = level === 1 ? "text-base font-semibold" : level === 2 ? "text-sm font-semibold" : "text-sm font-medium";
      blocks.push(
        <p key={`h-${idx}`} className={`${sizeClass} text-ink`}>
          {renderInline(headingMatch[2], `h-${idx}`, index)}
        </p>
      );
      return;
    }

    if (isLabelLine(trimmed)) {
      flushList(`list-${idx}`);
      blocks.push(
        <p key={`label-${idx}`} className="font-semibold text-ink">
          {renderInline(trimmed, `label-${idx}`, index)}
        </p>
      );
      autoListMode = true;
      return;
    }

    if (autoListMode) {
      currentList.push(trimmed);
      return;
    }

    blocks.push(
      <p key={`p-${idx}`} className="leading-relaxed">
        {renderInline(trimmed, `p-${idx}`, index)}
      </p>
    );
  });

  flushList("list-end");

  return <div className="space-y-2">{blocks}</div>;
}
