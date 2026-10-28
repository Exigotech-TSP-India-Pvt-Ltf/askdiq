"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { m } from "motion/react";
import { ArrowUpRight, ChevronDown } from "lucide-react";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { Pagination, type PageSize } from "@/components/admin/Pagination";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar, type FilterGroup } from "@/components/admin/TableToolbar";
import { useAuth } from "@/lib/auth-context";
import {
  CONVERSATION_OUTCOMES,
  CONVERSATION_SORTS,
  EXPERIENCE_RATINGS,
  MAX_SEARCH_CHARS,
  type ConversationOutcome,
  type ConversationRow,
  type ConversationSort,
  type ExperienceRating,
  type InsightsFilters,
  type QuestionSummary,
  type SortDirection,
  type TurnDetail,
} from "@/lib/insights-api";
import { duration, ease, usePrefersReducedMotion } from "@/lib/motion";
import { runWhenIdle } from "@/lib/query-cache";
import { useTablePreferences } from "@/lib/use-table-preferences";
import { BASE_USER_TYPES } from "./FilterBar";
import { clock, fmtInt, isoDay, shortId } from "./format";
import {
  errorText,
  pageOf,
  prefetchConversation,
  prefetchConversations,
  prefetchPerson,
  useInsightsBundle,
  useInsightsConversation,
  useInsightsConversations,
} from "./use-insights";
import { stripControlChars, type AnalyticsState } from "./url-state";

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

/** Column preferences (visibility, density) are remembered under this id. */
export const CONVERSATIONS_TABLE_ID = "analytics-conversations";
/** Search-as-you-type delay (the server answers searches from memory). */
const SEARCH_DEBOUNCE_MS = 300;
const ALL = "all";
const EXPAND_KEY = "expand";

const OUTCOME_PILL: Record<ConversationOutcome, string> = {
  Served: "border-brand-500/60 bg-brand-50 text-brand-800 dark:border-brand-400/50 dark:bg-brand-950/60 dark:text-brand-200",
  "Partly served": "border-status-warn/50 bg-status-warn-bg text-status-warn-fg",
  "Not served": "border-status-danger/50 bg-status-danger-bg text-status-danger-fg",
  "Greeting only": "border-line bg-surface-muted text-fg-muted",
};

