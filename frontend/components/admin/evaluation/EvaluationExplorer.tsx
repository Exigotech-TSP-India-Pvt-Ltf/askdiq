"use client";

/**
 * Query & Case Explorer of the Evaluation page: one server page of slim rows
 * (`GET /metrics/evaluation/live/cases`, filtered and searched on the server
 * from the precomputed report). The previous page stays on screen while the
 * next loads, the next page is warmed when idle, and hovering a row warms its
 * record drawer.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination, type PageSize } from "@/components/admin/Pagination";
import {
  evaluationCaseKey,
  evaluationCasesKey,
  fetchEvaluationCase,
  fetchEvaluationCases,
  fetchEvaluationCasesBatch,
  type CasePage,
  type CaseQuery,
  type EvaluationRange,
  type GroundedFilter,
  type OutcomeFilter,
} from "@/lib/evaluation-live";
import { formatLatencyMs } from "@/lib/format";
import { useApiQuery, usePrefetchNextPage, usePrefetchOnIntent, usePrefetchRows } from "@/lib/query-cache";
import type { Audience } from "@/lib/types";
import { useScrollUnder } from "@/lib/use-scroll-under";
import { stickyCell, stickyHeadCell, tableRow, tableSurface } from "@/components/ui/styles";

/** A case of the precomputed report changes rarely (hover / batch warm it for a minute). */
const CASE_DETAIL_STALE_MS = 60_000;

/** Search runs on the server: one request per pause in typing, not per key. */
export const SEARCH_DEBOUNCE_MS = 120;

const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
const num = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

export interface ExplorerState {
  search: string;
  outcome: OutcomeFilter;
  grounded: GroundedFilter;
  page: number;
  pageSize: PageSize;
}

export const DEFAULT_EXPLORER_STATE: ExplorerState = {
  search: "",
  outcome: "all",
  grounded: "all",
  page: 1,
  pageSize: 25,
};

export function explorerQuery(state: ExplorerState, search = state.search): CaseQuery {
  return {
    outcome: state.outcome,
    grounded: state.grounded,
    q: search,
    offset: (Math.max(1, state.page) - 1) * state.pageSize,
    limit: state.pageSize,
  };
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (Object.is(value, debounced)) return undefined;
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, debounced, ms]);
  return debounced;
}

interface Props {
  token: string | null;
  audience: Audience;
  range: EvaluationRange;
  state: ExplorerState;
  onStateChange: (patch: Partial<ExplorerState>) => void;
  onOpen: (row: Record<string, any>) => void;
}

export function EvaluationExplorer({ token, audience, range, state, onStateChange, onOpen }: Props) {
  const search = useDebounced(state.search, SEARCH_DEBOUNCE_MS);
  const query = explorerQuery(state, search);
  const page = useApiQuery<CasePage>(
    token ? evaluationCasesKey(audience, range, query) : null,
    (signal) => fetchEvaluationCases(token ?? "", audience, range, query, signal),
    { keepPreviousData: true },
  );
  const data = page.data;
  const total = data?.total ?? 0;
  const pageCount = Math.ceil(total / state.pageSize);
  const [scrollRef, scrollUnder] = useScrollUnder<HTMLDivElement>();
  // The server clamps a page past the end to the last page.
  const shownPage = data ? Math.floor(data.offset / Math.max(1, data.limit)) + 1 : state.page;
  const nextQuery = { ...query, offset: query.offset + query.limit };
  usePrefetchNextPage<CasePage>(
    token && data && !page.isPlaceholderData && nextQuery.offset < total
      ? evaluationCasesKey(audience, range, nextQuery)
      : null,
    (signal) => fetchEvaluationCases(token ?? "", audience, range, nextQuery, signal),
  );
  // While idle, the drawer of every case on screen in one request (a click opens from cache).
  usePrefetchRows(
    token && data && !page.isPlaceholderData ? (data.items ?? []).map((c) => String(c.id ?? "")).filter(Boolean) : null,
    (id) => evaluationCaseKey(audience, range, id),
    { batch: (ids, signal) => fetchEvaluationCasesBatch(token ?? "", audience, range, ids, signal) },
    { staleTime: CASE_DETAIL_STALE_MS, version: page.updatedAt },
  );

  return (
    <TableCard
      toolbar={
        <TableToolbar
          title={`Evaluated Queries & Test Cases (${total})`}
          search={state.search}
          onSearchChange={(v) => onStateChange({ search: v, page: 1 })}
          searchPlaceholder="Search queries, answers, IDs..."
          filters={[
            {
              id: "outcome",
              label: "Evaluation Status",
              value: state.outcome,
              options: [
                { label: "All Cases", value: "all" },
                { label: "Passed Only", value: "passed" },
                { label: "Failed Only", value: "failed" },
              ],
              onChange: (v) => onStateChange({ outcome: v as OutcomeFilter, page: 1 }),
            },
            {
              id: "grounded",
              label: "Grounding",
              value: state.grounded,
              options: [
                { label: "All Grounding", value: "all" },
                { label: "Grounded Only", value: "grounded" },
                { label: "Ungrounded Fallback", value: "ungrounded" },
              ],
              onChange: (v) => onStateChange({ grounded: v as GroundedFilter, page: 1 }),
            },
          ]}
        />
      }
      pagination={
        <Pagination
          page={shownPage}
          pageCount={pageCount}
          totalItems={total}
          pageSize={state.pageSize}
          onPageChange={(p) => onStateChange({ page: p })}
          onPageSizeChange={(size) => onStateChange({ pageSize: size, page: 1 })}
        />
      }
    >
      <div
        ref={scrollRef}
        data-tour="evaluation-explorer-table"
        data-scroll-under={scrollUnder ? "true" : undefined}
        className={`overflow-x-auto ${tableSurface}`}
        aria-busy={page.isLoading || page.isPlaceholderData}
      >
        <table className="w-full text-left text-xs">
          {/* VA-5: one-line headers; confidence, recall and citation precision
              (also in the drawer) show from 1440 px; the action column stays
              in view (sticky right) and the query column truncates. */}
          <thead className="bg-surface-studio text-fg-muted dark:text-brand-200 border-b border-line-brand/70 dark:border-brand-500/20 font-bold whitespace-nowrap">
            <tr>
              <th scope="col" className="px-3.5 py-2.5">Status</th>
              <th scope="col" className="hidden px-3.5 py-2.5 sm:table-cell">Outcome type</th>
              <th scope="col" className="w-full px-3.5 py-2.5">Query</th>
              <th scope="col" className="hidden min-[1440px]:table-cell px-3.5 py-2.5 text-right" title="Confidence">Conf.</th>
              <th scope="col" className="hidden min-[1440px]:table-cell px-3.5 py-2.5 text-right">Recall</th>
              <th scope="col" className="hidden min-[1440px]:table-cell px-3.5 py-2.5 text-right" title="Citation precision">Cit. prec.</th>
              <th scope="col" className="hidden px-3.5 py-2.5 text-right md:table-cell">Faithfulness</th>
              <th scope="col" className="hidden px-3.5 py-2.5 text-right sm:table-cell">Latency</th>
              <th scope="col" className={`${stickyHeadCell} px-3.5 py-2.5 text-center`}>Action</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((c) => (
              <CaseRow key={c.id} row={c} token={token} audience={audience} range={range} onOpen={onOpen} />
            ))}
          </tbody>
        </table>
      </div>
    </TableCard>
  );
}

