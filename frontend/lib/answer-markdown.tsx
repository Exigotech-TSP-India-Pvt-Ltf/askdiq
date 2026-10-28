import type { ReactNode } from "react";

/**
 * Safe Markdown renderer for chat answers (OR-8 "Adaptive structure").
 *
 * - No dependency and no raw-HTML path: every node is a React element or a
 *   text node, so `<script>` or `<img onerror>` in an answer shows as text.
 * - No links: a model-written `[x](javascript:...)` or URL stays plain text;
 *   sources reach the reader only through the citation mechanism.
 * - Linear time: a line-based block parser (`startsWith` / `split`, no
 *   nested-quantifier regex).
 *
 * Blocks: `##`/`###` headings, `-`/`*`/`•` bullets and `1.` steps (one
 * nesting level), GFM pipe tables (a header row plus a `| --- |` separator;
 * without one the lines stay paragraphs) and paragraphs. Inline text goes
 * through `renderInline`, so a caller can swap in a renderer that also turns
 * citation markers into source pills; its nodes are placed unchanged in
 * paragraphs, list items, headings and table cells.
 */

export type InlineRenderer = (text: string, key: string) => ReactNode;

export interface ListItem {
  text: string;
  children?: { ordered: boolean; items: string[] };
}

export type AnswerBlock =
  | { type: "heading"; level: 2 | 3; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; start: number; items: ListItem[] }
  | { type: "table"; header: string[]; rows: string[][] };

const BULLETS = ["- ", "* ", "• ", "+ "];
const MAX_HEADING = 6;
const MAX_ORDINAL_DIGITS = 9;
const NEST_INDENT = 2;

function indentOf(line: string): number {
  let n = 0;
  for (const ch of line) {
    if (ch === " ") n += 1;
    else if (ch === "\t") n += NEST_INDENT;
    else break;
  }
  return n;
}

function heading(trimmed: string): { level: 2 | 3; text: string } | null {
  let hashes = 0;
  while (hashes < trimmed.length && trimmed[hashes] === "#") hashes += 1;
  if (hashes === 0 || hashes > MAX_HEADING || trimmed[hashes] !== " ") return null;
  const text = trimmed.slice(hashes + 1).trim();
  if (!text) return null;
  // The page owns the h1; answer headings start at h2.
  return { level: hashes <= 2 ? 2 : 3, text };
}

interface ListMarker {
  ordered: boolean;
  ordinal: number;
  text: string;
}

function listMarker(trimmed: string): ListMarker | null {
  for (const bullet of BULLETS) {
    if (trimmed.startsWith(bullet)) {
      return { ordered: false, ordinal: 1, text: trimmed.slice(bullet.length).trim() };
    }
  }
  let digits = 0;
  while (digits < trimmed.length && trimmed[digits] >= "0" && trimmed[digits] <= "9") digits += 1;
  if (digits === 0 || digits > MAX_ORDINAL_DIGITS) return null;
  const mark = trimmed.slice(digits, digits + 2);
  if (mark !== ". " && mark !== ") ") return null;
  return { ordered: true, ordinal: Number(trimmed.slice(0, digits)), text: trimmed.slice(digits + 2).trim() };
}

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < row.length; i += 1) {
    const ch = row[i];
    if (ch === "\\" && row[i + 1] === "|") {
      current += "|";
      i += 1;
    } else if (ch === "|") {
      cells.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  cells.push(current.trim());
  return cells;
}

function isSeparatorCell(cell: string): boolean {
  let body = cell;
  if (body.startsWith(":")) body = body.slice(1);
  if (body.endsWith(":")) body = body.slice(0, -1);
  if (body.length === 0) return false;
  for (const ch of body) if (ch !== "-") return false;
  return true;
}

function isSeparator(line: string | undefined, columns: number): boolean {
  if (line === undefined || !line.includes("-")) return false;
  const cells = splitRow(line);
  return cells.length === columns && cells.every(isSeparatorCell);
}

function fit(cells: string[], columns: number): string[] {
  const out = cells.slice(0, columns);
  while (out.length < columns) out.push("");
  return out;
}

/** Block-level parse (pure; exported for tests). */
export function parseAnswer(content: string): AnswerBlock[] {
  const lines = content.split("\n").map((line) => (line.endsWith("\r") ? line.slice(0, -1) : line));
  const blocks: AnswerBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      i += 1;
      continue;
    }

    const head = heading(trimmed);
    if (head) {
      blocks.push({ type: "heading", ...head });
      i += 1;
      continue;
    }

    if (trimmed.includes("|")) {
      const header = splitRow(trimmed);
      if (header.length >= 2 && isSeparator(lines[i + 1], header.length)) {
        const rows: string[][] = [];
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
          rows.push(fit(splitRow(lines[i]), header.length));
          i += 1;
        }
        blocks.push({ type: "table", header, rows });
        continue;
      }
    }

    const marker = listMarker(trimmed);
    if (marker) {
      const list: AnswerBlock & { type: "list" } = {
        type: "list",
        ordered: marker.ordered,
        start: marker.ordinal,
        items: [],
      };
      const baseIndent = indentOf(line);
      while (i < lines.length) {
        const current = lines[i];
        const t = current.trim();
        if (!t) {
          // A blank line ends the list unless the next line continues it.
          const next = lines[i + 1];
          if (next !== undefined && listMarker(next.trim())) {
            i += 1;
            continue;
          }
          break;
        }
        const m = listMarker(t);
        if (!m) break;
        const nested = indentOf(current) >= baseIndent + NEST_INDENT && list.items.length > 0;
        if (nested) {
          const parent = list.items[list.items.length - 1];
          parent.children ??= { ordered: m.ordered, items: [] };
          parent.children.items.push(m.text);
        } else if (m.ordered === list.ordered) {
          list.items.push({ text: m.text });
        } else {
          break;
        }
        i += 1;
      }
      blocks.push(list);
      continue;
    }

    blocks.push({ type: "paragraph", text: trimmed });
    i += 1;
  }

  return blocks;
}

