"use client";

/**
 * The one chart container (planchanges4 F0.4.1, newplan §15.2). Replaces the
 * local copies that lived in the Dashboard (`AuroraChartCard`), Monitoring,
 * Evaluation and Evaluation (Live) pages.
 *
 * - `variant="aurora"` is the branded card with the ambient glow (Dashboard,
 *   Evaluation Live); `variant="plain"` is the flat card (Monitoring, Evaluation).
 * - `titleStyle="eyebrow"` renders the small uppercase title used by the
 *   evaluation pages; `"heading"` the regular title.
 * - Loading skeleton, error (with retry) and empty states.
 * - "View as table": every chart has an accessible table fallback.
 */
import type React from "react";
import { useId, useState } from "react";
import { m } from "motion/react";
import { ChartColumnBig, RotateCcw, Table2, TriangleAlert } from "lucide-react";
import { ChartEmpty } from "./primitives";
import { crossfade, useMotionVariants } from "@/lib/motion";
import { StudioIconBadge, StudioPill, studioCardClass } from "@/components/ui/StudioCard";
import {
  studioDivider,
  studioSubtitle,
  studioTitle,
  tableBody,
  tableHead,
  tableRow,
  type StudioTone,
} from "@/components/ui/styles";

export type ChartCardVariant = "plain" | "aurora";
export type ChartCardTitleStyle = "heading" | "eyebrow";

export interface ChartTableColumn {
  key: string;
  label: string;
  /** Right-aligned with tabular numerals. */
  numeric?: boolean;
}

export interface ChartTableData {
  columns: readonly ChartTableColumn[];
  rows: readonly Record<string, React.ReactNode>[];
  caption?: string;
}

export interface ChartCardProps {
  title: string;
  subtitle?: string;
  /** Time window shown after the subtitle, e.g. "Last 7 days". */
  window?: string;
  /** Header slot (badges, range control, menus). */
  actions?: React.ReactNode;
  /** Studio header: icon badge left of the title. */
  icon?: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  /** Studio header: mono pill beside the title. */
  pill?: React.ReactNode;
  tone?: StudioTone;
  variant?: ChartCardVariant;
  titleStyle?: ChartCardTitleStyle;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyMessage?: string;
  /** Data for the "View as table" fallback; the toggle is hidden without it. */
  table?: ChartTableData;
  className?: string;
  /** Size of the chart area. Default `h-64 w-full`. */
  bodyClassName?: string;
  children: React.ReactNode;
}

// Both variants use the studio surface (Usage Live Telemetry look); "aurora"
// adds the extra corner glow. Kept as two names so existing pages keep working.
const SECTION = `${studioCardClass({ interactive: true })} flex flex-col p-6`;

const TITLE: Record<ChartCardTitleStyle, string> = {
  heading: studioTitle,
  eyebrow: "text-xs font-bold uppercase tracking-wider text-fg-muted dark:text-brand-300/90",
};

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;

const BUTTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] font-semibold text-fg-muted transition-colors duration-150 hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-card";

export function ChartSkeleton() {
  return (
    <div role="status" aria-busy="true" className="flex h-full w-full flex-col justify-end gap-2">
      <span className="sr-only">Loading chart</span>
      <div className="flex h-full items-end gap-2 motion-safe:animate-pulse" aria-hidden="true">
        {[40, 65, 50, 80, 55, 70, 45].map((h, i) => (
          <div key={i} className="flex-1 rounded-t-md bg-surface-muted" style={{ height: `${h}%` }} />
        ))}
      </div>
    </div>
  );
}

function ChartError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-status-danger/40 bg-status-danger-bg p-4 text-center text-xs text-status-danger-fg"
    >
      <TriangleAlert {...ICON} />
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className={BUTTON}>
          <RotateCcw {...ICON} />
          Retry
        </button>
      )}
    </div>
  );
}

