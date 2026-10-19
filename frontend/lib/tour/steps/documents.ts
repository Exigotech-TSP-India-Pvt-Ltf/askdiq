import type { PageTour } from "../types";

export const documentsTour: PageTour = {
  id: "documents",
  title: "Knowledge",
  steps: [
    {
      placement: "center",
      title: "The knowledge base",
      body: "Every document the assistant can answer from lives here. Operators use it to check what is indexed and how it is cited; admins also upload and delete documents.",
    },
    {
      target: '[data-tour="documents-header"]',
      placement: "bottom",
      title: "Page header and date range",
      body: "The date range in the middle only affects the citation charts at the bottom; the KPI cards and inventory always show every document.",
    },
    {
      target: '[data-tour="documents-kpis"]',
      placement: "bottom",
      title: "Knowledge base at a glance",
      body: "Total documents (and how many were converted from Word), total indexed chunks with the average per document, the most recent ingestion, and documents that need attention.",
    },
    {
      target: '[data-tour="documents-kpis"] > :nth-child(4)',
      placement: "bottom",
      title: "Needs Attention",
      body: "Documents that are quarantined (suspicious instructions found), have conversion warnings, or produced no chunks. Any of these may be missing from answers. Try filtering the table by Status to find them.",
    },
    {
      target: '[data-tour="documents-upload"]',
      placement: "left",
      title: "Upload a document",
      body: "Admins can add .md or .docx files up to 10 MB. Word files are standardised to Markdown; you preview the chunks before anything is embedded, and re-uploading unchanged content is not re-embedded.",
    },
    {
      target: '[data-tour="documents-inventory"]',
      placement: "top",
      title: "Document inventory",
      body: "One row per document: original format (.md or .docx → .md), canonical Markdown size, chunk count and chunking strategy, a short content hash (copy for the full value), when and by whom it was ingested, and its status. Click a row to open it.",
    },
    {
      target: '[data-tour="documents-toolbar"]',
      placement: "bottom",
      title: "Search, filter and view options",
      body: "Search by name, original file, id or hash, and filter by original format, status or warnings. Columns hides fields you don't need and the density toggle switches to a compact view; both are remembered.",
    },
    {
      target: '[data-tour="documents-delete"]',
      placement: "left",
      title: "Delete a document",
      body: "Admins can remove a document and all its chunks after a confirmation. Answers will no longer cite it, so prefer re-uploading a corrected version when the content just changed.",
    },
    {
      target: '[data-tour="documents-pagination"]',
      placement: "top",
      title: "Paging",
      body: "Choose 10 to 100 rows per page and step through the list. The count in the toolbar shows how many documents match your filters.",
    },
    {
      target: '[data-tour="documents-charts"]',
      placement: "top",
      title: "How documents are used",
      body: "Most cited documents, age versus citations (old and never cited means stale), and answer coverage: the share of answered questions that cited at least one document. Click a bar or dot to open that document.",
    },
    {
      target: '[data-tour="documents-detail-metadata"]',
      placement: "bottom",
      title: "Document detail",
      body: "Open any document to see its metadata: original and canonical format, sizes, the original file's SHA-256, chunk statistics and ingestion provenance.",
    },
    {
      target: '[data-tour="documents-detail-tabs"]',
      placement: "bottom",
      title: "Document sections",
      body: "Chunks, Canonical Markdown, Citations (low-rated answers that used this document) and History (every ingest of this file).",
    },
    {
      target: '[data-tour="documents-detail-chunks"]',
      placement: "top",
      title: "Chunks",
      body: "The pieces the assistant actually retrieves, each with its section, size and how often answers cited it. Search the text or sort by most hits; the heatmap above shows hits per week.",
    },
    {
      target: '[data-tour="documents-detail-markdown"]',
      placement: "top",
      tab: "markdown",
      title: "Canonical Markdown",
      body: "The standardised text every chunk was cut from, rendered or as raw source. Copy or download it to check exactly what a Word file became.",
    },
    {
      placement: "center",
      title: "That's Knowledge",
      body: "Keep the knowledge base clean: fix anything in Needs Attention and refresh stale documents. Related pages: Chat to try answers, Feedback for low-rated ones, and Governance for ingest audit events.",
    },
  ],
};
