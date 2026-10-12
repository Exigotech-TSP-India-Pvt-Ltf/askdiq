"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { renderFormattedText } from "@/lib/format-text";
import type { DocumentSummary } from "@/lib/types";
import { FileText, Hash, Layers, ArrowLeft, ChevronDown } from "lucide-react";

type DocChunk = Awaited<ReturnType<typeof api.documentChunks>>[number];

const PREVIEW_CHAR_LIMIT = 900;

function sourceTypeBadge(type: string) {
  const t = type.toLowerCase();
  const styles: Record<string, string> = {
    markdown: "bg-indigo-50 text-indigo-700",
    pdf: "bg-red-50 text-red-700",
    html: "bg-amber-50 text-amber-700",
    text: "bg-slate-100 text-slate-600",
  };
  return styles[t] ?? "bg-slate-100 text-slate-600";
}

function strategyBadge(strategy: string) {
  const s = strategy.toLowerCase();
  const styles: Record<string, string> = {
    structure_aware: "bg-emerald-50 text-emerald-700",
    semantic: "bg-cyan-50 text-cyan-700",
    fixed: "bg-slate-100 text-slate-600",
  };
  return styles[s] ?? "bg-slate-100 text-slate-600";
}

function ChunkCard({ chunk }: { chunk: DocChunk }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = chunk.content.length > PREVIEW_CHAR_LIMIT;
  const shown = !isLong || expanded ? chunk.content : `${chunk.content.slice(0, PREVIEW_CHAR_LIMIT)}…`;

  return (
    <div className="overflow-hidden rounded-xl border border-ink/10 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink/10 bg-ink/[0.02] px-4 py-2.5">
        <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700">
          <Hash className="h-3 w-3" /> Chunk {chunk.chunk_index + 1}
        </span>
        <span className="text-[11px] text-ink/40">{chunk.char_count.toLocaleString()} chars</span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${strategyBadge(chunk.strategy)}`}>
          {chunk.strategy.replace(/_/g, " ")}
        </span>
        {chunk.section_heading && (
          <span className="truncate text-[11px] text-ink/50" title={chunk.section_heading}>
            · {chunk.section_heading}
          </span>
        )}
      </div>

      <div className="px-4 py-4 text-sm text-ink/80">
        {renderFormattedText(shown)}
        {isLong && (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-800"
          >
            {expanded ? "Show less" : "Show full chunk"}
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function DocumentChunksPage() {
  const { token } = useAuth();
  const { id } = useParams<{ id: string }>();
  const [doc, setDoc] = useState<DocumentSummary | null>(null);
  const [chunks, setChunks] = useState<DocChunk[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !id) return;
    api
      .listDocuments(token)
      .then((docs) => setDoc(docs.find((d) => d.document_id === id) ?? null))
      .catch(() => {});
    api
      .documentChunks(token, id)
      .then(setChunks)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Couldn't load chunks.")
      );
  }, [token, id]);

  const totalChars = useMemo(
    () => chunks?.reduce((sum, c) => sum + c.char_count, 0) ?? 0,
    [chunks]
  );

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <Link
        href="/documents"
        className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:underline"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to documents
      </Link>

      <header className="mb-6 mt-4 rounded-xl border border-ink/10 bg-white p-5">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-brand-50 p-2.5">
            <FileText className="h-5 w-5 text-brand-700" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-ink">
              {doc?.source_name ?? "Document"}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {doc && (
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${sourceTypeBadge(doc.source_type)}`}>
                  {doc.source_type}
                </span>
              )}
              <span className="inline-flex items-center gap-1 text-xs text-ink/50">
                <Layers className="h-3.5 w-3.5" />
                {chunks ? `${chunks.length} chunk${chunks.length === 1 ? "" : "s"}` : "…"}
              </span>
              {chunks && (
                <span className="text-xs text-ink/40">
                  · {totalChars.toLocaleString()} total chars
                </span>
              )}
              {doc && (
                <span className="text-xs text-ink/40">
                  · ingested {new Date(doc.created_at).toLocaleString()}
                </span>
              )}
            </div>
          </div>
        </div>
      </header>

      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {!error && !chunks && (
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
      )}
      {chunks && chunks.length === 0 && <p className="text-sm text-ink/50">No chunks.</p>}

      <div className="space-y-4">
        {chunks?.map((c) => (
          <ChunkCard key={c.id} chunk={c} />
        ))}
      </div>
    </div>
  );
}
