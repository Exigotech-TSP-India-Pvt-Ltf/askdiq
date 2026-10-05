"use client";

import { useState, type ReactNode } from "react";
import type { ChatMessage } from "@/lib/types";

function CopyIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function BubbleActions({
  onCopy,
  onDelete,
  tone,
}: {
  onCopy: () => void;
  onDelete?: () => void;
  tone: "light" | "dark";
}) {
  const [copied, setCopied] = useState(false);
  const iconClass =
    tone === "dark"
      ? "text-white/60 hover:text-white"
      : "text-ink/35 hover:text-ink/70";

  return (
    <div className="mt-1 flex items-center justify-end gap-2">
      <button
        type="button"
        onClick={() => {
          onCopy();
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        className={`inline-flex items-center gap-1 text-[11px] transition ${iconClass}`}
        aria-label="Copy message"
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
        {copied ? "Copied" : "Copy"}
      </button>

      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          className={`inline-flex items-center gap-1 text-[11px] transition ${iconClass}`}
          aria-label="Delete this exchange"
        >
          <TrashIcon />
          Delete
        </button>
      )}
    </div>
  );
}

// Lightweight formatter (no markdown dependency): renders **bold** spans and
// "- "/"• " bullet lines, since answers come back in that style.
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

function renderFormattedAnswer(content: string): ReactNode {
  const blocks: ReactNode[] = [];
  let currentList: string[] = [];

  const flushList = (key: string) => {
    if (currentList.length === 0) return;
    blocks.push(
      <ul key={key} className="list-disc space-y-1 pl-4">
        {currentList.map((item, i) => (
          <li key={i}>{renderInline(item, `${key}-li-${i}`)}</li>
        ))}
      </ul>
    );
    currentList = [];
  };

  content.split("\n").forEach((line, idx) => {
    const trimmed = line.trim();
    const bulletMatch = trimmed.match(/^[-•]\s+(.*)/);

    if (bulletMatch) {
      currentList.push(bulletMatch[1]);
      return;
    }

    flushList(`list-${idx}`);

    if (trimmed === "") return;

    blocks.push(
      <p key={`p-${idx}`} className="leading-relaxed">
        {renderInline(trimmed, `p-${idx}`)}
      </p>
    );
  });

  flushList("list-end");

  return <div className="space-y-2">{blocks}</div>;
}

export function MessageBubble({
  message,
  onDelete,
}: {
  message: ChatMessage;
  onDelete?: () => void;
}) {
  const isUser = message.role === "user";

  const copy = () => {
    navigator.clipboard?.writeText(message.content).catch(() => {
      // Clipboard API unavailable/blocked — nothing else we can do here.
    });
  };

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tr-sm bg-brand-600 px-4 py-2.5 text-sm text-white">
            {message.content}
          </div>
          <BubbleActions onCopy={copy} onDelete={onDelete} tone="light" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] space-y-2.5">
        <div className="rounded-2xl rounded-tl-sm border border-ink/10 bg-ink/[0.02] px-4 py-3 text-sm text-ink">
          {message.pending ? (
            <span className="inline-flex items-center gap-1.5 text-ink/50">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/30 [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/30 [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink/30" />
            </span>
          ) : message.error ? (
            <span className="text-red-600">{message.error}</span>
          ) : (
            renderFormattedAnswer(message.content)
          )}
        </div>

        {!message.pending && !message.error && (
          <BubbleActions onCopy={copy} onDelete={onDelete} tone="dark" />
        )}
      </div>
    </div>
  );
}

