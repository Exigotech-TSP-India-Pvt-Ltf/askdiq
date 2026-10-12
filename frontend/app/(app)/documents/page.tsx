"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { convertDocxToMarkdown } from "@/lib/docx-to-markdown";
import type { DocumentSummary } from "@/lib/types";
import { CheckCircle2, FileText, Layers, Clock, Upload } from "lucide-react";

export default function DocumentsPage() {
  const { token } = useAuth();
  const router = useRouter();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "details">("overview");

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [result, setResult] = useState<{ name: string; chunks: number } | null>(null);

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

  async function handleDelete(id: string, name: string) {
    if (!token || !window.confirm(`Delete "${name}" and all its chunks?`)) return;
    try {
      await api.deleteDocument(token, id);
      setDocuments((docs) => docs?.filter((d) => d.document_id !== id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete document.");
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !token) return;

    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (ext !== "md" && ext !== "docx") {
      setUploadError("Only .md and .docx files are supported.");
      return;
    }

    setUploading(true);
    setUploadError(null);
    setResult(null);

    let content: string;
    try {
      content = ext === "docx" ? await convertDocxToMarkdown(file) : await file.text();
    } catch (err) {
      console.error("Document conversion failed:", err);
      setUploadError(
        "Couldn't read this .docx file — it may be corrupted, password-protected, or in an unsupported format."
      );
      setUploading(false);
      return;
    }

    try {
      const res = await api.ingest(token, {
        source_name: file.name,
        source_type: "markdown",
        content,
      });
      setResult({
        name: file.name,
        chunks: res.chunk_count,
      });
      load();
    } catch (err) {
      console.error("Document upload failed:", err);
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? `Upload failed: ${err.message}`
            : "Upload failed. Please check your connection and try again.";
      setUploadError(message);
    } finally {
      setUploading(false);
    }
  }

  const totalDocuments = documents?.length ?? 0;
  const totalChunks = documents?.reduce((sum, d) => sum + (d.chunk_count ?? 0), 0) ?? 0;
  const lastIngested = documents && documents.length > 0
    ? documents.reduce((latest, d) =>
        new Date(d.created_at) > new Date(latest) ? d.created_at : latest, documents[0].created_at)
    : null;

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-sm font-semibold text-ink">Knowledge base documents</h1>
          <p className="text-xs text-ink/50">
            Source documents ingested into DeployIQ, and how many chunks each produced.
          </p>
        </div>
        <label className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700">
          <Upload className="h-4 w-4" />
          {uploading ? "Uploading…" : "Upload"}
          <input
            type="file"
            accept=".md,.docx"
            className="hidden"
            disabled={uploading}
            onChange={handleUpload}
          />
        </label>
      </header>

      <p className="mb-4 text-xs text-ink/40">
        Supports .md and .docx files only. The file is chunked, embedded and quality-checked.
      </p>

      {uploadError && (
        <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{uploadError}</p>
      )}
      {result && (
        <p className="mb-4 flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" />
          {result.name} ingested successfully — {result.chunks} chunk{result.chunks === 1 ? "" : "s"} indexed.
        </p>
      )}
      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {!error && !documents && (
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      )}

      {documents && (
        <>
          <div className="mb-5 flex gap-1 border-b border-ink/10">
            {(["overview", "details"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-3 py-2 text-sm font-medium capitalize transition-colors ${
                  tab === t
                    ? "border-b-2 border-brand-600 text-brand-600"
                    : "text-ink/50 hover:text-ink"
                }`}
              >
                {t === "overview" ? "Overview" : "Document Details"}
              </button>
            ))}
          </div>

          {tab === "overview" && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="flex items-center gap-3 rounded-xl border border-ink/10 p-4">
                <div className="rounded-lg bg-brand-50 p-2.5 text-brand-600">
                  <FileText className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-xs text-ink/50">Total documents</p>
                  <p className="text-lg font-semibold text-ink">{totalDocuments}</p>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-xl border border-ink/10 p-4">
                <div className="rounded-lg bg-brand-50 p-2.5 text-brand-600">
                  <Layers className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-xs text-ink/50">Total chunks</p>
                  <p className="text-lg font-semibold text-ink">{totalChunks}</p>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-xl border border-ink/10 p-4">
                <div className="rounded-lg bg-brand-50 p-2.5 text-brand-600">
                  <Clock className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-xs text-ink/50">Last ingested</p>
                  <p className="text-lg font-semibold text-ink">
                    {lastIngested ? new Date(lastIngested).toLocaleString() : "—"}
                  </p>
                </div>
              </div>
            </div>
          )}

          {tab === "details" && (
            documents.length === 0 ? (
              <p className="text-sm text-ink/50">No documents have been ingested yet.</p>
            ) : (
              <div className="overflow-hidden rounded-xl border border-ink/10">
                <table className="w-full text-left text-sm">
                  <thead className="bg-ink/[0.03] text-xs uppercase tracking-wide text-ink/40">
                    <tr>
                      <th className="px-4 py-2.5 font-medium">Source</th>
                      <th className="px-4 py-2.5 font-medium">Type</th>
                      <th className="px-4 py-2.5 font-medium">Chunks</th>
                      <th className="px-4 py-2.5 font-medium">Ingested</th>
                      <th className="px-4 py-2.5" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink/5">
                    {documents.map((doc) => (
                      <tr
                        key={doc.document_id}
                        onClick={() => router.push(`/documents/${doc.document_id}`)}
                        className="cursor-pointer hover:bg-ink/[0.02]"
                      >
                        <td className="px-4 py-2.5 font-medium text-ink">{doc.source_name}</td>
                        <td className="px-4 py-2.5 text-ink/60">{doc.source_type}</td>
                        <td className="px-4 py-2.5 text-ink/60">{doc.chunk_count}</td>
                        <td className="px-4 py-2.5 text-ink/60">
                          {new Date(doc.created_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(doc.document_id, doc.source_name);
                            }}
                            className="text-xs font-medium text-red-600 hover:underline"
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}
