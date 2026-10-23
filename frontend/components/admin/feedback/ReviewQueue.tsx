"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { CheckCircle2, FlaskConical, Keyboard, MessageSquareDashed, Star, XCircle } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/ToastProvider";
import { iconButton, primaryButton, secondaryButton } from "@/components/ui/styles";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { useCursorStack } from "@/lib/use-cursor-stack";
import { isTypingTarget } from "@/lib/shortcuts";
import { errorStatus, invalidate, queryCache, useApiMutation, useApiQuery, usePrefetchNextPage } from "@/lib/query-cache";
import {
  FEEDBACK_KEY_PREFIX,
  MAX_BULK_IDS,
  OUTCOMES,
  TRIAGE_LABELS,
  TRIAGE_STATUSES,
  bulkTriage,
  feedbackKeys,
  feedbackListPath,
  listFeedback,
  promoteToTest,
  reasonLabel,
  triageFeedback,
  type FeedbackItem,
  type FeedbackListParams,
  type FeedbackPage,
  type TriagePatch,
} from "@/lib/feedback-api";
import {
  hasClientFilters,
  matchesClientFilters,
  matchesFilters,
  outcomeLabel,
  toListParams,
  type FeedbackFilters,
  type FilterPatch,
} from "@/lib/feedback-filters";
import type { Audience } from "@/lib/types";
import { FeedbackDrawer } from "./FeedbackDrawer";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { ACTION_LABELS, STATUS_BADGE, canTransition, ratingTone, replaceItem, type TriageAction } from "./triage";

const HTTP_CONFLICT_STATUS = 409;

export const QUEUE_PAGE_SIZE = 25;

/** First-page list params of the queue (also used to warm it before the tab opens). */
export function queueBaseParams(
  audience: Audience,
  filters: FeedbackFilters,
  days?: { from?: string; to?: string },
): FeedbackListParams {
  return { audience, ...toListParams(filters, { from: days?.from, to: days?.to }), limit: QUEUE_PAGE_SIZE };
}

export interface ReviewQueueProps {
  token: string;
  audience: Audience;
  filters: FeedbackFilters;
  onFilter: (patch: FilterPatch) => void;
  canTriage: boolean;
  /** Global date range (inclusive UTC days); a chart-picked day filter wins. */
  days?: { from?: string; to?: string };
}

interface TriageVars {
  item: FeedbackItem;
  patch: TriagePatch;
}

const SHORTCUT_HINTS: readonly [string, string][] = [
  ["j / k", "move"],
  ["Enter", "open"],
  ["x", "select"],
  ["r", "reviewed"],
  ["d", "dismiss"],
  ["p", "promote"],
];

/**
 * Review Queue: a keyboard-first triage workspace with
 * multi-select bulk actions, a detail drawer, optimistic updates with undo
 * and idle prefetch of the next page. Operators (no `feedback:triage`) see a
 * read-only queue.
 */
