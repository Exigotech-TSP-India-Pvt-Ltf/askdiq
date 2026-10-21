"use client";

import { useBrand } from "@/lib/brand-context";

/** Chat header: the assistant name and what its answers are based on. */
export function ChatHeader() {
  const brand = useBrand();
  return (
    <header data-tour="chat-header" className="flex h-20 shrink-0 items-center border-b border-line bg-surface-card px-4 sm:px-6">
      <div className="min-w-0">
        <h1 className="truncate text-sm font-semibold text-fg">{brand.assistantName}</h1>
        <p className="truncate text-xs text-fg-subtle">
          Answers are grounded in your knowledge base and checked before delivery.
        </p>
      </div>
    </header>
  );
}
