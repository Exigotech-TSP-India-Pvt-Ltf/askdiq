import type { ReactNode } from "react";

// Lightweight formatter (no markdown dependency): renders **bold** spans,
// "- "/"• " bullet lines, "#"-style headings, and auto-detects loosely
// structured "Label:" + one-point-per-line answers (common from the RAG
// pipeline) as bullet lists even when the model didn't add "-" markers.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter((part) => part.length > 0)
    .map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
      ) : (
        <span key={`${keyPrefix}-${i}`}>{part}</span>
      )
    );
}

// A short line ending in ":" (e.g. "Key points:") is treated as a label that
// introduces a run of one-point-per-line statements below it.
function isLabelLine(trimmed: string): boolean {
  return trimmed.endsWith(":") && trimmed.length <= 80;
}

export function renderFormattedText(content: string): ReactNode {
  const blocks: ReactNode[] = [];
  let currentList: string[] = [];
  let autoListMode = false;

  const flushList = (key: string) => {
    if (currentList.length === 0) return;
    blocks.push(
      <ul key={key} className="list-disc space-y-1.5 pl-4">
        {currentList.map((item, i) => (
          <li key={i}>{renderInline(item, `${key}-li-${i}`)}</li>
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
          {renderInline(headingMatch[2], `h-${idx}`)}
        </p>
      );
      return;
    }

    if (isLabelLine(trimmed)) {
      flushList(`list-${idx}`);
      blocks.push(
        <p key={`label-${idx}`} className="font-semibold text-ink">
          {renderInline(trimmed, `label-${idx}`)}
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
        {renderInline(trimmed, `p-${idx}`)}
      </p>
    );
  });

  flushList("list-end");

  return <div className="space-y-2">{blocks}</div>;
}
