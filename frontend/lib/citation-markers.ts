/**
 * Inline citation markers (OR-7): `[n]` right after a claim, where `n` is the
 * server citation number. One definition shared by the chat renderer
 * (`lib/format-text.tsx`) and every admin view that shows stored answers.
 *
 * Linear-time: a fixed-width pattern with no nested or overlapping
 * quantifiers (CodeQL polynomial-ReDoS, see 6bbc919).
 */

/** Global, so always use it with `matchAll` / `replace` (never `test`/`exec` state). */
export const CITATION_MARKER = /\[(\d{1,2})\]/g;

/** Trailing spaces/tabs only (an explicit loop: no regex backtracking). */
function trimTrailingBlanks(text: string): string {
  let end = text.length;
  while (end > 0 && (text[end - 1] === " " || text[end - 1] === "\t")) end -= 1;
  return text.slice(0, end);
}

/**
 * `text` without citation markers and the blanks right before them
 * ("Claim [1]." -> "Claim."). For excerpts, search, copy and any view that
 * has no citation list to render pills from.
 */
export function stripCitationMarkers(text: string): string;
export function stripCitationMarkers(text: string | null): string | null;
export function stripCitationMarkers(text: string | null | undefined): string | null | undefined;
export function stripCitationMarkers(text: string | null | undefined): string | null | undefined {
  if (!text || !text.includes("[")) return text;
  let out = "";
  let last = 0;
  for (const match of text.matchAll(CITATION_MARKER)) {
    const start = match.index ?? 0;
    out += trimTrailingBlanks(text.slice(last, start));
    last = start + match[0].length;
  }
  return out + text.slice(last);
}
