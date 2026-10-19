"use client";

import type React from "react";
import { useState } from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { ArrowDown, ArrowUp, ArrowUpRight, ChevronDown } from "lucide-react";
import { Pagination, type PageSize } from "@/components/admin/Pagination";
import { TableCard } from "@/components/admin/TableCard";
import type { ConversationOutcome, ConversationRow, ConversationSort, InsightsFilters, TurnDetail } from "@/lib/insights-api";
import { duration, ease, usePrefersReducedMotion } from "@/lib/motion";
import { clock, fmtInt, isoDay, shortId } from "./format";
import { errorText, useInsightsConversation, useInsightsConversations } from "./use-insights";
import type { AnalyticsState } from "./url-state";

export interface ConversationColumn {
  key: ConversationSort;
  label: string;
  numeric?: boolean;
}

/** The HTML's `COLS`; every key is in the backend sort allow-list. */
export const CONVERSATION_COLUMNS: readonly ConversationColumn[] = [
  { key: "conv_id", label: "Conv" },
  { key: "person", label: "Person" },
  { key: "start", label: "Started" },
  { key: "user_type", label: "Type" },
  { key: "outcome", label: "Outcome" },
  { key: "experience_rating", label: "Rating" },
  { key: "issue", label: "Issue" },
  { key: "exchanges", label: "Qs", numeric: true },
];

const OUTCOME_PILL: Record<ConversationOutcome, string> = {
  Served: "border-brand-500/60 bg-brand-50 text-brand-800 dark:border-brand-400/50 dark:bg-brand-950/60 dark:text-brand-200",
  "Partly served": "border-status-warn/50 bg-status-warn-bg text-status-warn-fg",
  "Not served": "border-status-danger/50 bg-status-danger-bg text-status-danger-fg",
  "Greeting only": "border-line bg-surface-muted text-fg-muted",
};

export function OutcomePill({ outcome }: { outcome: ConversationOutcome }) {
  return (
    <span className={`inline-block rounded-full border px-2 py-px text-[11px] font-semibold ${OUTCOME_PILL[outcome]}`}>{outcome}</span>
  );
}

const ICON = { className: "h-3.5 w-3.5", strokeWidth: 1.75, "aria-hidden": true } as const;
const CELL = "whitespace-nowrap px-3 py-2 align-top";

function turnMeta(t: TurnDetail): string {
  if (t.pending) return `${clock(t.timestamp)} · Pending classification`;
  const parts = [
    clock(t.timestamp),
    t.intent_label ?? t.intent ?? "Unlabelled",
    t.substantive === false ? "greeting/closing" : t.handled ? "Handled" : "Not handled",
    t.quality_label ?? t.quality ?? "",
  ];
  if (t.issue_label ?? t.issue) parts.push(t.issue_label ?? t.issue ?? "");
  if (t.friction) parts.push(`friction: ${t.friction}`);
  return parts.filter(Boolean).join(" · ");
}

/** Per-turn detail of one conversation (the HTML's `detailBox()`). */
export function TurnList({ convId, filters }: { convId: string; filters: InsightsFilters }) {
  const { data, error, isLoading } = useInsightsConversation(convId, filters, true);
  if (isLoading) return <p className="px-4 py-3 text-xs text-fg-subtle">Loading questions…</p>;
  if (error) return <p role="alert" className="px-4 py-3 text-xs text-status-danger-fg">{errorText(error)}</p>;
  const turns = data?.turns ?? [];
  if (turns.length === 0) return <p className="px-4 py-3 text-xs text-fg-subtle">No questions recorded.</p>;
  return (
    <ol className="list-decimal space-y-1.5 rounded-lg bg-surface-muted px-4 py-3 pl-8 text-xs" aria-label="Questions in this conversation">
      {[...turns]
        .sort((a, b) => a.turn - b.turn)
        .map((t) => (
          <li key={t.turn} className="whitespace-normal text-fg">
            <strong className="font-semibold">{t.user_message || "(empty)"}</strong>{" "}
            <span className="text-fg-muted">· {turnMeta(t)}</span>
          </li>
        ))}
    </ol>
  );
}