function CaseRow({
  row: c,
  token,
  audience,
  range,
  onOpen,
}: {
  row: Record<string, any>;
  token: string | null;
  audience: Audience;
  range: EvaluationRange;
  onOpen: (row: Record<string, any>) => void;
}) {
  const id = String(c.id ?? "");
  const intent = usePrefetchOnIntent(
    token && id ? evaluationCaseKey(audience, range, id) : null,
    (signal) => fetchEvaluationCase(token ?? "", audience, range, id, signal),
    { staleTime: CASE_DETAIL_STALE_MS },
  );
  const faith = useMemo(
    () => (typeof c.faithfulness === "number" ? `${c.faithfulness.toFixed(1)} / 5` : "—"),
    [c.faithfulness],
  );
  return (
    <tr
      onClick={() => onOpen(c)}
      {...intent}
      className={`${tableRow} group cursor-pointer border-b border-teal-100/50 align-top dark:border-teal-500/10`}
    >
      <td className="px-3.5 py-2.5 whitespace-nowrap">
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            c.passed
              ? "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200/80 dark:border-emerald-800/80"
              : "bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border border-rose-200/80 dark:border-rose-800/80"
          }`}
        >
          {c.passed ? "✅ Pass" : c.passed === false ? "❌ Fail" : "Not scored"}
        </span>
      </td>
      <td className="hidden px-3.5 py-2.5 font-medium text-slate-600 dark:text-slate-400 whitespace-nowrap sm:table-cell">
        <span className="px-2 py-0.5 rounded-sm bg-teal-50 dark:bg-white/10 border border-teal-200/60 dark:border-teal-500/20 text-[10px] font-mono text-teal-800 dark:text-teal-300">
          {c.type}
        </span>
      </td>
      <td className="w-full max-w-0 px-3.5 py-2.5 text-slate-800 dark:text-slate-100 font-medium truncate" title={c.query}>
        {c.query}
      </td>
      <td className="hidden min-[1440px]:table-cell px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
        {num(c.confidence)}
      </td>
      <td className="hidden min-[1440px]:table-cell px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
        {pct(c.recall)}
      </td>
      <td className="hidden min-[1440px]:table-cell px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
        {pct(c.citation_precision)}
      </td>
      <td className="hidden px-3.5 py-2.5 text-right whitespace-nowrap md:table-cell">
        <span className="font-bold text-teal-700 dark:text-teal-300 font-mono tabular-nums">{faith}</span>
      </td>
      <td className="hidden px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-500 dark:text-slate-400 whitespace-nowrap sm:table-cell">
        {formatLatencyMs(c.latency_ms)}
      </td>
      <td className={`${stickyCell} px-3.5 py-2.5 text-right whitespace-nowrap`}>
        <button type="button" aria-label={`Inspect: ${c.query ?? id}`} className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-600 dark:text-teal-400 group-hover:text-teal-700 dark:group-hover:text-teal-300">
          <span>Inspect</span>
          <ChevronRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" />
        </button>
      </td>
    </tr>
  );
}