export function ChartTable({ table, labelledBy }: { table: ChartTableData; labelledBy?: string }) {
  return (
    <div className="h-full w-full overflow-auto">
      <table className="w-full text-left text-xs" aria-labelledby={labelledBy}>
        {table.caption && <caption className="sr-only">{table.caption}</caption>}
        <thead className={`sticky top-0 z-10 ${tableHead}`}>
          <tr>
            {table.columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={`px-2 py-1.5 font-semibold text-fg-muted ${col.numeric ? "text-right" : ""}`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className={tableBody}>
          {table.rows.map((row, i) => (
            <tr key={i} className={tableRow}>
              {table.columns.map((col) => (
                <td
                  key={col.key}
                  className={`px-2 py-1.5 text-fg ${col.numeric ? "text-right tabular-nums" : ""}`}
                >
                  {row[col.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartCard({
  title,
  subtitle,
  window: timeWindow,
  actions,
  icon,
  pill,
  tone = "brand",
  variant = "plain",
  titleStyle = "heading",
  loading = false,
  error = null,
  onRetry,
  empty = false,
  emptyMessage,
  table,
  className = "",
  bodyClassName = "h-64 w-full",
  children,
}: ChartCardProps) {
  const [asTable, setAsTable] = useState(false);
  const titleId = useId();
  const bodyId = useId();
  const ready = !loading && !error && !empty;
  const canToggle = ready && table !== undefined && table.rows.length > 0;
  const showTable = canToggle && asTable;
  const aurora = variant === "aurora";
  const subtitleText = [subtitle, timeWindow].filter(Boolean).join(" · ");
  const fade = useMotionVariants(crossfade);
  const bodyState = loading ? "loading" : error ? "error" : empty ? "empty" : showTable ? "table" : "chart";

  let body: React.ReactNode;
  if (loading) body = <ChartSkeleton />;
  else if (error) body = <ChartError message={error} onRetry={onRetry} />;
  else if (empty) body = <ChartEmpty message={emptyMessage} />;
  else if (showTable) body = <ChartTable table={table} labelledBy={titleId} />;
  else body = children;

  return (
    <section aria-labelledby={titleId} data-testid="chart-card" className={`${SECTION} ${className}`}>
      {aurora && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-12 -top-12 h-36 w-36 rounded-full bg-brand-400/10 blur-2xl dark:bg-brand-500/15"
        />
      )}
      <div className={`relative z-10 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b pb-3 ${studioDivider}`}>
        <div className="flex min-w-[10rem] flex-1 items-center gap-2.5">
          {icon ? <StudioIconBadge icon={icon} tone={tone} /> : null}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id={titleId} className={TITLE[titleStyle]}>
                {title}
              </h2>
              {pill ? <StudioPill tone={tone}>{pill}</StudioPill> : null}
            </div>
            {subtitleText && <p className={`mt-0.5 ${studioSubtitle}`}>{subtitleText}</p>}
          </div>
        </div>
        {(actions || canToggle) && (
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            {canToggle && (
              <button
                type="button"
                aria-pressed={asTable}
                aria-controls={bodyId}
                onClick={() => setAsTable((v) => !v)}
                className={BUTTON}
              >
                {asTable ? <ChartColumnBig {...ICON} /> : <Table2 {...ICON} />}
                {asTable ? "View as chart" : "View as table"}
              </button>
            )}
          </div>
        )}
      </div>
      <m.div
        key={bodyState}
        id={bodyId}
        aria-busy={loading || undefined}
        variants={fade}
        initial="hidden"
        animate="visible"
        data-state={bodyState}
        className={`relative z-10 ${titleStyle === "eyebrow" ? "mt-4" : "mt-5"} ${bodyClassName}`}
      >
        {body}
      </m.div>
    </section>
  );
}

/** Builds `ChartCard.table` from chart rows. */
export function toChartTable<T extends object>(
  rows: readonly T[],
  columns: readonly (ChartTableColumn & {
    key: Extract<keyof T, string>;
    format?: (value: unknown) => React.ReactNode;
  })[],
  caption?: string,
): ChartTableData {
  return {
    columns: columns.map(({ key, label, numeric }) => ({ key, label, numeric })),
    caption,
    rows: rows.map((row) => {
      const out: Record<string, React.ReactNode> = {};
      for (const col of columns) {
        const v: unknown = row[col.key];
        if (col.format) out[col.key] = col.format(v);
        else out[col.key] = typeof v === "number" || typeof v === "string" ? v : v == null ? null : String(v);
      }
      return out;
    }),
  };
}
