"use client";

import { useEffect, useId, useState, type MouseEvent } from "react";
import type { Citation } from "@/lib/types";
import { safeHttpsUrl } from "@/lib/safe-url";
import { focusRing } from "@/components/ui/styles";
import { focusSourceChip } from "./SourceChips";
import { SourcePillCard } from "./SourcePillCard";

const PILL = `inline-flex max-w-40 items-center rounded-full bg-surface-muted px-2 text-[11px] font-medium leading-5 text-fg-muted no-underline transition-colors hover:bg-brand-50 hover:text-fg motion-reduce:transition-none dark:hover:bg-white/8 ${focusRing}`;

/** Visible pill text: the server label, else the title. */
function pillLabel(citation: Citation): string {
  return citation.label.trim() || citation.title.trim() || "Source";
}

/**
 * One inline source pill. With a safe https URL it is a link that opens in a
 * new tab; without one (missing or no longer allowed) it is a button that
 * jumps to the matching chip under the answer. The hover / focus card is
 * linked with `aria-describedby`.
 */
export function SourcePill({ citation }: { citation: Citation }) {
  const cardId = useId();
  const [open, setOpen] = useState(false);
  const href = safeHttpsUrl(citation.url);
  const label = pillLabel(citation);
  const name = href
    ? `Source: ${label}, ${citation.title}, opens in a new tab`
    : `Source: ${label}, ${citation.title}`;

  // Esc dismisses the card whether it was opened by hover or focus.
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const shared = {
    "aria-label": name,
    "aria-describedby": cardId,
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
    className: PILL,
  };

  return (
    <span
      className="relative inline-flex align-baseline"
      data-source-pill
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {href ? (
        <a {...shared} href={href} target="_blank" rel="noopener noreferrer">
          <span className="truncate">{label}</span>
        </a>
      ) : (
        <button
          {...shared}
          type="button"
          onClick={(event: MouseEvent<HTMLButtonElement>) => {
            setOpen(false);
            focusSourceChip(event.currentTarget, citation.document_id);
          }}
        >
          <span className="truncate">{label}</span>
        </button>
      )}
      <SourcePillCard id={cardId} open={open} citation={citation} />
    </span>
  );
}

/** One marker run (`[1][3]`) as pills placed right after the claim. */
export function SourcePills({ citations }: { citations: readonly Citation[] }) {
  return (
    <span className="ml-1 inline-flex flex-wrap items-center gap-1 align-baseline">
      {citations.map((c) => (
        <SourcePill key={c.n} citation={c} />
      ))}
    </span>
  );
}
