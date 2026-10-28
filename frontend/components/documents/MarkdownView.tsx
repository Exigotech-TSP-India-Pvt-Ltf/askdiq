"use client";

import React from "react";

/**
 * Minimal, safe Markdown renderer for the canonical documents (no dependency,
 * no `dangerouslySetInnerHTML`): every node is a React element, so raw HTML in
 * the source is shown as text. Supports what the converter emits: ATX
 * headings, paragraphs, bullet / numbered lists, block quotes, fenced code,
 * GFM tables, horizontal rules, and inline bold / italic / code / links
 * (http, https and mailto only; anything else renders as plain text).
 */

export type MdBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "quote"; text: string }
  | { type: "code"; lang: string; text: string }
  | { type: "table"; header: string[]; rows: string[][] }
  | { type: "rule" };

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s*(```|~~~)\s*([\w+-]*)\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d{1,9}[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  const PIPE = "\u0000";
  const trimmed = line.trim().replace(/\\\|/g, PIPE).replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim().split(PIPE).join("|"));
}

/** Block-level parse (pure; exported for tests). */
export function parseMarkdown(source: string): MdBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  let i = 0;

  const isBlockStart = (line: string, next: string | undefined) =>
    HEADING.test(line) ||
    FENCE.test(line) ||
    BULLET.test(line) ||
    ORDERED.test(line) ||
    QUOTE.test(line) ||
    RULE.test(line) ||
    (line.includes("|") && next !== undefined && TABLE_SEPARATOR.test(next));

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(FENCE.exec(lines[i])?.[1] === fence[1])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence (or end of input)
      blocks.push({ type: "code", lang: fence[2], text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ type: "rule" });
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      const header = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        body.push(QUOTE.exec(lines[i])?.[1] ?? "");
        i += 1;
      }
      blocks.push({ type: "quote", text: body.join(" ") });
      continue;
    }
    const ordered = ORDERED.test(line);
    if (ordered || BULLET.test(line)) {
      const pattern = ordered ? ORDERED : BULLET;
      const items: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        const item = pattern.exec(lines[i]);
        if (item) items.push(item[1]);
        else if (items.length && /^\s{2,}\S/.test(lines[i])) items[items.length - 1] += ` ${lines[i].trim()}`;
        else break;
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    const para: string[] = [line.trim()];
    i += 1;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i], lines[i + 1])) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "paragraph", text: para.join(" ") });
  }
  return blocks;
}

const SAFE_HREF = /^(https?:\/\/|mailto:)/i;

export function safeHref(href: string): string | null {
  const trimmed = href.trim();
  return SAFE_HREF.test(trimmed) ? trimmed : null;
}

// Inline tokens, earliest match wins: code, link, bold, italic.
const INLINE = /(`[^`]+`)|(!?\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\))|(\*\*([^*]+)\*\*|__([^_]+)__)|(\*([^*\s][^*]*)\*|_([^_\s][^_]*)_)/;

export function renderInline(text: string, key = "i"): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let rest = text;
  let n = 0;
  while (rest) {
    const match = INLINE.exec(rest);
    if (!match) {
      out.push(rest);
      break;
    }
    if (match.index > 0) out.push(rest.slice(0, match.index));
    const k = `${key}-${n}`;
    n += 1;
    const [whole, code, link, linkText, linkHref, bold, boldA, boldB, italic, italicA, italicB] = match;
    if (code) {
      out.push(
        <code key={k} className="rounded-sm bg-surface-muted px-1 py-0.5 font-mono text-[0.85em] text-fg">
          {code.slice(1, -1)}
        </code>,
      );
    } else if (link) {
      const href = safeHref(linkHref ?? "");
      const isImage = whole.startsWith("!");
      if (isImage) {
        out.push(
          <span key={k} className="italic text-fg-subtle">
            [image: {linkText || "untitled"}]
          </span>,
        );
      } else if (href) {
        out.push(
          <a
            key={k}
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="text-brand-700 underline underline-offset-2 hover:text-brand-800 dark:text-brand-300 dark:hover:text-brand-200"
          >
            {renderInline(linkText ?? "", k)}
          </a>,
        );
      } else {
        out.push(<span key={k}>{renderInline(linkText ?? "", k)}</span>);
      }
    } else if (bold) {
      out.push(<strong key={k}>{renderInline(boldA ?? boldB ?? "", k)}</strong>);
    } else if (italic) {
      out.push(<em key={k}>{renderInline(italicA ?? italicB ?? "", k)}</em>);
    }
    rest = rest.slice(match.index + whole.length);
  }
  return out;
}

const HEADING_CLASS: Record<number, string> = {
  1: "text-xl font-bold",
  2: "text-lg font-semibold",
  3: "text-base font-semibold",
  4: "text-sm font-semibold",
  5: "text-sm font-semibold text-fg-muted",
  6: "text-xs font-semibold uppercase tracking-wide text-fg-muted",
};

function Block({ block, index }: { block: MdBlock; index: number }) {
  const key = `b${index}`;
  switch (block.type) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 1, 6)}` as "h2";
      return <Tag className={`mt-5 text-fg first:mt-0 ${HEADING_CLASS[block.level]}`}>{renderInline(block.text, key)}</Tag>;
    }
    case "paragraph":
      return <p className="leading-relaxed">{renderInline(block.text, key)}</p>;
    case "quote":
      return (
        <blockquote className="border-l-4 border-brand-300 pl-3 italic text-fg-muted dark:border-brand-700/60">
          {renderInline(block.text, key)}
        </blockquote>
      );
    case "code":
      return (
        <pre className="overflow-x-auto rounded-lg border border-line bg-surface-muted p-3 font-mono text-xs text-fg" data-lang={block.lang || undefined}>
          <code>{block.text}</code>
        </pre>
      );
    case "rule":
      return <hr className="border-line" />;
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag className={`space-y-1 pl-5 ${block.ordered ? "list-decimal" : "list-disc"}`}>
          {block.items.map((item, i) => (
            <li key={`${key}-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
          ))}
        </Tag>
      );
    }
    case "table":
      return (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="bg-surface-muted text-fg">
              <tr>
                {block.header.map((cell, c) => (
                  <th key={`${key}-h${c}`} scope="col" className="border-b border-line px-3 py-2 font-semibold">
                    {renderInline(cell, `${key}-h${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {block.rows.map((row, r) => (
                <tr key={`${key}-r${r}`}>
                  {row.map((cell, c) => (
                    <td key={`${key}-r${r}c${c}`} className="px-3 py-2 align-top text-fg-muted">
                      {renderInline(cell, `${key}-r${r}c${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return null;
  }
}

export function MarkdownView({ source, className = "" }: { source: string; className?: string }) {
  const blocks = React.useMemo(() => parseMarkdown(source), [source]);
  return (
    <div className={`space-y-3 text-sm text-fg ${className}`} data-testid="markdown-view">
      {blocks.map((block, index) => (
        <Block key={`b${index}`} block={block} index={index} />
      ))}
    </div>
  );
}
