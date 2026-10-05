"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DocumentSummary } from "@/lib/types";

export default function DocumentsPage() {
  const { token } = useAuth();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .listDocuments(token)
      .then(setDocuments)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load documents.")
      );
  }, [token]);

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <header className="mb-6">
        <h1 className="text-sm font-semibold text-ink">Knowledge base documents</h1>
        <p className="text-xs text-ink/50">
          Source documents ingested into DeployIQ, and how many chunks each produced.
        </p>
      </header>

      {error && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {!error && !documents && (
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      )}

      {documents && documents.length === 0 && (
        <p className="text-sm text-ink/50">No documents have been ingested yet.</p>
      )}

      {documents && documents.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-ink/10">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink/[0.03] text-xs uppercase tracking-wide text-ink/40">
              <tr>
                <th className="px-4 py-2.5 font-medium">Source</th>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">Chunks</th>
                <th className="px-4 py-2.5 font-medium">Ingested</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink/5">
              {documents.map((doc) => (
                <tr key={doc.document_id}>
                  <td className="px-4 py-2.5 font-medium text-ink">{doc.source_name}</td>
                  <td className="px-4 py-2.5 text-ink/60">{doc.source_type}</td>
                  <td className="px-4 py-2.5 text-ink/60">{doc.chunk_count}</td>
                  <td className="px-4 py-2.5 text-ink/60">
                    {new Date(doc.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
