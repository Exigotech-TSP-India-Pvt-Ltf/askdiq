"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DocumentSummary, IngestResult } from "@/lib/types";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { Pagination } from "@/components/admin/Pagination";
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
  const [tab, setTab] = useState<"overview" | "details">("details");

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
            <span className="font-medium text-slate-900 line-clamp-1">{doc.source_name}</span>
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
          <span className="font-mono text-[10px] text-slate-400">{doc.document_id}</span>
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
      align: "center",
      render: (doc) => (
        <span className="font-mono font-medium text-slate-700">
          {doc.chunk_count.toLocaleString()}
        </span>
      ),
    },
    {
      key: "ingest_count",
      header: "Ingests",
      align: "center",
      render: (doc) => (
        <span className="font-mono text-xs text-slate-500">
          {(doc.ingest_count ?? 1).toLocaleString()}
        </span>
      ),
    },
    {
      key: "content_hash",
      header: "SHA-256 Hash",
      render: (doc) => (
        <span className="font-mono text-[11px] text-slate-400">
          {doc.content_hash ? `${doc.content_hash.slice(0, 12)}…` : "—"}
        </span>
      ),
    },
    {
      key: "updated_at",
      header: "Last Updated",
      sortable: true,
      render: (doc) => (
        <span className="text-xs text-slate-500 whitespace-nowrap">
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
          className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-rose-600 transition"
          aria-label={`Delete ${doc.source_name}`}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      ),
    },
  ];

  return (
    <div className="h-full overflow-y-auto px-6 py-6 space-y-6">
      {/* Header */}
      <header className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900">
            Knowledge Base Documents
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Standardized markdown knowledge documents, ingestion provenance, and vector chunk mappings.
          </p>
        </div>

        <label
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-medium text-white shadow-brand-glow transition ${
            uploading
              ? "cursor-not-allowed bg-brand-indigo/60"
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
      </header>

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
              ? "border-slate-200 bg-slate-50 text-slate-800"
              : result.quarantined
              ? "border-amber-200 bg-amber-50 text-amber-900"
              : "border-emerald-200 bg-emerald-50/70 text-emerald-900"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2.5">
              {result.status === "unchanged" ? (
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
              ) : result.quarantined ? (
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              ) : (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              )}
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-900">{result.source_name}</span>
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
                        ? "bg-slate-200 text-slate-700"
                        : "bg-amber-100 text-amber-800"
                    }`}
                  >
                    {result.status}
                  </span>
                </div>
                <p className="text-slate-600">
                  {result.chunks_added} chunks added, {result.chunks_kept} kept unchanged,{" "}
                  {result.chunks_removed} removed. Embeddings cost: ${result.embedding_cost_usd.toFixed(4)}.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setResult(null)}
              className="text-slate-400 hover:text-slate-600"
              aria-label="Dismiss result"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Confirmation Modal */}
      {pendingPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl border border-slate-200">
            <h3 className="text-sm font-bold text-slate-900">Confirm Document Re-ingest</h3>
            <p className="mt-1 text-xs text-slate-500">
              Re-ingesting <strong>{pendingPreview.preview.source_name}</strong> will update changed
              chunks and reuse unchanged embeddings.
            </p>
            <div className="my-4 rounded-xl border border-slate-100 bg-slate-50 p-3 text-xs space-y-1.5">
              <div className="flex justify-between">
                <span className="text-slate-500">New chunks:</span>
                <span className="font-semibold text-emerald-600">
                  +{pendingPreview.preview.chunks_added}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Unchanged:</span>
                <span className="font-semibold text-slate-700">
                  {pendingPreview.preview.chunks_kept}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Removed:</span>
                <span className="font-semibold text-rose-600">
                  -{pendingPreview.preview.chunks_removed}
                </span>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingPreview(null)}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
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

      {/* Navigation Tabs */}
      <div className="border-b border-slate-200">
        <nav className="flex space-x-6" role="tablist">
          <button
            role="tab"
            aria-selected={tab === "overview"}
            onClick={() => setTab("overview")}
            className={`py-2 px-1 border-b-2 text-xs font-semibold transition ${
              tab === "overview"
                ? "border-brand-indigo text-brand-indigo"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            Overview
          </button>
          <button
            role="tab"
            aria-selected={tab === "details"}
            onClick={() => setTab("details")}
            className={`py-2 px-1 border-b-2 text-xs font-semibold transition ${
              tab === "details"
                ? "border-brand-indigo text-brand-indigo"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            Document Details & Provenance
          </button>
        </nav>
      </div>

      {/* Overview Cards */}
      {tab === "overview" && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo">
              <FileText className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Total Canonical Documents</p>
              <p className="text-lg font-bold text-slate-900">{totalDocuments}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo">
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Total Indexed Chunks</p>
              <p className="text-lg font-bold text-slate-900">{totalChunks.toLocaleString()}</p>
            </div>
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
            <div className="rounded-lg bg-indigo-50 p-2.5 text-brand-indigo">
              <Clock className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-500">Last Ingestion Event</p>
              <p className="text-sm font-semibold text-slate-900">
                {lastIngested ? new Date(lastIngested).toLocaleString() : "—"}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Standardized DataTable View */}
      {tab === "details" && (
        <div className="space-y-3">
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

          <Pagination
            page={page}
            pageCount={Math.ceil(sortedDocuments.length / pageSize)}
            totalItems={sortedDocuments.length}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        </div>
      )}
    </div>
  );
}
