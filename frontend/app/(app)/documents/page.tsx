"use client";

import React, { Suspense, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { m } from "motion/react";
import {
  AlertTriangle,
  ArrowUpRight,
  Clock,
  FileText,
  Layers,
  ShieldAlert,
  Trash2,
  Upload,
  type LucideIcon,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination, type PageSize } from "@/components/admin/Pagination";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { FormatBadge } from "@/components/documents/FormatBadge";
import { UploadDialog } from "@/components/documents/UploadDialog";
import { ChartCard } from "@/components/charts/ChartCard";
import { StackedBar } from "@/components/charts/StackedBar";
import { QuadrantScatter } from "@/components/charts/QuadrantScatter";
import { RadialGauge } from "@/components/charts/RadialGauge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CopyButton } from "@/components/ui/CopyButton";
import { CountUp } from "@/components/ui/CountUp";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing, iconButton } from "@/components/ui/styles";
import { ToneChip, errorMessage, formatDateTime, useRangeParams } from "@/components/admin/entity/shared";
import { entityHref } from "@/lib/entity-links";
import { useCan } from "@/lib/use-can";
import { useDeepLink } from "@/lib/use-deep-link";
import { useTablePreferences } from "@/lib/use-table-preferences";
import { fadeUp, stagger, useMotionVariants } from "@/lib/motion";
import { invalidate, useApiQuery } from "@/lib/query-cache";
import { getDocumentCitationStats, type CitedDocument } from "@/lib/entity-stats-api";
import {
  DEFAULT_INVENTORY_SORT,
  FORMAT_FILTERS,
  INVENTORY_SORTS,
  STATUS_FILTERS,
  WARNING_FILTERS,
  documentStatus,
  filterInventory,
  formatBytes,
  ingestedAt,
  isOneOf,
  isStandardized,
  listDocumentInventory,
  originalFormat,
  shortHash,
  strategyLabel,
  type DocumentInventoryItem,
  type DocumentStatus,
  type InventoryFilters,
  type UploadResult,
} from "@/lib/documents-api";

const TOP_CITED_LIMIT = 10;
const COVERAGE_BANDS = { warn: 80, danger: 60 } as const;
const DEFAULT_PAGE_SIZE: PageSize = 10;
const TABLE_ID = "documents-inventory";
const DOCS_KEY = "documents";

const STATUS_TONE: Record<DocumentStatus, "ok" | "warn" | "danger" | "neutral"> = {
  active: "ok",
  quarantined: "danger",
  empty: "warn",
};
const STATUS_LABEL: Record<DocumentStatus, string> = { active: "Active", quarantined: "Quarantined", empty: "No chunks" };

/** DataTable header sort key -> inventory sort base. */
const SORT_COLUMNS: Record<string, string> = { source_name: "name", size: "size", chunk_count: "chunks", ingested: "ingested" };

interface PendingDelete {
  id: string;
  name: string;
}

function Kpi({
  icon: Icon,
  label,
  children,
  caption,
}: {
  icon: LucideIcon;
  label: string;
  children: React.ReactNode;
  caption?: React.ReactNode;
}) {
  const variants = useMotionVariants(fadeUp);
  return (
    <m.div
      variants={variants}
      className="flex items-start gap-3 rounded-xl border border-line bg-surface-card p-4 shadow-sm transition duration-150 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0 dark:hover:border-brand-700/60"
    >
      <div className="rounded-lg border border-brand-100 bg-brand-50 p-2.5 text-brand-600 dark:border-brand-900/40 dark:bg-brand-950/40 dark:text-brand-400">
        <Icon className="h-5 w-5" strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wider text-fg-subtle">{label}</p>
        <div className="mt-1 text-2xl font-bold tracking-tight text-fg">{children}</div>
        {caption ? <p className="mt-0.5 text-[11px] text-fg-subtle">{caption}</p> : null}
      </div>
    </m.div>
  );
}

