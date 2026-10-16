"use client";

import React, { useMemo, useState } from "react";
import { Clock, Download, RefreshCw, X } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useApiQuery } from "@/lib/query-cache";
import { useDeepLink } from "@/lib/use-deep-link";
import { useRange } from "@/lib/range-context";
import { useCursorStack } from "@/lib/use-cursor-stack";
import { useAutoRefresh } from "@/lib/use-governance-stream";
import {
  LOG_LEVELS,
  LOG_SERVICES,
  governanceApi,
  logsExportUrl,
  type GovernanceLogItem,
  type GovernanceLogPage,
  type LogLevel,
} from "@/lib/governance-api";
import { formatCount, formatHour, humanize } from "@/lib/governance-format";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { hourWindow, parseList, parseOne, rangeBounds } from "@/lib/governance-links";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { EntityLink } from "@/components/ui/EntityLink";
import { focusRing, secondaryButton } from "@/components/ui/styles";
import { LEVEL_TONE, LevelBadge } from "@/components/admin/governance/badges";
import { JsonViewer } from "@/components/admin/governance/JsonViewer";
import { useToast } from "@/components/ui/ToastProvider";

/**
 * Downloads the CSV through fetch so a 403 / 422 shows an error toast instead
 * of saving (or navigating to) a JSON error body. Returns the error message.
 */
async function downloadCsv(href: string): Promise<string | null> {
  try {
    const res = await fetch(href, { credentials: "same-origin" });
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
      return typeof body?.detail === "string" ? body.detail : `Export failed (${res.status})`;
    }
    const disposition = res.headers.get("content-disposition") ?? "";
    const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? "governance-logs.csv";
    const url = URL.createObjectURL(await res.blob());
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      URL.revokeObjectURL(url);
    }
    return null;
  } catch (err) {
    return err instanceof Error && err.message ? err.message : "Export failed";
  }
}

export const LOGS_PAGE_SIZE = 25;

const SERVICE_LABEL: Record<string, string> = {
  auth: "Authentication",
  query: "Query pipeline",
  ingest: "Ingestion",
  feedback: "Feedback",
  admin: "Admin",
  system: "System",
};

const COLUMNS: ColumnDef<GovernanceLogItem>[] = [
  {
    key: "created_at",
    header: "Time",
    render: (log) => <RelativeTime value={log.created_at} className="text-[11px] text-fg-muted" />,
  },
  {
    key: "level",
    header: "Level",
    align: "center",
    render: (log) => <LevelBadge level={log.level} />,
  },
  {
    key: "service",
    header: "Service",
    render: (log) => (
      <span className="whitespace-nowrap text-xs font-medium text-fg-muted" title={log.service}>
        {SERVICE_LABEL[log.service] ?? humanize(log.service)}
      </span>
    ),
  },
  {
    key: "summary",
    header: "Event",
    render: (log) => (
      <div className="flex min-w-[16rem] max-w-xl flex-col gap-1">
        <span className="text-xs text-fg">{log.summary}</span>
        <span className="font-mono text-[10px] text-fg-subtle">{log.action}</span>
        {Object.keys(log.detail ?? {}).length > 0 ? <JsonViewer value={log.detail} /> : null}
      </div>
    ),
  },
  {
    key: "user_id",
    header: "User",
    render: (log) => <span className="font-mono text-[11px] text-fg-muted">{log.user_id ?? "—"}</span>,
  },
  {
    key: "ip_address",
    header: "Client IP",
    render: (log) => <span className="font-mono text-[11px] text-fg-muted">{log.ip_address ?? "—"}</span>,
  },
  {
    key: "request_id",
    header: "Request",
    render: (log) =>
      log.request_id ? (
        <EntityLink entity={{ kind: "trace", id: log.request_id }}>{log.request_id.slice(0, 8)}…</EntityLink>
      ) : (
        <span className="text-fg-subtle">—</span>
      ),
  },
];

const option = (value: string, label: string) => ({ value, label });