function ExpandRow({ row, open, filters }: { row: ConversationRow; open: boolean; filters: InsightsFilters }) {
  const reduced = usePrefersReducedMotion();
  return (
    <tr className={open ? "border-b border-line" : ""} id={`turns-${row.conv_id}`}>
      <td colSpan={CONVERSATION_COLUMNS.length + 1} className="p-0">
        <AnimatePresence initial={false}>
          {open && (
            <m.div
              key="turns"
              initial={reduced ? false : { height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
              transition={{ duration: duration.base, ease }}
              className="overflow-hidden"
            >
              <div className="px-3 py-2">
                <TurnList convId={row.conv_id} filters={filters} />
              </div>
            </m.div>
          )}
        </AnimatePresence>
      </td>
    </tr>
  );
}

export interface ConversationsTableProps {
  state: AnalyticsState;
  filters: InsightsFilters;
  update: (patch: Partial<AnalyticsState>) => void;
}

/** Conversations tab: server-sorted, offset-paginated table with per-turn expand. */
export function ConversationsTable({ state, filters, update }: ConversationsTableProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const offset = (state.page - 1) * state.limit;
  const page = { sort: state.sort, dir: state.dir, limit: state.limit, offset };
  const { data, error, isLoading, refetch } = useInsightsConversations(filters, page);
  const total = data?.total ?? 0;
  const rows = data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(total / state.limit));

  const sortBy = (key: ConversationSort) => {
    if (state.sort === key) update({ dir: state.dir === "asc" ? "desc" : "asc", page: 1 });
    else update({ sort: key, dir: key === "exchanges" ? "desc" : "asc", page: 1 });
  };

  let body: React.ReactNode;
  if (isLoading) {
    body = (
      <tr>
        <td colSpan={CONVERSATION_COLUMNS.length + 1} className="px-3 py-8 text-center text-xs text-fg-subtle" role="status">
          Loading conversations…
        </td>
      </tr>
    );
  } else if (error) {
    body = (
      <tr>
        <td colSpan={CONVERSATION_COLUMNS.length + 1} className="px-3 py-8 text-center text-xs text-status-danger-fg" role="alert">
          {errorText(error)}{" "}
          <button type="button" className="underline" onClick={() => void refetch()}>
            Retry
          </button>
        </td>
      </tr>
    );
  } else if (rows.length === 0) {
    body = (
      <tr>
        <td colSpan={CONVERSATION_COLUMNS.length + 1} className="px-3 py-8 text-center text-xs text-fg-subtle">
          No conversations match these filters.
        </td>
      </tr>
    );
  } else {
    body = rows.flatMap((row) => {
      const open = Boolean(expanded[row.conv_id]);
      const href = `/admin/conversations/${row.conv_id}`;
      return [
        <tr key={row.conv_id} data-testid="conversation-row" className="border-b border-line transition-colors duration-150 hover:bg-surface-muted/60">
          <td className={CELL}>
            <button
              type="button"
              aria-expanded={open}
              aria-controls={`turns-${row.conv_id}`}
              aria-label={`${open ? "Hide" : "View"} questions for ${shortId(row.conv_id)}`}
              onClick={() => setExpanded((e) => ({ ...e, [row.conv_id]: !open }))}
              className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-[11.5px] font-medium text-brand-700 transition-colors duration-150 hover:border-brand-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300 dark:hover:border-brand-700/60"
            >
              <ChevronDown {...ICON} className={`h-3.5 w-3.5 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
              {open ? "Hide" : "View"}
            </button>
          </td>
          <td className={CELL}>
            <Link
              href={href}
              className="group inline-flex items-center gap-1 rounded font-mono text-xs font-semibold text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300"
              aria-label={`Open conversation ${shortId(row.conv_id)}`}
            >
              {shortId(row.conv_id)}
              <ArrowUpRight
                {...ICON}
                className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none"
              />
            </Link>
          </td>
          <td className={CELL}>
            {row.person ? (
              <button
                type="button"
                onClick={() => update({ person: state.person === row.person_id ? "" : row.person_id })}
                aria-pressed={state.person === row.person_id}
                aria-label={`Filter to person ${row.person}`}
                className="rounded font-medium text-brand-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300"
              >
                {row.person}
              </button>
            ) : (
              "–"
            )}
          </td>
          <td className={`${CELL} tabular-nums`}>{isoDay(row.start)}</td>
          <td className={CELL}>{row.user_type}</td>
          <td className={CELL}>
            <OutcomePill outcome={row.outcome} />
          </td>
          <td className={CELL}>{row.experience_rating}</td>
          <td className={`${CELL} whitespace-normal`}>{row.issue_label ?? row.issue ?? "–"}</td>
          <td className={`${CELL} text-right tabular-nums`}>{row.exchanges}</td>
        </tr>,
        <ExpandRow key={`${row.conv_id}-turns`} row={row} open={open} filters={filters} />,
      ];
    });
  }

  return (
    <TableCard
      title="Conversations"
      badge={
        <span className="rounded-full border border-line bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-fg-muted">
          {fmtInt(total)} conversations
        </span>
      }
      description="Sort any column; View lists every question and how it was handled. The id opens the full log."
      pagination={
        <Pagination
          page={Math.min(state.page, pageCount)}
          pageCount={pageCount}
          totalItems={total}
          pageSize={state.limit}
          onPageChange={(p) => update({ page: Math.max(1, Math.min(p, pageCount)) })}
          onPageSizeChange={(size: PageSize) => update({ limit: size, page: 1 })}
        />
      }
    >
      <table className="w-full border-collapse text-left text-xs text-fg">
        <caption className="sr-only">Conversations matching the current filters</caption>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">Expand</span>
            </th>
            {CONVERSATION_COLUMNS.map((c) => {
              const activeSort = state.sort === c.key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={activeSort ? (state.dir === "asc" ? "ascending" : "descending") : "none"}
                  className={`whitespace-nowrap px-3 py-2 font-semibold text-fg-muted ${c.numeric ? "text-right" : ""}`}
                >
                  <button
                    type="button"
                    onClick={() => sortBy(c.key)}
                    className="inline-flex items-center gap-1 rounded hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  >
                    {c.label}
                    {activeSort && (state.dir === "asc" ? <ArrowUp {...ICON} /> : <ArrowDown {...ICON} />)}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </TableCard>
  );
}