export function ReviewQueue({ token, audience, filters, onFilter, canTriage, days }: ReviewQueueProps) {
  const toast = useToast();
  const rowVariants = useMotionVariants(fadeUp);

  // ---- data -------------------------------------------------------------
  const baseParams = useMemo<FeedbackListParams>(
    () => queueBaseParams(audience, filters, { from: days?.from, to: days?.to }),
    [audience, filters, days?.from, days?.to],
  );
  const baseSignature = feedbackListPath(baseParams);
  // Reset to page 1 in the same render as a filter / sort change: an old
  // cursor sent with a new sort is rejected (422) by the keyset API.
  const pager = useCursorStack(baseSignature);
  const cursor = pager.cursor ?? null;

  const params = useMemo(() => ({ ...baseParams, cursor }), [baseParams, cursor]);
  const listKey = feedbackKeys.list(feedbackListPath(params));
  const fetchPage = useCallback((signal: AbortSignal) => listFeedback(token, params, signal), [token, params]);
  const { data, error, isLoading, isValidating, refetch } = useApiQuery<FeedbackPage>(listKey, fetchPage, {
    // Paging, sorting and filtering swap the rows in place (no skeleton flash).
    keepPreviousData: true,
  });

  const nextCursor = data?.next_cursor ?? null;
  const nextParams = useMemo(() => ({ ...baseParams, cursor: nextCursor }), [baseParams, nextCursor]);
  usePrefetchNextPage<FeedbackPage>(
    nextCursor ? feedbackKeys.list(feedbackListPath(nextParams)) : null,
    (signal) => listFeedback(token, nextParams, signal),
  );

  const rows = useMemo(
    () => (data?.items ?? []).filter((it) => matchesFilters(it, filters) && matchesClientFilters(it, filters)),
    [data, filters],
  );

  // ---- focus, selection, drawer ------------------------------------------
  const [focusIndex, setFocusIndex] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const autoOpened = useRef<string | null>(null);

  useEffect(() => {
    setSelected(new Set());
    setFocusIndex(0);
  }, [listKey]);
  useEffect(() => {
    if (focusIndex > rows.length - 1) setFocusIndex(Math.max(0, rows.length - 1));
  }, [rows.length, focusIndex]);
  // A deep link to one message (EntityLink) opens its drawer once.
  useEffect(() => {
    if (filters.message_id && rows[0] && autoOpened.current !== filters.message_id) {
      autoOpened.current = filters.message_id;
      setDrawerId(rows[0].id);
    }
  }, [filters.message_id, rows]);

  const drawerItem = useMemo(
    () => (drawerId ? (data?.items.find((it) => it.id === drawerId) ?? null) : null),
    [data, drawerId],
  );
  const drawerIndex = drawerItem ? rows.findIndex((r) => r.id === drawerItem.id) : -1;

  const moveFocus = useCallback(
    (delta: number) => {
      if (rows.length === 0) return;
      const next = Math.min(rows.length - 1, Math.max(0, focusIndex + delta));
      setFocusIndex(next);
      const row = rows[next];
      rowRefs.current.get(row.id)?.scrollIntoView?.({ block: "nearest" });
      if (drawerId) setDrawerId(row.id);
    },
    [drawerId, focusIndex, rows],
  );

  // ---- mutations ----------------------------------------------------------
  const setItem = useCallback(
    (id: string, update: (it: FeedbackItem) => FeedbackItem) =>
      queryCache.setData<FeedbackPage>(listKey, (page) => replaceItem(page, id, update)),
    [listKey],
  );
  const afterTriage = useCallback(() => {
    invalidate(`${FEEDBACK_KEY_PREFIX}/analytics`);
    invalidate(feedbackKeys.golden);
  }, []);

  // 409s get the shared conflict toast; every other failure is reported here
  // (the optimistic change has already been rolled back).
  const reportError = useCallback(
    (err: unknown) => {
      if (errorStatus(err) === HTTP_CONFLICT_STATUS) return;
      toast.show({
        title: "Action failed",
        description: err instanceof Error && err.message ? err.message : "The server rejected the change.",
        tone: "error",
      });
    },
    [toast],
  );

  const triage = useApiMutation<TriageVars, FeedbackItem, FeedbackPage>({
    mutationFn: ({ item, patch }) => triageFeedback(token, item.id, patch),
    optimistic: {
      key: listKey,
      update: (page, { item, patch }) =>
        replaceItem(page, item.id, (it) => ({
          ...it,
          triage_status: patch.triage_status ?? it.triage_status,
          review_note: patch.review_note !== undefined ? patch.review_note : it.review_note,
        })) as FeedbackPage,
    },
    onSuccess: (server) => {
      setItem(server.id, () => server);
      afterTriage();
    },
    onError: reportError,
  });

  const promote = useApiMutation<FeedbackItem, Awaited<ReturnType<typeof promoteToTest>>, FeedbackPage>({
    mutationFn: (item) => promoteToTest(token, item.message_id),
    optimistic: {
      key: listKey,
      update: (page, item) => replaceItem(page, item.id, (it) => ({ ...it, triage_status: "promoted" })) as FeedbackPage,
    },
    onSuccess: (res, item) => {
      setItem(item.id, (it) => ({ ...it, triage_status: "promoted", promoted_case_id: res.case_id }));
      afterTriage();
    },
    onError: reportError,
  });

  const bulk = useApiMutation<{ ids: string[]; action: TriageAction }, Awaited<ReturnType<typeof bulkTriage>>, FeedbackPage>({
    mutationFn: ({ ids, action }) => bulkTriage(token, { ids, action }),
    optimistic: {
      key: listKey,
      update: (page, { ids, action }) =>
        (page ? { ...page, items: page.items.map((it) => (ids.includes(it.id) ? { ...it, triage_status: action } : it)) } : page) as FeedbackPage,
    },
    invalidate: [feedbackKeys.listPrefix],
    onSuccess: () => afterTriage(),
    onError: reportError,
  });

  // Rows act independently: only a row with a request in flight (or a bulk
  // action) waits, so r / d / p can be pressed row after row without pausing.
  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(() => new Set());
  const pendingRef = useRef<ReadonlySet<string>>(pendingIds);
  const setPending = useCallback((id: string, on: boolean) => {
    const next = new Set(pendingRef.current);
    if (on) next.add(id);
    else next.delete(id);
    pendingRef.current = next;
    setPendingIds(next);
  }, []);
  const busy = bulk.isPending;

  const restore = useCallback(
    async (item: FeedbackItem, version: number) => {
      const res = await triage.mutate({ item: { ...item, version }, patch: { version, triage_status: "new" } });
      if (res) toast.show({ title: "Restored to the queue", tone: "info" });
    },
    [toast, triage],
  );

  const runOne = useCallback(
    async (item: FeedbackItem, action: TriageAction) => {
      if (action === "promoted") {
        const res = await promote.mutate(item);
        if (res) {
          toast.show({
            title: ACTION_LABELS.promoted.done,
            description: res.created ? `Case ${res.case_id} added to the golden set.` : `Already in the golden set as ${res.case_id}.`,
            tone: "success",
          });
        }
        return;
      }
      const res = await triage.mutate({ item, patch: { version: item.version, triage_status: action } });
      if (!res) return;
      toast.show({
        title: ACTION_LABELS[action].done,
        tone: "success",
        // Only dismissal can be reversed (dismissed -> new); review and promotion are final.
        onUndo: action === "dismissed" ? () => void restore(res, res.version) : undefined,
      });
    },
    [promote, restore, toast, triage],
  );

  const runAction = useCallback(
    async (item: FeedbackItem, action: TriageAction) => {
      if (!canTriage || busy || pendingRef.current.has(item.id) || !canTransition(item.triage_status, action)) return;
      setPending(item.id, true);
      try {
        await runOne(item, action);
      } finally {
        setPending(item.id, false);
      }
    },
    [busy, canTriage, runOne, setPending],
  );

  const runBulk = useCallback(
    async (action: TriageAction) => {
      const items = rows.filter((r) => selected.has(r.id) && canTransition(r.triage_status, action));
      if (items.length === 0 || busy) return;
      const ids = items.slice(0, MAX_BULK_IDS).map((r) => r.id);
      const res = await bulk.mutate({ ids, action });
      if (res) {
        setSelected(new Set());
        toast.show({ title: `${ACTION_LABELS[action].done}: ${res.updated} item${res.updated === 1 ? "" : "s"}`, tone: "success" });
      }
    },
    [bulk, busy, rows, selected, toast],
  );

  const saveNote = useCallback(
    async (item: FeedbackItem, note: string) => {
      const res = await triage.mutate({ item, patch: { version: item.version, review_note: note || null } });
      if (res) toast.show({ title: "Note saved", tone: "success" });
    },
    [toast, triage],
  );

  const saveCorrectedAnswer = useCallback(
    async (item: FeedbackItem, answer: string) => {
      const res = await triage.mutate({ item, patch: { version: item.version, corrected_answer: answer || null } });
      if (!res) return;
      // Stored PII-masked, so compare presence rather than the exact text.
      if (Boolean(res.corrected_answer) === Boolean(answer)) {
        toast.show({ title: "Correct answer saved", description: "It is now the chosen side of this preference pair.", tone: "success" });
      } else {
        toast.show({
          title: "Correct answer not stored",
          description: "The API did not accept a reviewer answer for this row yet.",
          tone: "error",
        });
      }
    },
    [toast, triage],
  );

  // ---- keyboard ------------------------------------------------------------
  const keyState = useRef({ rows, focusIndex, drawerId, runAction, moveFocus, canTriage });
  keyState.current = { rows, focusIndex, drawerId, runAction, moveFocus, canTriage };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      const s = keyState.current;
      const current = s.drawerId ? s.rows.find((r) => r.id === s.drawerId) : s.rows[s.focusIndex];
      switch (e.key) {
        case "j":
          e.preventDefault();
          s.moveFocus(1);
          break;
        case "k":
          e.preventDefault();
          s.moveFocus(-1);
          break;
        case "Enter":
          if (s.drawerId || !current || (e.target instanceof HTMLElement && e.target.closest("button, a"))) return;
          e.preventDefault();
          setDrawerId(current.id);
          break;
        case "x":
          if (!current || !s.canTriage) return;
          e.preventDefault();
          setSelected((set) => {
            const next = new Set(set);
            if (next.has(current.id)) next.delete(current.id);
            else next.add(current.id);
            return next;
          });
          break;
        case "p":
        case "d":
        case "r": {
          if (!current || !s.canTriage) return;
          e.preventDefault();
          const action: TriageAction = e.key === "p" ? "promoted" : e.key === "d" ? "dismissed" : "reviewed";
          void s.runAction(current, action);
          break;
        }
        default:
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---- render --------------------------------------------------------------
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)));
  const toggleOne = (id: string) =>
    setSelected((set) => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toolbar = (
    <div data-tour="feedback-queue-toolbar" className="space-y-3">
      <TableToolbar
        search={filters.q ?? ""}
        onSearchChange={(v) => onFilter({ q: v || null })}
        debounceMs={300}
        searchPlaceholder="Search question, comment, user or IDs…"
        filters={[
          {
            id: "status",
            label: "Status",
            value: filters.status,
            options: [{ label: "All statuses", value: "all" }, ...TRIAGE_STATUSES.map((s) => ({ label: TRIAGE_LABELS[s], value: s }))],
            onChange: (v) => onFilter({ status: v }),
          },
          {
            id: "rating",
            label: "Rating",
            value: filters.rating === undefined ? "all" : String(filters.rating),
            options: [{ label: "All ratings", value: "all" }, ...[5, 4, 3, 2, 1].map((r) => ({ label: `${r} star${r === 1 ? "" : "s"}`, value: String(r) }))],
            onChange: (v) => onFilter({ rating: v === "all" ? null : v }),
          },
          {
            id: "outcome",
            label: "Outcome",
            value: filters.outcome ?? "all",
            options: [{ label: "All outcomes", value: "all" }, ...OUTCOMES.map((o) => ({ label: outcomeLabel(o), value: o }))],
            onChange: (v) => onFilter({ outcome: v === "all" ? null : v }),
          },
          {
            id: "grounded",
            label: "Grounded",
            value: filters.grounded === undefined ? "all" : String(filters.grounded),
            options: [
              { label: "Any", value: "all" },
              { label: "Grounded", value: "true" },
              { label: "Ungrounded", value: "false" },
            ],
            onChange: (v) => onFilter({ grounded: v === "all" ? null : v }),
          },
          {
            id: "has_comment",
            label: "Comment",
            value: filters.has_comment === undefined ? "all" : String(filters.has_comment),
            options: [
              { label: "Any", value: "all" },
              { label: "Has comment", value: "true" },
              { label: "No comment", value: "false" },
            ],
            onChange: (v) => onFilter({ has_comment: v === "all" ? null : v }),
          },
          {
            id: "sort",
            label: "Sort",
            value: filters.sort,
            options: [
              { label: "Newest first", value: "created_at_desc" },
              { label: "Oldest first", value: "created_at_asc" },
              { label: "Lowest rating", value: "rating_asc" },
              { label: "Highest rating", value: "rating_desc" },
            ],
            onChange: (v) => onFilter({ sort: v }),
          },
        ]}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p
          data-tour="feedback-queue-shortcuts"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-subtle"
          aria-label="Keyboard shortcuts"
        >
          <Keyboard className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          {SHORTCUT_HINTS.filter(([k]) => canTriage || ["j / k", "Enter"].includes(k)).map(([k, label]) => (
            <span key={k}>
              <kbd className="rounded bg-surface-muted px-1 font-mono text-fg-muted">{k}</kbd> {label}
            </span>
          ))}
        </p>
        {canTriage && selected.size > 0 ? (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Bulk actions">
            <span className="text-xs font-medium text-fg-muted">{selected.size} selected</span>
            <button type="button" disabled={busy} onClick={() => void runBulk("reviewed")} className={secondaryButton}>
              <CheckCircle2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Mark reviewed
            </button>
            <button type="button" disabled={busy} onClick={() => void runBulk("dismissed")} className={secondaryButton}>
              <XCircle className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Dismiss
            </button>
            <button type="button" disabled={busy} onClick={() => void runBulk("promoted")} className={primaryButton}>
              <FlaskConical className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              Promote
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );

  let body: React.ReactNode;
  if (error && !data) {
    body = <ErrorState title="Could not load the review queue" message={error instanceof Error ? error.message : undefined} onRetry={() => void refetch()} />;
  } else if (isLoading) {
    body = <SkeletonRows rows={6} columns={6} label="Loading feedback" />;
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        icon={MessageSquareDashed}
        title={filters.status === "new" ? "Nothing left to triage" : "No feedback matches these filters"}
        description={hasClientFilters(filters) ? "Reason and sentiment filters apply to the loaded page; try the next page." : "Remove a filter chip to widen the queue."}
      />
    );
  } else {
    body = (
      <table className="w-full text-left text-xs" aria-label="Feedback review queue" aria-rowcount={rows.length}>
        <thead className="border-b border-line-brand/70 bg-surface-studio dark:border-brand-500/20 text-[11px] uppercase tracking-wide text-fg-subtle">
          <tr>
            {canTriage ? (
              <th scope="col" className="w-10 px-4 py-2.5">
                <input
                  type="checkbox"
                  aria-label="Select all rows"
                  checked={allSelected}
                  onChange={toggleAll}
                  className="h-3.5 w-3.5 rounded border-line accent-brand-600"
                />
              </th>
            ) : null}
            <th scope="col" className="px-3 py-2.5">Rating</th>
            <th scope="col" className="px-3 py-2.5">Question and comment</th>
            <th scope="col" className="px-3 py-2.5">Reasons</th>
            <th scope="col" className="px-3 py-2.5">Outcome</th>
            <th scope="col" className="px-3 py-2.5">Status</th>
            <th scope="col" className="px-3 py-2.5">Submitted</th>
            {canTriage ? <th scope="col" className="px-4 py-2.5 text-center">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          <AnimatePresence initial={false}>
            {rows.map((item, index) => {
              const focused = index === focusIndex;
              return (
                <m.tr
                  key={item.id}
                  ref={(el: HTMLTableRowElement | null) => {
                    if (el) rowRefs.current.set(item.id, el);
                    else rowRefs.current.delete(item.id);
                  }}
                  layout
                  variants={rowVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  data-testid="queue-row"
                  data-focused={focused ? "true" : "false"}
                  aria-selected={selected.has(item.id)}
                  onClick={() => {
                    setFocusIndex(index);
                    setDrawerId(item.id);
                  }}
                  className={`cursor-pointer border-b border-line/70 transition-colors duration-150 last:border-0 hover:bg-surface-muted/60 ${
                    focused ? "bg-brand-50/70 shadow-[inset_3px_0_0_rgb(var(--brand-600))] dark:bg-brand-950/40" : ""
                  }`}
                >
                  {canTriage ? (
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select feedback ${index + 1}`}
                        checked={selected.has(item.id)}
                        onChange={() => toggleOne(item.id)}
                        className="h-3.5 w-3.5 rounded border-line accent-brand-600"
                      />
                    </td>
                  ) : null}
                  <td className="px-3 py-3">
                    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-semibold ${ratingTone(item.rating)}`}>
                      <Star className="h-3 w-3 fill-current" strokeWidth={1.75} aria-hidden="true" />
                      {item.rating}
                    </span>
                  </td>
                  <td className="max-w-md px-3 py-3">
                    <p className="line-clamp-1 font-medium text-fg">{item.user_query ?? "—"}</p>
                    <p className="line-clamp-1 text-fg-subtle">{item.comment || "No comment"}</p>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-1">
                      {item.reason_tags.length === 0 ? <span className="text-fg-subtle">—</span> : null}
                      {item.reason_tags.map((tag) => (
                        <span key={tag} className="rounded-full bg-surface-muted px-1.5 py-0.5 text-[10px] text-fg-muted">
                          {reasonLabel(tag)}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-fg-muted">
                    {outcomeLabel(item.outcome)}
                    {item.grounded === false ? <span className="ml-1 text-status-warn-fg">· ungrounded</span> : null}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_BADGE[item.triage_status]}`}>
                      {TRIAGE_LABELS[item.triage_status]}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-fg-subtle">
                    <RelativeTime value={item.created_at} />
                  </td>
                  {canTriage ? (
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex justify-end gap-1">
                        {(["reviewed", "dismissed", "promoted"] as const).map((action) => {
                          const Icon = action === "reviewed" ? CheckCircle2 : action === "dismissed" ? XCircle : FlaskConical;
                          return (
                            <button
                              key={action}
                              type="button"
                              disabled={busy || pendingIds.has(item.id) || !canTransition(item.triage_status, action)}
                              onClick={() => void runAction(item, action)}
                              aria-label={`${ACTION_LABELS[action].verb} (${ACTION_LABELS[action].key})`}
                              title={`${ACTION_LABELS[action].verb} (${ACTION_LABELS[action].key})`}
                              className={iconButton}
                            >
                              <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                            </button>
                          );
                        })}
                      </div>
                    </td>
                  ) : null}
                </m.tr>
              );
            })}
          </AnimatePresence>
        </tbody>
      </table>
    );
  }

  return (
    <>
      <div data-tour="feedback-queue">
      <TableCard
        title="Review queue"
        description={canTriage ? "Triage with the keyboard: j / k to move, r / d / p to act, Enter to open." : "Read-only: your role can view feedback but not triage it."}
        badge={
          data ? (
            <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">
              {data.total.toLocaleString()}
            </span>
          ) : null
        }
        toolbar={toolbar}
        cursor={{
          hasPrev: pager.hasPrev,
          hasNext: Boolean(nextCursor),
          onPrev: pager.prev,
          onNext: () => pager.next(nextCursor),
          pageItemCount: rows.length,
          itemLabel: "ratings",
          isLoading: isValidating,
        }}
      >
        {body}
      </TableCard>
      </div>
      <FeedbackDrawer
        item={drawerItem}
        onClose={() => setDrawerId(null)}
        onPrev={drawerIndex > 0 ? () => moveFocus(-1) : undefined}
        onNext={drawerIndex >= 0 && drawerIndex < rows.length - 1 ? () => moveFocus(1) : undefined}
        canTriage={canTriage}
        busy={busy || (drawerItem ? pendingIds.has(drawerItem.id) : false)}
        onAction={(item, action) => void runAction(item, action)}
        onRestore={(item) => void restore(item, item.version)}
        onSaveNote={saveNote}
        onSaveCorrectedAnswer={saveCorrectedAnswer}
      />
    </>
  );
}