export default function LogsView() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const { get, set } = useDeepLink();
  const rangeCtx = useRange();
  const [autoRefresh, setAutoRefresh] = useState(false);

  const levels = parseList(get("level"), LOG_LEVELS);
  const service = parseOne(get("service"), LOG_SERVICES);
  const q = (get("q") ?? "").slice(0, 200);
  const at = get("at");
  const hour = hourWindow(at);
  const { from, to } = hour ?? rangeBounds(rangeCtx?.range);

  const filters = useMemo(
    () => ({ level: levels, service: service ? [service] : undefined, q: q || undefined, from, to }),
    // levels is a fresh array per render; key on its joined value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [levels.join(","), service, q, from, to],
  );
  const [pageSize, setPageSize] = useState<number>(LOGS_PAGE_SIZE);
  const filterKey = [audience, filters.level.join(","), service, q, from, to, pageSize].join("|");
  const pager = useCursorStack(filterKey);

  const query = useApiQuery<GovernanceLogPage>(
    ["admin/governance/logs", filterKey, pager.cursor],
    (signal) => governanceApi.logs(token, { ...filters, audience, limit: pageSize, cursor: pager.cursor }, signal),
    { enabled: Boolean(token) },
  );
  const page = query.data;
  const items = page?.items ?? [];
  const { refetch } = query;
  // G13: auto-refresh only re-reads the newest page, and pauses while hidden.
  const live = useAutoRefresh(autoRefresh && !pager.hasPrev, () => void refetch());

  const toggleLevel = (level: LogLevel) => {
    const next = levels.includes(level) ? levels.filter((l) => l !== level) : [...levels, level];
    set({ level: next.length ? LOG_LEVELS.filter((l) => next.includes(l)).join(",") : null });
  };

  const total = page ? `${page.total_is_estimate ? "≈ " : ""}${formatCount(page.total)} entries` : undefined;
  const exportHref = logsExportUrl({ ...filters, audience });
  const toast = useToast();

  return (
    <div data-tour="governance-logs-table">
    <TableCard
      title="Governance logs"
      description="Audit, security and pipeline events, filtered and paged on the server."
      badge={
        total ? <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold text-fg-muted">{total}</span> : null
      }
      headerActions={
        <>
          <button
            type="button"
            data-tour="governance-logs-autorefresh"
            aria-pressed={autoRefresh}
            onClick={() => setAutoRefresh((v) => !v)}
            className={`inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium transition-colors ${
              autoRefresh ? "bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300" : "text-fg-muted hover:text-fg"
            } ${focusRing}`}
          >
            <RefreshCw className={`h-4 w-4 ${live ? "motion-safe:animate-spin" : ""}`} strokeWidth={1.75} aria-hidden="true" />
            Auto-refresh {autoRefresh ? (live ? "on" : "paused") : "off"}
          </button>
          <a
            href={exportHref}
            download
            className={secondaryButton}
            data-testid="logs-export"
            data-tour="governance-logs-export"
            onClick={(e) => {
              e.preventDefault();
              void downloadCsv(exportHref).then((message) => {
                if (message) toast.show({ title: "Couldn't export the logs", description: message, tone: "error" });
              });
            }}
          >
            <Download className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            Export CSV
          </a>
        </>
      }
      toolbar={
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by level" data-tour="governance-logs-levels">
            {LOG_LEVELS.map((level) => {
              const active = levels.includes(level);
              const count = page?.level_counts?.[level];
              return (
                <button
                  key={level}
                  type="button"
                  aria-pressed={active}
                  onClick={() => toggleLevel(level)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                    active ? LEVEL_TONE[level] : "border-line bg-surface-card text-fg-muted hover:text-fg"
                  } ${focusRing}`}
                >
                  {humanize(level.toLowerCase())}
                  <span className="tabular-nums opacity-80">
                    {count === undefined ? "–" : `${page?.level_counts_capped ? "≥" : ""}${formatCount(count)}`}
                  </span>
                </button>
              );
            })}
            {hour && at ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-line-brand bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
                <Clock className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                Hour of {formatHour(at)}
                <button
                  type="button"
                  aria-label="Clear hour filter"
                  onClick={() => set({ at: null })}
                  className={`rounded-full p-0.5 hover:bg-brand-100 dark:hover:bg-brand-900/60 ${focusRing}`}
                >
                  <X className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
                </button>
              </span>
            ) : null}
          </div>
          <div data-tour="governance-logs-search">
          <TableToolbar
            search={q}
            onSearchChange={(v) => set({ q: v || null })}
            debounceMs={300}
            searchPlaceholder="Search summary, action or request ID…"
            filters={[
              {
                id: "service",
                label: "Service",
                value: service ?? "all",
                options: [option("all", "All services"), ...LOG_SERVICES.map((s) => option(s, SERVICE_LABEL[s]))],
                onChange: (v) => set({ service: v === "all" ? null : v }),
              },
            ]}
            onClearAll={() => set({ level: null, at: null })}
            countLabel={hour ? undefined : rangeCtx ? `Range: ${rangeCtx.range.label}` : undefined}
          />
          </div>
        </div>
      }
      cursor={{
        hasPrev: pager.hasPrev,
        hasNext: Boolean(page?.has_more && page.next_cursor),
        onPrev: pager.prev,
        onNext: () => pager.next(page?.next_cursor),
        pageItemCount: items.length,
        itemLabel: "entries",
        isLoading: query.isValidating,
        page: pager.page,
        totalItems: page?.total,
        pageSize,
        onPageSizeChange: setPageSize,
        onFirst: pager.reset,
      }}
    >
      <DataTable
        columns={COLUMNS}
        data={items}
        keyField="id"
        isLoading={query.isLoading}
        error={query.error && !page ? "Couldn't load logs." : null}
        onRetry={() => void refetch()}
        emptyMessage="No log entries match these filters."
      />
    </TableCard>
    </div>
  );
}
