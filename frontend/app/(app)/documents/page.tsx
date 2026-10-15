"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DocumentSummary, IngestResult } from "@/lib/types";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TableCard } from "@/components/admin/TableCard";
import { formatNumber } from "@/components/admin/TableFormatters";
import { FormatBadge } from "@/components/documents/FormatBadge";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileText,
  Info,
  Layers,
  RefreshCw,
  ShieldAlert,
  Trash2,
  Upload,
  X,
  Sparkles,
} from "lucide-react";

interface PendingPreview {
  file: File;
  preview: IngestResult;
}

export default function DocumentsPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [pendingPreview, setPendingPreview] = useState<PendingPreview | null>(null);

  // Table state
  const [search, setSearch] = useState("");
  const [filterOrigin, setFilterOrigin] = useState("all");
  const [filterQuarantine, setFilterQuarantine] = useState("all");
  const [filterWarnings, setFilterWarnings] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [sort, setSort] = useState("updated_at_desc");

  const load = useCallback(() => {
    if (!token) return;
    api
      .listDocuments(token)
      .then(setDocuments)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load documents.")
      );
  }, [token]);

  useEffect(load, [load]);

  async function handleDelete(e: React.MouseEvent, id: string, name: string) {
    e.stopPropagation();
    if (!token || !window.confirm(`Delete "${name}" and all its chunks?`)) return;
    try {
      await api.deleteDocument(token, id);
      setDocuments((docs) => docs?.filter((d) => d.document_id !== id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete document.");
    }
  }

  function mapUploadError(err: unknown): string {
    if (err instanceof ApiError) {
      if (err.status === 413) return "File exceeds the 10 MB maximum upload limit.";
      if (err.status === 415) return "Unsupported file type. Only .md and .docx files are accepted.";
      if (err.status === 422) return "Unreadable, corrupted, or password-protected document.";
      if (err.status === 409) return "Concurrent update conflict. Please try again.";
      if (err.status === 429) return "Rate limit reached. Please wait before uploading again.";
      return err.message;
    }
    if (err instanceof Error) return `Upload failed: ${err.message}`;
    return "Upload failed. Please check your connection and try again.";
  }

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !token) return;

    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (ext !== "md" && ext !== "docx") {
      setUploadError("Unsupported file type. Only .md and .docx files are accepted.");
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      setUploadError("File exceeds the 10 MB maximum upload limit.");
      return;
    }

    setUploading(true);
    setUploadError(null);
    setResult(null);
    setPendingPreview(null);

    try {
      const preview = await api.ingestFile(token, file, { dryRun: true });

      if (preview.status === "unchanged") {
        setResult(preview);
        setUploading(false);
        return;
      }

      if (
        preview.status === "updated" ||
        preview.status === "repaired" ||
        preview.status === "forced"
      ) {
        setPendingPreview({ file, preview });
        setUploading(false);
        return;
      }

      const ingestRes = await api.ingestFile(token, file, { force: false });
      setResult(ingestRes);
      load();
    } catch (err) {
      setUploadError(mapUploadError(err));
    } finally {
      setUploading(false);
    }
  }

  async function confirmReingest() {
    if (!pendingPreview || !token) return;
    const { file } = pendingPreview;
    setPendingPreview(null);
    setUploading(true);
    setUploadError(null);

    try {
      const res = await api.ingestFile(token, file, { force: false });
      setResult(res);
      load();
    } catch (err) {
      setUploadError(mapUploadError(err));
    } finally {
      setUploading(false);
    }
  }

  const totalDocuments = documents?.length ?? 0;
  const totalChunks = documents?.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0) ?? 0;
  const lastIngested =
    documents && documents.length > 0
      ? documents.reduce((latest, d) => {
          const docTime = d.updated_at || d.created_at;
          return new Date(docTime) > new Date(latest) ? docTime : latest;
        }, documents[0].updated_at || documents[0].created_at)
      : null;

  // Filter and sort documents for table
  const filteredDocuments = useMemo(() => {
    if (!documents) return [];
    return documents.filter((d) => {
      // Search
      if (search) {
        const needle = search.toLowerCase();
        const matchName = d.source_name.toLowerCase().includes(needle);
        const matchId = d.document_id.toLowerCase().includes(needle);
        const matchHash = d.content_hash?.toLowerCase().includes(needle);
        if (!matchName && !matchId && !matchHash) return false;
      }

      // Filter Origin
      if (filterOrigin === "docx" && d.converted_from !== "docx") return false;
      if (filterOrigin === "markdown" && d.converted_from === "docx") return false;

      // Filter Quarantine
      if (filterQuarantine === "quarantined" && !d.quarantined) return false;
      if (filterQuarantine === "normal" && d.quarantined) return false;

      // Filter Warnings
      if (filterWarnings === "warnings_only" && (!d.warnings || d.warnings.length === 0)) {
        return false;
      }

      return true;
    });
  }, [documents, search, filterOrigin, filterQuarantine, filterWarnings]);

  const sortedDocuments = useMemo(() => {
    return [...filteredDocuments].sort((a, b) => {
      if (sort === "updated_at_desc") {
        return new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime();
      }
      if (sort === "updated_at_asc") {
        return new Date(a.updated_at || a.created_at).getTime() - new Date(b.updated_at || b.created_at).getTime();
      }
      if (sort === "chunks_desc") return (b.chunk_count || 0) - (a.chunk_count || 0);
      if (sort === "chunks_asc") return (a.chunk_count || 0) - (b.chunk_count || 0);
      return 0;
    });
  }, [filteredDocuments, sort]);

  const pagedDocuments = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedDocuments.slice(start, start + pageSize);
  }, [sortedDocuments, page, pageSize]);

  const columns: ColumnDef<DocumentSummary>[] = [
    {
      key: "source_name",
      header: "Canonical Source Document",
      sortable: false,
      render: (doc) => (
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span className="font-medium text-fg line-clamp-1">{doc.source_name}</span>
            {doc.quarantined && (
              <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold text-rose-800">
                Quarantined
              </span>
            )}
            {doc.warnings && doc.warnings.length > 0 && (
              <span
                title={`${doc.warnings.length} warning(s)`}
                className="inline-flex items-center text-amber-600"
              >
                <AlertTriangle className="w-3.5 h-3.5" />
              </span>
            )}
          </div>
          <span className="font-mono text-[10px] text-slate-400 dark:text-slate-500">{doc.document_id}</span>
        </div>
      ),
    },
    {
      key: "format",
      header: "Format",
      render: (doc) => (
        <FormatBadge
          convertedFrom={doc.converted_from}
          originalFilename={doc.original_filename}
          originalSha256={doc.original_sha256}
        />
      ),
    },
    {
      key: "chunk_count",
      header: "Chunks",
      sortable: true,
      align: "right",
      render: (doc) => formatNumber(doc.chunk_count),
    },
    {
      key: "ingest_count",
      header: "Ingests",
      align: "right",
      render: (doc) => formatNumber(doc.ingest_count ?? 1),
    },
    {
      key: "content_hash",
      header: "SHA-256 Hash",
      render: (doc) => (
        <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500">
          {doc.content_hash ? `${doc.content_hash.slice(0, 12)}…` : "—"}
        </span>
      ),
    },
    {
      key: "updated_at",
      header: "Last Updated",
      sortable: true,
      render: (doc) => (
        <span className="text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">
          {new Date(doc.updated_at || doc.created_at).toLocaleString()}
        </span>
      ),
    },
    {
      key: "actions",
      header: "Action",
      align: "right",
      render: (doc) => (
        <button
          type="button"
          onClick={(e) => handleDelete(e, doc.document_id, doc.source_name)}
          className="rounded-md p-1.5 text-slate-400 dark:text-slate-500 hover:bg-slate-100 dark:hover:bg-white/10 hover:text-rose-600 dark:hover:text-rose-400 transition"
          aria-label={`Delete ${doc.source_name}`}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      {/* Standard Top Bar Line */}
      <DashboardHeader
        title="Knowledge Base Documents"
        subtitle="Standardized markdown knowledge documents, ingestion provenance, and vector chunk mappings"
        icon={FileText}
        showAudienceTabs={false}
        actions={
          <label
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold text-white shadow-brand-glow transition ${
              uploading
                ? "cursor-not-allowed bg-brand-600/60"
                : "cursor-pointer bg-brand-gradient hover:opacity-90"
            }`}
          >
            {uploading ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            <span>{uploading ? "Standardizing & Ingesting…" : "Upload Document"}</span>
            <input
              type="file"
              accept=".md,.docx"
              className="hidden"
              disabled={uploading}
              onChange={handleFileSelect}
            />
          </label>
        }
      />

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* Upload Error Banner */}
        {uploadError && (
          <div className="flex items-center justify-between rounded-xl bg-rose-50 border border-rose-200 px-4 py-3 text-xs text-rose-700">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600" />
              <span>{uploadError}</span>
            </div>
            <button
              type="button"
              onClick={() => setUploadError(null)}
              className="text-rose-500 hover:text-rose-700"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Result Status Banner */}
        {result && (
          <div
            className={`rounded-xl border p-4 text-xs ${
              result.status === "unchanged"
                ? "border-line bg-surface-muted text-fg"
                : result.quarantined
                ? "border-status-warn/40 bg-status-warn-bg text-status-warn-fg"
                : "border-status-ok/40 bg-status-ok-bg text-status-ok-fg"
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2.5">
                {result.status === "unchanged" ? (
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" />
                ) : result.quarantined ? (
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                ) : (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                )}
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-fg">{result.source_name}</span>
                    {result.original_filename && result.original_filename !== result.source_name && (
                      <span className="text-[11px] text-slate-500">
                        (standardized from {result.original_filename})
                      </span>
                    )}
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                        result.status === "new"
                          ? "bg-emerald-100 text-emerald-800"
                          : result.status === "updated"
                          ? "bg-blue-100 text-blue-800"
                          : result.status === "unchanged"
                          ? "bg-surface-muted text-fg-muted"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {result.status}
                    </span>
                  </div>
                  <p className="text-fg-muted">
                    {result.chunks_added} chunks added, {result.chunks_kept} kept unchanged,{" "}
                    {result.chunks_removed} removed. Embeddings cost: ${result.embedding_cost_usd.toFixed(4)}.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setResult(null)}
                className="text-fg-subtle hover:text-fg-muted"
                aria-label="Dismiss result"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        {/* Confirmation Modal */}
        {pendingPreview && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
            <div className="w-full max-w-md rounded-2xl bg-surface-card p-6 shadow-2xl border border-line">
              <h3 className="text-sm font-bold text-fg">Confirm Document Re-ingest</h3>
              <p className="mt-1 text-xs text-fg-subtle">
                Re-ingesting <strong>{pendingPreview.preview.source_name}</strong> will update changed
                chunks and reuse unchanged embeddings.
              </p>
              <div className="my-4 rounded-xl border border-line bg-surface-muted p-3 text-xs space-y-1.5">
                <div className="flex justify-between">
                  <span className="text-fg-subtle">New chunks:</span>
                  <span className="font-semibold text-status-ok-fg">
                    +{pendingPreview.preview.chunks_added}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Unchanged:</span>
                  <span className="font-semibold text-fg-muted">
                    {pendingPreview.preview.chunks_kept}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-fg-subtle">Removed:</span>
                  <span className="font-semibold text-status-danger-fg">
                    -{pendingPreview.preview.chunks_removed}
                  </span>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setPendingPreview(null)}
                  className="rounded-lg border border-line px-3 py-1.5 text-xs text-fg-muted hover:bg-surface-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={confirmReingest}
                  className="rounded-lg bg-brand-gradient px-4 py-1.5 text-xs font-medium text-white shadow-brand-glow hover:opacity-90"
                >
                  Confirm Re-ingest
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Overview Hero Stat Cards (Permanently Merged at Top) */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-center gap-3 rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm">
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/40 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-900/40">
              <FileText className="h-5 w-5" />
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Total Canonical Documents</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{totalDocuments}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm">
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/40 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-900/40">
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Total Indexed Chunks</p>
              <p className="mt-1 text-2xl font-bold tracking-tight text-slate-900 dark:text-white">{totalChunks.toLocaleString()}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-4 shadow-sm">
            <div className="rounded-lg bg-brand-50 dark:bg-brand-950/40 p-2.5 text-brand-600 dark:text-brand-400 border border-brand-100 dark:border-brand-900/40">
              <Clock className="h-5 w-5" />
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400">Last Ingestion Event</p>
              <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">
                {lastIngested ? new Date(lastIngested).toLocaleString() : "—"}
              </p>
            </div>
          </div>
        </div>

        {/* Complete Inventory Table Integrated in TableCard */}
        <TableCard
          toolbar={
            <TableToolbar
              search={search}
              onSearchChange={(v) => {
                setSearch(v);
                setPage(1);
              }}
              searchPlaceholder="Search source document, ID, or content hash..."
              filters={[
                {
                  id: "origin",
                  label: "Origin Format",
                  value: filterOrigin,
                  options: [
                    { label: "All Formats", value: "all" },
                    { label: "Native Markdown", value: "markdown" },
                    { label: "Converted from DOCX", value: "docx" },
                  ],
                  onChange: (v: string) => {
                    setFilterOrigin(v);
                    setPage(1);
                  },
                },
                {
                  id: "quarantine",
                  label: "Quarantine Status",
                  value: filterQuarantine,
                  options: [
                    { label: "All Documents", value: "all" },
                    { label: "Quarantined Only", value: "quarantined" },
                    { label: "Active & Verified", value: "normal" },
                  ],
                  onChange: (v: string) => {
                    setFilterQuarantine(v);
                    setPage(1);
                  },
                },
                {
                  id: "warnings",
                  label: "Health Warnings",
                  value: filterWarnings,
                  options: [
                    { label: "All Documents", value: "all" },
                    { label: "With Warnings Only", value: "warnings_only" },
                  ],
                  onChange: (v: string) => {
                    setFilterWarnings(v);
                    setPage(1);
                  },
                },
              ]}
            />
          }
          pagination={
            <Pagination
              page={page}
              pageCount={Math.ceil(sortedDocuments.length / pageSize)}
              totalItems={sortedDocuments.length}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          }
        >
          <DataTable
            columns={columns}
            data={pagedDocuments}
            keyField="document_id"
            isLoading={!documents && !error}
            error={error}
            onRetry={load}
            currentSort={sort}
            onSortChange={setSort}
            onRowClick={(doc: DocumentSummary) => router.push(`/documents/${doc.document_id}`)}
            emptyMessage="No documents found matching the selected filters."
          />
        </TableCard>
      </div>
    </div>
  );
}