function DocumentsContent() {
  const { token } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const canWrite = useCan("documents:write");
  const range = useRangeParams();
  const { get, set } = useDeepLink();
  const prefs = useTablePreferences(TABLE_ID);
  const gridVariants = useMotionVariants(stagger);

  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);

  // URL state: ?q=&format=&status=&warnings=&sort=&page=
  const q = get("q") ?? "";
  const formatRaw = get("format");
  const statusRaw = get("status");
  const warningsRaw = get("warnings");
  const sortRaw = get("sort");
  const filters: InventoryFilters = useMemo(
    () => ({
      q,
      format: isOneOf(FORMAT_FILTERS, formatRaw) ? formatRaw : "all",
      status: isOneOf(STATUS_FILTERS, statusRaw) ? statusRaw : "all",
      warnings: isOneOf(WARNING_FILTERS, warningsRaw) ? warningsRaw : "all",
    }),
    [q, formatRaw, statusRaw, warningsRaw],
  );
  const sort = isOneOf(INVENTORY_SORTS, sortRaw) ? sortRaw : DEFAULT_INVENTORY_SORT;
  const page = Math.max(1, Number.parseInt(get("page") ?? "1", 10) || 1);

  const docs = useApiQuery(token ? [DOCS_KEY, "inventory"] : null, (signal) => listDocumentInventory(token ?? "", signal));
  const documents = docs.data;
  const citations = useApiQuery(
    token ? [DOCS_KEY, "stats", "citations", range.from, range.to] : null,
    (signal) => getDocumentCitationStats(token ?? "", { from: range.from, to: range.to, limit: TOP_CITED_LIMIT }, signal),
  );
  const citationStats = citations.data ?? null;
  const citationsAbsent = !citations.isLoading && !citations.error && citationStats === null;
  const citationsError = errorMessage(citations.error, "Couldn't load citation stats.");

  const filtered = useMemo(() => filterInventory(documents ?? [], filters, sort), [documents, filters, sort]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const paged = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const totals = useMemo(() => {
    const list = documents ?? [];
    const latest = list.reduce<DocumentInventoryItem | null>(
      (best, d) => (!best || new Date(ingestedAt(d)) > new Date(ingestedAt(best)) ? d : best),
      null,
    );
    return {
      documents: list.length,
      standardized: list.filter(isStandardized).length,
      chunks: list.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0),
      quarantined: list.filter((d) => documentStatus(d) === "quarantined").length,
      empty: list.filter((d) => documentStatus(d) === "empty").length,
      warnings: list.filter((d) => (d.warnings?.length ?? 0) > 0).length,
      latest,
    };
  }, [documents]);

  const setFilter = useCallback((key: string, value: string) => set({ [key]: value === "all" ? null : value, page: null }), [set]);

  const onUploaded = useCallback(
    (res: UploadResult) => {
      invalidate(DOCS_KEY);
      toast.show({
        tone: res.quarantined ? "info" : "success",
        title: `${res.source_name}: ${res.chunk_count} chunk${res.chunk_count === 1 ? "" : "s"}`,
      });
    },
    [toast],
  );

  async function confirmDelete() {
    if (!token || !pendingDelete) return;
    const { id, name } = pendingDelete;
    setPendingDelete(null);
    try {
      await api.deleteDocument(token, id);
      docs.setData((current) => current?.filter((d) => d.document_id !== id));
      invalidate(DOCS_KEY);
      toast.show({ tone: "success", title: `Deleted ${name}` });
    } catch (err) {
      toast.show({ tone: "error", title: err instanceof ApiError ? err.message : "Couldn't delete document." });
    }
  }

  const columns: ColumnDef<DocumentInventoryItem>[] = [
    {
      key: "source_name",
      header: "Document",
      sortable: true,
      render: (doc) => (
        <div className="flex min-w-[220px] flex-col gap-0.5">
          <Link
            href={entityHref({ kind: "document", id: doc.document_id })}
            onClick={(e) => e.stopPropagation()}
            className={`group/link inline-flex items-center gap-1 rounded font-medium text-fg hover:text-brand-700 dark:hover:text-brand-300 ${focusRing}`}
          >
            <span className="line-clamp-1">{doc.source_name}</span>
            <ArrowUpRight
              className="h-3.5 w-3.5 shrink-0 text-fg-subtle transition-transform duration-150 group-hover/link:-translate-y-0.5 group-hover/link:translate-x-0.5 motion-reduce:transition-none"
              strokeWidth={1.75}
              aria-hidden="true"
            />
          </Link>
          {doc.original_filename && doc.original_filename !== doc.source_name ? (
            <span className="text-[11px] text-fg-subtle">uploaded as {doc.original_filename}</span>
          ) : null}
          {doc.labels && Object.keys(doc.labels).length ? (
            <span className="text-[10px] text-fg-subtle">
              {Object.entries(doc.labels)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: "format",
      header: "Format",
      render: (doc) => (
        <FormatBadge
          convertedFrom={originalFormat(doc)}
          originalFilename={doc.original_filename}
          originalSha256={doc.original_sha256}
          converter={doc.converter}
        />
      ),
    },
    {
      key: "size",
      header: "Size",
      sortable: true,
      align: "right",
      render: (doc) => (
        <div className="flex flex-col items-end whitespace-nowrap">
          <span className="text-fg" title="Canonical Markdown size">
            {formatBytes(doc.canonical_size_bytes)}
          </span>
          {typeof doc.original_size_bytes === "number" && isStandardized(doc) ? (
            <span className="text-[10px] text-fg-subtle">original {formatBytes(doc.original_size_bytes)}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "chunk_count",
      header: "Chunks",
      sortable: true,
      align: "right",
      render: (doc) => (
        <div className="flex flex-col items-end whitespace-nowrap">
          <span className="font-semibold text-fg">{doc.chunk_count.toLocaleString()}</span>
          <span className="text-[10px] text-fg-subtle">{strategyLabel(doc.chunking_strategy ?? doc.chunk_strategies?.[0])}</span>
        </div>
      ),
    },
    {
      key: "hash",
      header: "Content hash",
      render: (doc) => {
        const full = doc.content_sha256 ?? doc.content_hash ?? null;
        return full ? (
          <span className="inline-flex items-center gap-1">
            <code className="font-mono text-[11px] text-fg-muted" title={full}>
              {shortHash(full)}
            </code>
            <CopyButton value={full} label={`Copy content hash of ${doc.source_name}`} />
          </span>
        ) : (
          <span className="text-fg-subtle">—</span>
        );
      },
    },
    {
      key: "ingested",
      header: "Ingested",
      sortable: true,
      render: (doc) => (
        <div className="flex flex-col whitespace-nowrap">
          <span className="text-fg-muted">{formatDateTime(ingestedAt(doc))}</span>
          <span className="max-w-[200px] truncate text-[10px] text-fg-subtle" title={doc.ingested_by ?? undefined}>
            {doc.ingested_by ? `by ${doc.ingested_by}` : "by —"}
            {doc.ingest_count && doc.ingest_count > 1 ? ` · ${doc.ingest_count} ingests` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (doc) => {
        const status = documentStatus(doc);
        const warnings = doc.warnings?.length ?? 0;
        return (
          <div className="flex flex-wrap items-center gap-1">
            <ToneChip tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</ToneChip>
            {warnings ? (
              <span title={doc.warnings?.join("\n")}>
                <ToneChip tone="warn">
                  {warnings} warning{warnings === 1 ? "" : "s"}
                </ToneChip>
              </span>
            ) : null}
            {doc.pii_redacted?.length ? (
              <span title={`PII redacted: ${doc.pii_redacted.join(", ")}`}>
                <ToneChip tone="neutral">PII redacted</ToneChip>
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (doc) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setPendingDelete({ id: doc.document_id, name: doc.source_name });
          }}
          className={`${iconButton} hover:text-status-danger`}
          aria-label={`Delete ${doc.source_name}`}
        >
          <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </button>
      ),
    },
  ];

  const allowed = canWrite ? columns : columns.filter((c) => c.key !== "actions");
  const visible = prefs.visibleColumns(allowed);
  const currentSort = (() => {
    const [base, dir] = [sort.slice(0, sort.lastIndexOf("_")), sort.slice(sort.lastIndexOf("_") + 1)];
    const column = Object.entries(SORT_COLUMNS).find(([, v]) => v === base)?.[0];
    return column ? `${column}_${dir}` : undefined;
  })();

  const latest = totals.latest;

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Knowledge Base Documents"
        subtitle="Every uploaded .md and .docx file, standardized to canonical Markdown, chunked and embedded"
        icon={FileText}
        showAudienceTabs={false}
        showDateRange
        actions={
          canWrite ? (
            <button
              type="button"
              onClick={() => setUploadOpen(true)}
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand-gradient px-3.5 py-1.5 text-xs font-semibold text-white shadow-brand-glow transition hover:opacity-90 ${focusRing}`}
            >
              <Upload className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
              <span>Upload Document</span>
            </button>
          ) : undefined
        }
      />

      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-6 p-6 sm:p-8">
        <m.div variants={gridVariants} initial="hidden" animate="visible" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Kpi
            icon={FileText}
            label="Total Canonical Documents"
            caption={documents ? `${totals.standardized} standardized from .docx` : undefined}
          >
            {documents ? <CountUp value={totals.documents} format={(v) => Math.round(v).toLocaleString()} /> : "—"}
          </Kpi>
          <Kpi
            icon={Layers}
            label="Total Indexed Chunks"
            caption={
              documents && totals.documents ? `${(totals.chunks / totals.documents).toFixed(1)} per document` : undefined
            }
          >
            {documents ? <CountUp value={totals.chunks} format={(v) => Math.round(v).toLocaleString()} /> : "—"}
          </Kpi>
          <Kpi
            icon={Clock}
            label="Last Ingestion Event"
            caption={latest ? `${latest.source_name}${latest.ingested_by ? ` · ${latest.ingested_by}` : ""}` : undefined}
          >
            <span className="text-sm font-semibold">{latest ? formatDateTime(ingestedAt(latest)) : "—"}</span>
          </Kpi>
          <Kpi
            icon={totals.quarantined ? ShieldAlert : AlertTriangle}
            label="Needs Attention"
            caption={
              documents
                ? `${totals.quarantined} quarantined · ${totals.warnings} with warnings · ${totals.empty} without chunks`
                : undefined
            }
          >
            {documents ? (
              <CountUp
                value={new Set(
                  documents
                    .filter((d) => documentStatus(d) !== "active" || (d.warnings?.length ?? 0) > 0)
                    .map((d) => d.document_id),
                ).size}
                format={(v) => Math.round(v).toLocaleString()}
              />
            ) : (
              "—"
            )}
          </Kpi>
        </m.div>

        <TableCard
          title="Document inventory"
          description="Every canonical document with its original format, sizes, chunks, hash and ingestion provenance. Select a document for its Markdown and chunks."
          toolbar={
            <TableToolbar
              search={q}
              onSearchChange={(v) => set({ q: v || null, page: null })}
              searchPlaceholder="Search name, original file, id or hash…"
              countLabel={documents ? `${filtered.length} of ${documents.length} documents` : undefined}
              filters={[
                {
                  id: "format",
                  label: "Original format",
                  value: filters.format,
                  options: [
                    { label: "All formats", value: "all" },
                    { label: "Markdown (.md)", value: "md" },
                    { label: "Word (.docx → .md)", value: "docx" },
                  ],
                  onChange: (v: string) => setFilter("format", v),
                },
                {
                  id: "status",
                  label: "Status",
                  value: filters.status,
                  options: [
                    { label: "All statuses", value: "all" },
                    { label: "Active", value: "active" },
                    { label: "Quarantined", value: "quarantined" },
                    { label: "No chunks", value: "empty" },
                  ],
                  onChange: (v: string) => setFilter("status", v),
                },
                {
                  id: "warnings",
                  label: "Warnings",
                  value: filters.warnings,
                  options: [
                    { label: "Any", value: "all" },
                    { label: "With warnings", value: "with" },
                    { label: "Without warnings", value: "without" },
                  ],
                  onChange: (v: string) => setFilter("warnings", v),
                },
              ]}
              onClearAll={() => set({ q: null, format: null, status: null, warnings: null, page: null })}
              columnOptions={allowed
                .filter((c) => c.key !== "actions")
                .map((c) => ({ key: c.key, label: c.header, hideable: c.key !== "source_name" }))}
              hiddenColumns={prefs.hiddenColumns}
              onToggleColumn={prefs.toggleColumn}
              density={prefs.density}
              onDensityChange={prefs.setDensity}
            />
          }
          pagination={
            <Pagination
              page={safePage}
              pageCount={pageCount}
              totalItems={filtered.length}
              pageSize={pageSize}
              onPageChange={(p) => set({ page: p > 1 ? p : null })}
              onPageSizeChange={(size) => {
                setPageSize(size);
                set({ page: null });
              }}
            />
          }
        >
          <DataTable
            columns={visible}
            data={paged}
            keyField="document_id"
            isLoading={docs.isLoading}
            error={documents ? null : errorMessage(docs.error, "Couldn't load documents.")}
            onRetry={() => void docs.refetch()}
            currentSort={currentSort}
            onSortChange={(key) => {
              const dir = key.endsWith("_asc") ? "asc" : "desc";
              const base = SORT_COLUMNS[key.slice(0, key.lastIndexOf("_"))];
              if (base) set({ sort: `${base}_${dir}` === DEFAULT_INVENTORY_SORT ? null : `${base}_${dir}`, page: null });
            }}
            onRowClick={(doc) => router.push(entityHref({ kind: "document", id: doc.document_id }))}
            density={prefs.density}
            animateRows
            emptyMessage={documents?.length ? "No documents match these filters" : "No documents yet"}
            emptySubMessage={
              documents?.length
                ? "Clear the search or filters to see every document."
                : canWrite
                  ? "Upload a .md or .docx file to add it to the knowledge base."
                  : "An admin can upload .md or .docx files."
            }
          />
        </TableCard>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <ChartCard
            title="Most cited documents"
            subtitle="Answers that cited each document; select a bar to open it"
            window={range.label ?? "Last 7 days"}
            loading={citations.isLoading}
            error={citationsError}
            onRetry={() => void citations.refetch()}
            empty={citationsAbsent || (citationStats?.top_cited.length ?? 0) === 0}
            emptyMessage={citationsAbsent ? "Citation stats are not available yet" : "No answer cited a document in this period"}
            table={{
              columns: [
                { key: "source_name", label: "Document" },
                { key: "citations", label: "Citations", numeric: true },
              ],
              rows: (citationStats?.top_cited ?? []).map((d) => ({ source_name: d.source_name, citations: d.citations })),
            }}
          >
            <StackedBar<CitedDocument>
              data={citationStats?.top_cited ?? []}
              xKey="source_name"
              series={[{ key: "citations", label: "Citations" }]}
              orientation="horizontal"
              onSelect={(seg) => router.push(entityHref({ kind: "document", id: seg.row.document_id }))}
              label="Most cited documents"
            />
          </ChartCard>

          <ChartCard
            title="Age vs citations"
            subtitle="Old documents nobody cites are stale content; select a dot to open it"
            window={range.label ?? "Last 7 days"}
            loading={citations.isLoading}
            error={citationsError}
            onRetry={() => void citations.refetch()}
            empty={citationsAbsent || (citationStats?.age_vs_citations.length ?? 0) === 0}
            emptyMessage={citationsAbsent ? "Citation stats are not available yet" : "No documents yet"}
            table={{
              columns: [
                { key: "source_name", label: "Document" },
                { key: "age_days", label: "Age (days)", numeric: true },
                { key: "citations", label: "Citations", numeric: true },
              ],
              rows: (citationStats?.age_vs_citations ?? []).map((d) => ({
                source_name: d.source_name,
                age_days: d.age_days,
                citations: d.citations,
              })),
            }}
          >
            <QuadrantScatter
              data={(citationStats?.age_vs_citations ?? []).map((d) => ({
                id: d.document_id,
                label: d.source_name,
                x: d.age_days,
                y: d.citations,
              }))}
              xLabel="Age (days)"
              yLabel="Citations"
              quadrants={{ topLeft: "Fresh, cited", topRight: "Old, still cited", bottomLeft: "New", bottomRight: "Stale" }}
              onSelect={(p) => router.push(entityHref({ kind: "document", id: p.id }))}
              label="Document age against citations"
            />
          </ChartCard>

          <ChartCard
            title="Answer coverage"
            subtitle="Answered questions that cited at least one document"
            window={range.label ?? "Last 7 days"}
            loading={citations.isLoading}
            error={citationsError}
            onRetry={() => void citations.refetch()}
            empty={citationsAbsent || citationStats?.coverage.coverage_pct === null}
            emptyMessage={citationsAbsent ? "Citation stats are not available yet" : "No answered questions in this period"}
            bodyClassName="h-48 w-full"
          >
            <RadialGauge
              label="Answer coverage"
              value={citationStats?.coverage.coverage_pct ?? null}
              bands={COVERAGE_BANDS}
              caption={
                citationStats
                  ? `${citationStats.coverage.cited_queries.toLocaleString()} of ${citationStats.coverage.answered_queries.toLocaleString()} answers`
                  : undefined
              }
            />
          </ChartCard>
        </div>
      </div>

      {canWrite && token ? (
        <UploadDialog open={uploadOpen} token={token} onClose={() => setUploadOpen(false)} onUploaded={onUploaded} />
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        tone="danger"
        title="Delete document?"
        description={
          pendingDelete ? (
            <>
              <strong>{pendingDelete.name}</strong> and all of its chunks will be removed from the knowledge base.
              Answers will no longer cite it.
            </>
          ) : undefined
        }
        confirmLabel="Delete document"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-xs text-fg-subtle">Loading documents…</div>}>
      <DocumentsContent />
    </Suspense>
  );
}
