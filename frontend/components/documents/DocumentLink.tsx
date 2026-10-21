"use client";

import { ExternalLink } from "lucide-react";
import { ToneChip } from "@/components/admin/entity/shared";
import { ActionTooltip } from "@/components/ui/ActionTooltip";
import { CopyButton } from "@/components/ui/CopyButton";
import { iconButton } from "@/components/ui/styles";
import { displayUrl, safeHttpsUrl } from "@/lib/safe-url";
import type { DocumentSummary } from "@/lib/types";

type LinkFields = Pick<DocumentSummary, "source_name" | "source_url" | "link_status">;

/** "Missing link" / "Link not allowed" (warning tone, text, never colour only). */
export function LinkStatusBadge({ status }: { status: DocumentSummary["link_status"] }) {
  if (status === "missing") {
    return (
      <span className="whitespace-nowrap">
        <ToneChip tone="warn">Missing link</ToneChip>
      </span>
    );
  }
  if (status === "not_allowed") {
    return (
      <span className="whitespace-nowrap" title="The host is no longer on the allowed domain list; citations show no link until it is fixed.">
        <ToneChip tone="warn">Link not allowed</ToneChip>
      </span>
    );
  }
  return null;
}

/**
 * A document's knowledge URL: host + path truncated with an ellipsis (full URL
 * in the tooltip), an Open link (new tab, `noopener noreferrer`) and a Copy
 * button. Value-level read affordances only: editing lives in the row's
 * Actions column. `compact` is the table cell; otherwise the detail page.
 */
export function DocumentLink({ doc, compact = false }: { doc: LinkFields; compact?: boolean }) {
  const url = doc.source_url ?? null;
  const status = doc.link_status;
  if (!url) {
    return status === "missing" ? <LinkStatusBadge status="missing" /> : <span className="text-fg-subtle">—</span>;
  }
  // A stored link whose host left the allow-list is shown but not opened.
  const href = status === "not_allowed" ? null : safeHttpsUrl(url);
  return (
    <div className={`flex min-w-0 ${compact ? "flex-col items-start gap-1" : "flex-wrap items-center gap-2"}`}>
      <div className="flex min-w-0 max-w-full items-center gap-0.5">
        <span
          className={`min-w-0 truncate text-fg-muted ${compact ? "max-w-[7rem] min-[1440px]:max-w-[9rem] min-[1920px]:max-w-[13rem]" : "max-w-[28rem] text-xs"}`}
          title={url}
        >
          {displayUrl(url)}
        </span>
        {href ? (
          <ActionTooltip label="Open in a new tab">
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              aria-label={`Open link for ${doc.source_name} in a new tab`}
              className={`${iconButton} h-6 w-6 rounded-md`}
            >
              <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            </a>
          </ActionTooltip>
        ) : null}
        <CopyButton value={url} label={`Copy link for ${doc.source_name}`} />
      </div>
      <LinkStatusBadge status={status} />
    </div>
  );
}