export function OutcomePill({ outcome }: { outcome: ConversationOutcome }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-px text-[11px] font-semibold ${OUTCOME_PILL[outcome]}`}>
      {outcome}
    </span>
  );
}

const ICON = { className: "h-3.5 w-3.5", strokeWidth: 1.75, "aria-hidden": true } as const;

/** One question line: its text and how it was handled. */
interface QuestionLine {
  turn: number;
  text: string;
  meta: string;
}

function handledText(substantive: boolean | null, handled: boolean | null): string {
  if (substantive === false) return "greeting/closing";
  return handled ? "Handled" : "Not handled";
}

function metaOf(parts: (string | null | undefined)[]): string {
  return parts.filter(Boolean).join(" · ");
}

/** A question summary embedded in the table page (the instant path). */
function summaryLine(q: QuestionSummary): QuestionLine {
  return {
    turn: q.turn,
    text: q.text,
    meta: metaOf([
      q.time,
      q.intent_label ?? q.intent ?? "Unlabelled",
      handledText(q.substantive, q.handled),
      q.quality_label ?? q.quality,
      q.issue_label ?? q.issue,
      q.friction ? `friction: ${q.friction}` : null,
    ]),
  };
}

/** A turn of the conversation detail route (rows without embedded questions). */
function detailLine(t: TurnDetail): QuestionLine {
  if (t.pending) return { turn: t.turn, text: t.user_message, meta: `${clock(t.timestamp)} · Pending classification` };
  return {
    turn: t.turn,
    text: t.user_message,
    meta: metaOf([
      clock(t.timestamp),
      t.intent_label ?? t.intent ?? "Unlabelled",
      handledText(t.substantive, t.handled),
      t.quality_label ?? t.quality,
      t.issue_label ?? t.issue,
      t.friction ? `friction: ${t.friction}` : null,
    ]),
  };
}

function QuestionList({ lines }: { lines: QuestionLine[] }) {
  if (lines.length === 0) return <p className="px-4 py-3 text-xs text-fg-subtle">No questions recorded.</p>;
  return (
    <ol className="list-decimal space-y-1.5 rounded-lg bg-surface-muted px-4 py-3 pl-8 text-xs" aria-label="Questions in this conversation">
      {[...lines]
        .sort((a, b) => a.turn - b.turn)
        .map((l) => (
          <li key={l.turn} className="whitespace-normal text-fg">
            <strong className="font-semibold">{l.text || "(empty)"}</strong> <span className="text-fg-muted">· {l.meta}</span>
          </li>
        ))}
    </ol>
  );
}

/** Per-turn detail fetched on demand (fallback for rows without embedded questions). */
export function TurnList({ convId, filters }: { convId: string; filters: InsightsFilters }) {
  const { data, error, isLoading } = useInsightsConversation(convId, filters, true);
  if (isLoading) return <p className="px-4 py-3 text-xs text-fg-subtle">Loading questions…</p>;
  if (error) return <p role="alert" className="px-4 py-3 text-xs text-status-danger-fg">{errorText(error)}</p>;
  return <QuestionList lines={(data?.turns ?? []).map(detailLine)} />;
}

/** A row's questions: from the page payload (no wait), else from the detail route. */
export function RowQuestions({ row, filters }: { row: ConversationRow; filters: InsightsFilters }) {
  const reduced = usePrefersReducedMotion();
  return (
    <m.div
      id={`turns-${row.conv_id}`}
      initial={reduced ? false : { height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      transition={{ duration: duration.base, ease }}
      className="overflow-hidden"
    >
      <div className="px-3 py-2">
        {row.questions ? <QuestionList lines={row.questions.map(summaryLine)} /> : <TurnList convId={row.conv_id} filters={filters} />}
      </div>
    </m.div>
  );
}

/** `start_desc` -> ["start", "desc"] (DataTable's sort key). */
function parseSort(value: string): [ConversationSort, SortDirection] | null {
  const cut = value.lastIndexOf("_");
  const key = value.slice(0, cut);
  const dir = value.slice(cut + 1);
  if (!(CONVERSATION_SORTS as readonly string[]).includes(key) || (dir !== "asc" && dir !== "desc")) return null;
  return [key as ConversationSort, dir];
}

const options = (values: readonly string[], labels: Record<string, string> = {}) => [
  { label: "All", value: ALL },
  ...values.map((v) => ({ label: labels[v] ?? v, value: v })),
];

export interface ConversationsTableProps {
  state: AnalyticsState;
  filters: InsightsFilters;
  update: (patch: Partial<AnalyticsState>) => void;
}

/**
 * Conversations tab: the console's standard table (search, filters,
 * columns, density, sortable headers, pagination), server-sorted and
 * paged. Each page carries its rows' questions, so View expands at once;
 * the next page (with its questions) is warmed while idle. Search and
 * filters are the page's own URL filters, shared with the filter bar and
 * every tab.
 */
export function ConversationsTable({ state, filters, update }: ConversationsTableProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { token } = useAuth();
  const page = pageOf(state);
  const { data, error, isLoading, isPlaceholderData, refetch } = useInsightsConversations(filters, page);
  // The bundle already on screen: issue and user-type options (no request).
  const bundle = useInsightsBundle(filters, page);
  const prefs = useTablePreferences(CONVERSATIONS_TABLE_ID);
  const total = data?.total ?? 0;
  const rows = useMemo(() => data?.items ?? [], [data]);
  const pageCount = Math.max(1, Math.ceil(total / state.limit));

  // While idle, warm each sort toggle's first page and the previous page.
  useEffect(() => {
    if (!token || !data || isPlaceholderData || process.env.NODE_ENV === "test") return undefined;
    return runWhenIdle(() => {
      if (page.offset > 0) prefetchConversations(token, filters, { ...page, offset: Math.max(0, page.offset - page.limit) });
      for (const c of CONVERSATION_COLUMNS) {
        const dir: SortDirection = state.sort === c.key && state.dir === "desc" ? "asc" : "desc";
        prefetchConversations(token, filters, { ...page, sort: c.key, dir, offset: 0 });
      }
    });
    // `page` is derived from these state fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, data, isPlaceholderData, filters, state.sort, state.dir, state.limit, state.page]);

  const issueLabels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const d of bundle.data?.dropoff?.items ?? []) out[d.issue] = d.label;
    for (const r of rows) if (r.issue && r.issue_label) out[r.issue] = r.issue_label;
    return out;
  }, [bundle.data, rows]);
  const issueCodes = Array.from(new Set([...(bundle.data?.dropoff?.items ?? []).map((d) => d.issue), ...(state.issue ? [state.issue] : [])]));
  const userTypes = Array.from(
    new Set([...BASE_USER_TYPES, ...(bundle.data?.flow?.user_types ?? []).map((t) => t.name), ...(state.user_type ? [state.user_type] : [])]),
  );
  const pick = (value: string) => (value === ALL ? "" : value);
  const toolbarFilters: FilterGroup[] = [
    { id: "user_type", label: "Type", value: state.user_type, options: options(userTypes), onChange: (v) => update({ user_type: pick(v) }) },
    {
      id: "outcome",
      label: "Outcome",
      value: state.outcome,
      options: options(CONVERSATION_OUTCOMES),
      onChange: (v) => update({ outcome: pick(v) as AnalyticsState["outcome"] }),
    },
    {
      id: "rating",
      label: "Rating",
      value: state.rating,
      options: options(EXPERIENCE_RATINGS),
      onChange: (v) => update({ rating: pick(v) as ExperienceRating | "" }),
    },
    { id: "issue", label: "Issue", value: state.issue, options: options(issueCodes, issueLabels), onChange: (v) => update({ issue: pick(v) }) },
  ];

  const onSearch = useCallback((value: string) => update({ q: stripControlChars(value).slice(0, MAX_SEARCH_CHARS) }), [update]);

  const toggle = (row: ConversationRow) => setExpanded((e) => ({ ...e, [row.conv_id]: !e[row.conv_id] }));
  const warmRow = (row: ConversationRow) => {
    // Rows of a bundle page already carry their questions.
    if (!row.questions) prefetchConversation(token, row.conv_id, filters);
  };

  const columns: ColumnDef<ConversationRow>[] = [
    {
      key: EXPAND_KEY,
      // A labelled column with a quiet icon toggle instead of a bordered "View" button per row (VA-29).
      header: "Details",
      srOnlyHeader: true,
      width: "1%",
      render: (row) => {
        const open = Boolean(expanded[row.conv_id]);
        return (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={`turns-${row.conv_id}`}
            aria-label={`${open ? "Hide" : "View"} questions for ${shortId(row.conv_id)}`}
            title={open ? "Hide questions" : "View questions"}
            onClick={(e) => {
              e.stopPropagation();
              toggle(row);
            }}
            onFocus={() => warmRow(row)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-muted transition-colors duration-150 hover:bg-surface-muted hover:text-brand-700 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:hover:text-brand-300"
          >
            <ChevronDown {...ICON} className={`h-4 w-4 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
          </button>
        );
      },
    },
    {
      key: "conv_id",
      header: "Conv",
      sortable: true,
      render: (row) => (
        <Link
          href={`/admin/conversations/${row.conv_id}`}
          onClick={(e) => e.stopPropagation()}
          className="group inline-flex items-center gap-1 rounded-sm font-mono text-xs font-semibold text-brand-700 hover:underline focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300"
          aria-label={`Open conversation ${shortId(row.conv_id)}`}
        >
          {shortId(row.conv_id)}
          <ArrowUpRight
            {...ICON}
            className="h-3.5 w-3.5 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none"
          />
        </Link>
      ),
    },
    {
      key: "person",
      header: "Person",
      sortable: true,
      render: (row) =>
        row.person ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              update({ person: state.person === row.person_id ? "" : row.person_id });
            }}
            onMouseEnter={() => prefetchPerson(token, row.person_id, filters, page)}
            aria-pressed={state.person === row.person_id}
            aria-label={`Filter to person ${row.person}`}
            className="rounded-sm font-medium text-brand-700 hover:underline focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300"
          >
            {row.person}
          </button>
        ) : (
          "–"
        ),
    },
    { key: "start", header: "Started", sortable: true, render: (row) => <span className="whitespace-nowrap tabular-nums">{isoDay(row.start)}</span> },
    { key: "user_type", header: "Type", sortable: true, render: (row) => row.user_type },
    { key: "outcome", header: "Outcome", sortable: true, render: (row) => <OutcomePill outcome={row.outcome} /> },
    { key: "experience_rating", header: "Rating", sortable: true, render: (row) => row.experience_rating },
    { key: "issue", header: "Issue", sortable: true, render: (row) => row.issue_label ?? row.issue ?? "–" },
    { key: "exchanges", header: "Qs", sortable: true, align: "right", render: (row) => <span className="tabular-nums">{row.exchanges}</span> },
  ];
  const visible = columns.filter((c) => c.key === EXPAND_KEY || !prefs.hiddenColumns.includes(c.key));

  const filtered = Boolean(state.q || state.user_type || state.outcome || state.rating || state.issue);

  return (
    <TableCard
      toolbar={
        <TableToolbar
          title="Conversations"
          countLabel={isLoading ? undefined : `${fmtInt(total)} conversation${total === 1 ? "" : "s"}`}
          search={state.q}
          onSearchChange={onSearch}
          debounceMs={SEARCH_DEBOUNCE_MS}
          searchPlaceholder="Search id, person, issue or question…"
          filters={toolbarFilters}
          columnOptions={CONVERSATION_COLUMNS.map((c) => ({ key: c.key, label: c.label }))}
          hiddenColumns={prefs.hiddenColumns}
          onToggleColumn={prefs.toggleColumn}
          density={prefs.density}
          onDensityChange={prefs.setDensity}
        />
      }
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
      <DataTable
        columns={visible}
        data={rows}
        keyField="conv_id"
        rowTestId="conversation-row"
        isLoading={isLoading || isPlaceholderData}
        error={error ? errorText(error) : null}
        onRetry={() => void refetch()}
        currentSort={`${state.sort}_${state.dir}`}
        onSortChange={(value) => {
          const parsed = parseSort(value);
          if (parsed) update({ sort: parsed[0], dir: parsed[1], page: 1 });
        }}
        onRowIntent={warmRow}
        density={prefs.density}
        renderExpanded={(row) => (expanded[row.conv_id] ? <RowQuestions row={row} filters={filters} /> : null)}
        emptyMessage={filtered ? "No conversations match this search or these filters" : "No conversations in this range"}
        emptySubMessage={
          filtered ? "Try another search, or clear the filters above." : "Pick a wider date range, or another audience."
        }
      />
    </TableCard>
  );
}