const plainText: InlineRenderer = (text) => text;

/**
 * `**bold**` spans around an inner renderer: every run of text (inside or
 * outside the bold) goes through `inner`, e.g. the citation-pill renderer.
 */
export function withBold(inner: InlineRenderer): InlineRenderer {
  return function renderWithBold(text: string, key: string): ReactNode {
    const parts = text.split("**");
    // An unmatched trailing "**" stays literal.
    if (parts.length % 2 === 0) {
      const last = parts.pop() ?? "";
      parts[parts.length - 1] = `${parts[parts.length - 1]}**${last}`;
    }
    return parts.map((part, idx) =>
      idx % 2 === 1 && part ? (
        <strong key={`${key}-${idx}`} className="font-semibold text-ink">
          {inner(part, `${key}-${idx}`)}
        </strong>
      ) : (
        <span key={`${key}-${idx}`}>{idx % 2 === 1 ? "****" : inner(part, `${key}-${idx}`)}</span>
      ),
    );
  };
}

/** Default inline renderer: `**bold**` only; everything else is text. */
export const renderBold: InlineRenderer = withBold(plainText);

function renderList(
  ordered: boolean,
  items: (ListItem | string)[],
  key: string,
  inline: InlineRenderer,
  start = 1,
): ReactNode {
  const className = ordered ? "list-decimal space-y-1.5 pl-5" : "list-disc space-y-1.5 pl-4";
  const children = items.map((item, idx) => {
    const entry = typeof item === "string" ? { text: item } : item;
    return (
      <li key={`${key}-${idx}`}>
        {inline(entry.text, `${key}-${idx}`)}
        {entry.children
          ? renderList(entry.children.ordered, entry.children.items, `${key}-${idx}-n`, inline)
          : null}
      </li>
    );
  });
  return ordered ? (
    <ol key={key} className={className} start={start === 1 ? undefined : start}>
      {children}
    </ol>
  ) : (
    <ul key={key} className={className}>
      {children}
    </ul>
  );
}

function renderTable(block: AnswerBlock & { type: "table" }, key: string, inline: InlineRenderer): ReactNode {
  return (
    // Wide tables scroll inside the bubble (phones) instead of the page.
    <div
      key={key}
      role="region"
      aria-label="Table"
      tabIndex={0}
      className="max-w-full overflow-x-auto rounded-lg border border-line focus:outline-hidden focus-visible:ring-1 focus-visible:ring-brand-500"
    >
      <table className="w-full min-w-max border-collapse text-left text-xs">
        <thead className="bg-slate-50 dark:bg-white/5">
          <tr>
            {block.header.map((cell, idx) => (
              <th key={idx} scope="col" className="border-b border-line px-2.5 py-1.5 font-semibold text-ink">
                {inline(cell, `${key}-h-${idx}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, r) => (
            <tr key={r} className="border-b border-line last:border-b-0">
              {row.map((cell, c) => (
                <td key={c} className="px-2.5 py-1.5 align-top">
                  {inline(cell, `${key}-${r}-${c}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderBlock(block: AnswerBlock, idx: number, inline: InlineRenderer): ReactNode {
  const key = `b-${idx}`;
  switch (block.type) {
    case "heading":
      return block.level === 2 ? (
        <h2 key={key} className="pt-1 text-sm font-semibold text-ink">
          {inline(block.text, key)}
        </h2>
      ) : (
        <h3 key={key} className="text-sm font-medium text-ink">
          {inline(block.text, key)}
        </h3>
      );
    case "list":
      return renderList(block.ordered, block.items, key, inline, block.start);
    case "table":
      return renderTable(block, key, inline);
    default:
      return (
        <p key={key} className="leading-relaxed">
          {inline(block.text, key)}
        </p>
      );
  }
}

export interface AnswerMarkdownOptions {
  /** Inline renderer for every text run (defaults to bold only). */
  renderInline?: InlineRenderer;
}

export function renderAnswerMarkdown(content: string, options: AnswerMarkdownOptions = {}): ReactNode {
  const inline = options.renderInline ?? renderBold;
  return <div className="min-w-0 space-y-2">{parseAnswer(content).map((block, idx) => renderBlock(block, idx, inline))}</div>;
}
