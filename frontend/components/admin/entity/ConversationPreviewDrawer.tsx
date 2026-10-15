"use client";

import React, { useMemo } from "react";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { EntityLink } from "@/components/ui/EntityLink";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonText } from "@/components/ui/Skeleton";
import { ToneChip, errorMessage, formatDateTime, outcomeTone } from "@/components/admin/entity/shared";
import { entityHref } from "@/lib/entity-links";
import { useApiQuery } from "@/lib/query-cache";
import { getConversation } from "@/lib/entity-stats-api";
import { buildTurns } from "@/components/admin/entity/turns";

const EXCERPT_CHARS = 280;

function excerpt(text: string | null): string {
  if (!text) return "—";
  return text.length > EXCERPT_CHARS ? `${text.slice(0, EXCERPT_CHARS - 1)}…` : text;
}

interface ConversationPreviewDrawerProps {
  token: string;
  conversationId: string | null;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}

/**
 * Turn timeline for one conversation over the list (F3.3). The list page
 * stays mounted underneath, so its page, sort and filters are unchanged when
 * the drawer closes; "Open full view" goes to the detail page.
 */
export function ConversationPreviewDrawer({ token, conversationId, onClose, onPrev, onNext }: ConversationPreviewDrawerProps) {
  const detail = useApiQuery(conversationId ? ["conversation", conversationId, "detail"] : null, (signal) =>
    getConversation(token, conversationId ?? "", null, signal),
  );
  const turns = useMemo(() => buildTurns(detail.data), [detail.data]);

  const header = detail.data?.conversation;

  return (
    <DrawerShell
      open={conversationId !== null}
      onClose={onClose}
      title={header?.title || "Conversation"}
      description={conversationId ? <span className="font-mono">{conversationId}</span> : undefined}
      fullViewHref={conversationId ? entityHref({ kind: "conversation", id: conversationId }) : undefined}
      onPrev={onPrev}
      onNext={onNext}
      closeLabel="Close preview"
    >
      {detail.isLoading ? (
        <SkeletonText lines={6} label="Loading conversation" />
      ) : detail.error ? (
        <ErrorState compact message={errorMessage(detail.error, "Couldn't load this conversation.")} onRetry={() => void detail.refetch()} />
      ) : turns.length === 0 ? (
        <EmptyState compact title="No turns in this conversation" />
      ) : (
        <ol className="space-y-4" aria-label="Turn timeline">
          {header ? (
            <li className="text-[11px] text-fg-subtle">
              Started {formatDateTime(header.created_at)} · last activity {formatDateTime(header.last_activity_at)}
            </li>
          ) : null}
          {turns.map((t) => {
            const outcome = t.detail?.outcome;
            return (
              <li key={t.turnIndex} className="relative border-l-2 border-brand-200 pl-4 dark:border-brand-800">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <EntityLink
                    entity={{ kind: "turn", id: String(t.turnIndex), conversationId: conversationId ?? "" }}
                    mono={false}
                    copyable={false}
                  >
                    Turn {t.turnIndex + 1}
                  </EntityLink>
                  {outcome ? <ToneChip tone={outcomeTone(outcome)}>{outcome}</ToneChip> : null}
                  {t.grounded === false ? <ToneChip tone="warn">not grounded</ToneChip> : null}
                  <span className="ml-auto text-fg-subtle">{formatDateTime(t.createdAt)}</span>
                </div>
                <p className="mt-1.5 text-xs font-semibold text-fg">{excerpt(t.question)}</p>
                <p className="mt-1 whitespace-pre-wrap text-xs text-fg-muted">{excerpt(t.answer)}</p>
              </li>
            );
          })}
        </ol>
      )}
    </DrawerShell>
  );
}
