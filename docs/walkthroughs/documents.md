# Documents

> The knowledge base: every uploaded .md / .docx file, standardised to canonical Markdown, chunked and embedded, plus citation analytics per document and per chunk.  Route: `/documents`, `/documents/[id]` · Nav: **Documents** · Permission: `dashboard:read` to view (operator, admin, superadmin); `documents:write` to upload or delete (admin, superadmin)

## Purpose and who uses it

The Documents pages are where the assistant's source material lives. Every answer in Chat is built
from chunks of these documents, so this is the place to:

- **Admins** upload new documents, replace changed ones and delete outdated ones
  (`documents:write`).
- **Analysts** see which documents are cited, which are stale and how much of the traffic the
  knowledge base actually covers.
- **Support** check how a document was chunked, read the exact text the assistant sees, and trace a
  low-rated answer back to the chunk it used.
- **Operators** have read-only access: they see everything except the **Upload Document** button and
  the delete column.

Users without an approved role (pending / rejected) get Access Required on these routes; the backend
refuses every `/documents*` read with 403 because each route depends on
`require_permission(Permission.DASHBOARD_READ)`.

Source: `frontend/app/(app)/documents/page.tsx`, `frontend/app/(app)/documents/[id]/page.tsx`,
`backend/app/api/routes/documents.py`, `backend/app/api/routes/ingest.py`.

## How to get there

- **Sidebar:** **Documents** (third item, shown to anyone with `dashboard:read`).
- **Command palette** (Ctrl / Cmd + K): type "documents". The Documents nav item has no extra
  keywords. The palette placeholder mentions searching documents, but no search provider is wired in
  this build (`CommandCenter` is mounted without `searchProvider`), so you can only jump to the page.
- **Inventory URL:** `/documents` with these optional query parameters (all written by the page, so
  links and back / forward restore the view):

| Param | Allowed values | Meaning |
|---|---|---|
| `q` | free text | Search box (name, original file, document id, content hash, label values) |
| `format` | `md`, `docx` | **Original format** filter (absent = all) |
| `status` | `active`, `quarantined`, `empty` | **Status** filter |
| `warnings` | `with`, `without` | **Warnings** filter |
| `sort` | `ingested_asc`, `name_asc`, `name_desc`, `chunks_desc`, `chunks_asc`, `size_desc`, `size_asc` | Table sort; the default `ingested_desc` is left out of the URL |
| `page` | integer >= 2 | Table page (page 1 is left out) |
| `range` / `from` / `to` | `24h`, `30d`, `90d` / `YYYY-MM-DD` | Global date range for the three citation charts only (7 days is the default and leaves the URL clean) |

  Invalid values are ignored and fall back to the default. Page size is not in the URL.
- **Detail URL:** `/documents/<document id>` with:

| Param | Allowed values | Meaning |
|---|---|---|
| `tab` | `markdown`, `citations`, `history` | Active tab (absent = **Chunks**) |
| `q` | text, max 200 chars | Chunk search (debounced 300 ms into the URL and the request) |
| `sort` | `hits` | Chunk order **Most hits** (absent = **Document order**) |
| `chunk` | chunk UUID | Forces the **Chunks** tab, scrolls to that chunk, expands and highlights it |

- **Links from elsewhere:** citation sources in Chat / Conversation Logs and the low-rated items here
  link to `/documents/<id>?chunk=<chunk id>`. The Feedback **Knowledge gaps** tab links to
  `/documents?q=<query>` (works) and to `/documents?action=upload` (the `action` param is **not
  read**, so the upload dialog does not open; see Known limitations).

Source: `frontend/components/Sidebar.tsx`, `frontend/lib/documents-api.ts`,
`frontend/lib/entity-links.ts`, `frontend/lib/range-context.tsx`,
`frontend/components/admin/feedback/KnowledgeGapsTab.tsx`.

## Layout at a glance

Inventory (`/documents`):

```text
+--------------------------------------------------------------------------+
| Knowledge Base Documents            [date range]   [Upload Document]*    |
| Every uploaded .md and .docx file, standardized to canonical Markdown... |
+------------------+------------------+------------------+-----------------+
| TOTAL CANONICAL  | TOTAL INDEXED    | LAST INGESTION   | NEEDS ATTENTION |
| DOCUMENTS        | CHUNKS           | EVENT            |                 |
+------------------+------------------+------------------+-----------------+
| Document inventory                                                       |
| [Search name, original file, id or hash...] [Filters] [Columns] [Rows]   |
| Document | Format | Size | Chunks | Content hash | Ingested | Status | x*|
| ...                                                                      |
|                                          [< 1 2 3 >]  [10 per page]      |
+------------------------+------------------------+------------------------+
| Most cited documents   | Age vs citations       | Answer coverage        |
| (horizontal bars)      | (quadrant scatter)     | (radial gauge)         |
+------------------------+------------------------+------------------------+
  * only with documents:write
```

> Screenshot placeholder: `img/documents-inventory.png` — inventory as an admin, with KPI row, a mix of .md and .docx rows (one with a warning chip), and the three citation charts.

Detail (`/documents/<id>`):

```text
+--------------------------------------------------------------------------+
| Documents > Leave policy.md                                              |
+--------------------------------------------------------------------------+
| [.docx -> .md] [Active] [PII redacted: ...]              [document link] |
| Original file | Original format | Canonical format | Converter | ...     |
| Content SHA-256 | Original SHA-256 | Chunks | Chunk size | Embedding ... |
| ! 2 conversion / health warnings                                         |
+--------------------------------------------------------------------------+
| [Chunks 14] [Canonical Markdown] [Citations 3] [History 5]               |
+--------------------------------------------------------------------------+
| Chunk hits by week (heatmap: chunk rows x week columns)                  |
| [Search chunk text or section...]  Sort [Document order v]  14 of 14 ... |
| +----------------------------------------------------------------------+ |
| | # Chunk 1  812 chars - 120 words  Structure aware  3 hits  a1b2c3d4  | |
| | Section: Annual leave                                                | |
| | ...markdown...                                   [Show full chunk v] | |
| +----------------------------------------------------------------------+ |
+--------------------------------------------------------------------------+
```

> Screenshot placeholder: `img/documents-detail-chunks.png` — detail page of a converted .docx with the metadata card, the heatmap and two chunk cards, one highlighted via `?chunk=`.

> Screenshot placeholder: `img/documents-upload-preview.png` — Upload document dialog in the "Preview chunks" step for a changed document.

## Layout tour

### Layout and header (both pages)

- `frontend/app/(app)/documents/layout.tsx` wraps both routes in `RangeProvider`, so the global date
  range is available and synced to the URL. Only the inventory uses it; the detail page has no date
  picker and none of its numbers depend on the range.
- Inventory header: title **Knowledge Base Documents**, subtitle **Every uploaded .md and .docx file,
  standardized to canonical Markdown, chunked and embedded**, the date range picker, no audience tabs
  (`showAudienceTabs={false}`). Nothing on these pages is filtered by audience: test traffic is
  included in every citation number.
- **Upload Document** (top right) is rendered only when `useCan("documents:write")` is true.
- Detail header: the document's `source_name` as title, breadcrumb **Documents** > name.

### KPI cards (inventory)

All four are computed in the browser from the full `GET /documents` list (not from the filtered
table, and not affected by the date range).

**Total Canonical Documents**
- Value: number of rows returned by `GET /documents` (one per document).
- Caption: **`<n> standardized from .docx`** = rows where `standardized` is true (falls back to
  "original format is not md" for documents that never recorded the flag).

**Total Indexed Chunks**
- Value: sum of `chunk_count` over all documents. `chunk_count` = `COUNT(chunks.id)` per document
  (`backend/app/documents/inventory.py::_chunk_aggregates`).
- Caption: **`<x.x> per document`** = total chunks / total documents, 1 decimal.

**Last Ingestion Event**
- Value: the most recent "ingested at" across all documents, shown with `toLocaleString()` (browser
  local time). "Ingested at" = `ingested_at` (from `doc_metadata.last_ingested_at`, else the newest
  `ingest` audit event) || `updated_at` || `created_at` (`documents-api.ts::ingestedAt`).
- Caption: that document's name and `ingested_by`.
- Note: an **unchanged** re-upload does not move this value (see Ingestion pipeline).

**Needs Attention**
- Value: number of distinct documents whose status is not `active` **or** that have at least one
  warning.
- Caption: **`<q> quarantined · <w> with warnings · <e> without chunks`** (the three parts can overlap,
  so they need not add up to the value).

### Document inventory table

Card title **Document inventory**, description **Every canonical document with its original format,
sizes, chunks, hash and ingestion provenance. Select a document for its Markdown and chunks.**

The whole list is fetched once; search, filters, sort and paging run in the browser
(`documents-api.ts::filterInventory`). The count label reads **`<filtered> of <total> documents`**.

Toolbar:
- Search placeholder **Search name, original file, id or hash…** — case-insensitive substring over
  `source_name`, `original_filename`, `document_id`, full content hash and label values.
- Filters: **Original format** (**All formats**, **Markdown (.md)**, **Word (.docx → .md)**),
  **Status** (**All statuses**, **Active**, **Quarantined**, **No chunks**), **Warnings** (**Any**,
  **With warnings**, **Without warnings**). **Clear all** resets search and filters.
- **Columns** menu (every column except **Document** can be hidden) and a row-density toggle; both are
  remembered per browser under table id `documents-inventory`.
- Pagination: 10 / 25 / 50 / 100 rows per page (default 10).

Columns:

| Column | Shows | Sortable |
|---|---|---|
| **Document** | `source_name` as a link to the detail page; **uploaded as `<original file>`** when the original file name differs (e.g. `Policy.docx` stored as `Policy.md`); label pairs `key: value` from extra `doc_metadata` keys | Yes (name) |
| **Format** | Format badge: **.md** with caption **Native Markdown**, or **.docx → .md** with caption **Standardized to Markdown** (tooltip lists original file, converter and original SHA-256) | No |
| **Size** | Canonical Markdown size (UTF-8 bytes, 1024-based units); for converted files also **original `<size>`** | Yes (canonical size, else original size, else total chunk chars) |
| **Chunks** | `chunk_count`, with the chunking strategy label under it | Yes |
| **Content hash** | First 12 hex chars of the document hash; hover for the full value, copy button copies all 64 | No |
| **Ingested** | "Ingested at" (browser local time), then **by `<email>`** (or **by —**) and **· `<n>` ingests** when more than one | Yes (default, newest first) |
| **Status** | **Active** / **Quarantined** / **No chunks** chip, plus **`<n>` warning(s)** (hover lists them) and **PII redacted** (hover lists types) | No |
| (trash icon) | Delete, admins only; aria-label **Delete `<name>`** | No |

Clicking a row (or the name link) opens `/documents/<id>`.

How the fields are computed (`backend/app/documents/inventory.py::build_summary`):
- `status` = `quarantined` if `doc_metadata.quarantined` is true, else `empty` if the document has 0
  chunks, else `active`.
- Content hash = `documents.content_hash` = SHA-256 of `"v1:v3:" + normalized Markdown`
  (`normalizer.py::doc_content_hash`, `DOC_HASH_VERSION` v1, `PIPELINE_VERSION` v3). It is **not** the
  hash of the uploaded file; that one is **Original SHA-256** on the detail page.
- `ingested_by` = `doc_metadata.last_ingested_by`, else the actor of the newest `ingest` audit event,
  mapped to the user's email when the stored value is a user id or Entra object id.
- `ingest_count` = `doc_metadata.ingest_count`, else the number of `ingest` audit events for the name.
- Fields a document never recorded (older ingests) come back `null` and show **—**; nothing is
  estimated.

### Most cited documents (chart)

Title **Most cited documents**, subtitle **Answers that cited each document; select a bar to open it**,
window label = the selected range (e.g. **Last 7 days**). Horizontal bars, top 10.

How it's computed:
- Endpoint: `GET /documents/stats/citations?from=&to=&limit=10` → `top_cited`
  (`backend/app/documents/citations.py::citation_stats`, `_DOC_CITATIONS`).
- A **citation** = one assistant turn (`request_id`) that listed the document among its sources,
  counted once per turn per document: `COUNT(DISTINCT request_id)` per document. Sources are read live
  from `query` audit rows (`detail.sources[]`, or legacy `detail.source_ids[]`) **and**
  `evaluation_records.retrieved_chunks[]`; each source resolves to a document by chunk id, then
  `document_id`, then source name (case-insensitive). No rollup table.
- Sorted by citations desc, then name. Documents that no longer exist are dropped.
- Window: `[from 00:00, to + 1 day 00:00)` in **UTC**. The browser sends `from` / `to` as its **local**
  calendar days (`range-context.tsx::toIsoDay`), so the window can be shifted by your UTC offset.
  Custom ranges are capped at 400 days server-side (`usage_max_range_days`, 422 beyond that).
- Audience filter: none (all traffic, test IPs included).
- Refresh: cached client-side for 30 s (`DEFAULT_STALE_TIME_MS`), revalidated on window focus; no
  polling; no manual refresh button. The response carries a weak ETag and `Cache-Control: private,
  max-age=15`. Upload and delete invalidate it.
- Empty states: **No answer cited a document in this period**; **Citation stats are not available
  yet** (backend returned 404 / 405 / 501); error **Couldn't load citation stats.** with retry.
- **View as table** toggle shows **Document** / **Citations**. Clicking a bar opens the document.

### Age vs citations (chart)

Title **Age vs citations**, subtitle **Old documents nobody cites are stale content; select a dot to
open it**. Scatter of **every** document, x = **Age (days)**, y = **Citations**.

How it's computed:
- Same endpoint, `age_vs_citations`. `age_days` = whole days since `updated_at` (else `created_at`),
  i.e. since the content last changed, computed at request time in UTC
  (`citations.py::age_days`). Citations = same definition as above, same window (0 if none).
- Quadrants: **Fresh, cited** (top left), **Old, still cited** (top right), **New** (bottom left),
  **Stale** (bottom right). The dividers are at half of each axis's rounded maximum, not at a median
  or a configured threshold (`QuadrantScatter.tsx`).
- Empty: **No documents yet**; same "not available" and error states as above. Table view columns
  **Document**, **Age (days)**, **Citations**.

### Answer coverage (gauge)

Title **Answer coverage**, subtitle **Answered questions that cited at least one document**; caption
**`<cited> of <answered> answers`**.

How it's computed (`citations.py::_COVERAGE`, `coverage`):
- `answered_queries` = assistant `chat_messages` in the window whose `outcome` is `answered` (or null).
- `cited_queries` = of those, messages whose `request_id` appears with **any** source in the audit /
  evaluation sources above (the source does not need to resolve to a document that still exists).
- `coverage_pct` = `100 * cited / answered`, 1 decimal; `null` when nothing was answered, which shows
  **No answered questions in this period**.
- Colour bands: below 60 % danger, below 80 % warning, otherwise normal.
- Same window, timezone, audience and refresh behaviour as the other two charts.

### Metadata card (detail)

Shows the format badge, status chip, **PII redacted: `<types>`** chip when relevant, a copyable
document link, then these fields (data from `GET /documents/{id}`, falling back to the inventory row
if the detail call fails):

| Label | Value |
|---|---|
| **Original file** | `original_filename` (else `source_name`) |
| **Original format** | **Word (.docx)** or **Markdown (.md)** |
| **Canonical format** | **Markdown (.md)**, plus **· standardized** for converted files |
| **Converter** | `converter` + `converter_version`: `mammoth+gfm v1` for .docx, `markdown-normalizer v1` for .md |
| **Original size** / **Canonical size** | Bytes of the uploaded file / of the stored canonical Markdown |
| **Content SHA-256** | Document hash (see inventory); 12 chars + "…", copy button |
| **Original SHA-256** | SHA-256 of the raw uploaded bytes (empty for documents ingested through the JSON `POST /ingest`) |
| **Chunks** | `chunk_count` · strategy label |
| **Chunk size (chars)** | **`<min> min · <avg> avg · <max> max`** from `chunk_stats` (SQL `length(content)` over the chunks; avg rounded), else **`<n> total`** |
| **Embedding** | **`<model> · <dim> dims`** as recorded at ingest time |
| **Pipeline** | `pipeline_version` (currently `v3`) |
| **Last ingested** / **First added** | "Ingested at" / `created_at`, browser local time |
| **Ingested by** / **Ingests** | As in the inventory |
| **Labels** | Extra scalar `doc_metadata` keys, if any |

If the document has warnings, a box lists them under **`<n>` conversion / health warning(s)**.

### Chunks tab (detail)

Tab label **Chunks** with a badge = number of chunks loaded.

**Chunk hits by week** (heatmap), subtitle **Most cited chunks over the last 8 weeks; select a cell to
jump to the chunk**:
- Rows = the 15 chunks with the most hits (`#<chunk_index + 1>`), columns = ISO weeks (Monday start).
- Endpoint: `GET /documents/{id}/chunks` → `weekly_hits[]` per chunk
  (`citations.py::_CHUNK_WEEKS`): distinct citing turns per chunk per `date_trunc('week')`, last 8 ISO
  weeks including the current one, **UTC**, independent of the page date range. Weeks with zero hits
  are omitted.
- Empty: **No chunk of this document was cited in the last 8 weeks**, or **Chunk hit counts are not
  available yet** when the backend sends no `weekly_hits`. Table view: **Chunk**, **Week of**,
  **Hits**.

Search and sort row:
- **Search chunk text or section…** — sent as `q` (max 200 chars) to the server, which does a
  case-insensitive `ILIKE` on chunk content or `section_heading`; the browser applies the same filter
  again so an older backend still behaves.
- **Sort**: **Document order** (`chunk_index`) or **Most hits** (hits desc, then index).
- Counter: **`<shown> of <total> chunk(s) · <chars> chars`** (sum of `char_count` of all chunks).

Each chunk card:
- **Chunk `<n>`** (`chunk_index + 1`), **`<chars>` chars · `<words>` words**, strategy pill (base
  strategy, e.g. **Structure aware**), **`<n>` hits** (tooltip **Answers that cited this chunk (last 8
  weeks)**), **`<n>` low-rated** chip, **last cited `<date>`**, first 8 chars of the chunk id with a
  copy button.
- **Section: `<heading>`** line when the chunker recorded a heading.
- Content rendered as safe Markdown (`components/documents/MarkdownView.tsx`: no raw HTML, only
  http / https / mailto links). Chunks over 900 characters are truncated with **Show full chunk** /
  **Show less**.
- `char_count` = `len(content)` and `word_count` = whitespace-split words of the **stored** chunk text,
  which includes the `Section: <heading>` prefix the chunker adds (see Chunking).
- `hits` = distinct citing turns of this chunk in the last 8 ISO weeks; `low_rated_hits` = of those,
  turns with any feedback rating <= 2; `last_hit_at` = latest citation within the 400-day lookback
  (`citations.py::_CHUNK_HITS`).
- Empty: **This document has no chunks** / **No chunks match this search**; error **Couldn't load
  chunks.** with retry.

### Canonical Markdown tab (detail)

Tab **Canonical Markdown**. Card **Canonical Markdown**, description **The standardized text every
chunk was cut from · `<size>`**.
- **Rendered** / **Source** toggle, copy button, and a **.md** download (built in the browser from the
  response; file name = `source_name`, `.md` appended if missing).
- Endpoint: `GET /documents/{id}/canonical`. This is exactly the normalized text that was hashed and
  chunked (after PII redaction and boilerplate stripping), not the original file.
- When the document was ingested before the canonical text was stored: **The canonical Markdown was not
  stored for this document** — **It was ingested before the standardized text was kept (only its chunks
  were). Upload the original .md or .docx again to store it; unchanged content is not re-embedded. The
  Chunks tab shows the indexed text.**

### Citations tab (detail)

Tab **Citations** with badge = total low-rated rows. Card **Low-rated answers citing this document**,
description **Answers rated 2 or lower that used a chunk from this document. Open one in Feedback to
triage it.**
- Endpoint: `GET /documents/{id}/low-rated-citations` (default limit 20, newest first;
  `citations.py::_LOW_RATED`). One row per (assistant message, chunk) where the message has feedback
  with `rating <= 2` and its turn cited this document, over the 400-day lookback. The user question is
  PII-redacted before it is returned.
- Each item: **`<rating>` / 5** chip, reason tags, date, the question, the comment in quotes, and the
  links **Open in Feedback** (`/admin/feedback?message_id=`), **Chunk `<n>`** (jumps to the chunk on
  this page) and **View turn** (Conversation Logs turn).
- Footer when truncated: **Showing the latest `<n>` of `<total>`.**
- Empty: **No low-rated answers cite this document**; **Low-rated citations are not available yet**;
  error **Couldn't load low-rated citations.**

### History tab (detail)

Tab **History** with badge = number of events. Card **Ingest history**, description **Every upload of
this document, newest first.** (plus **(latest 50)** when truncated).
- Source: `ingest` rows in the audit log whose `detail.source_name` matches the document name
  (case-insensitive), newest first, max 50 (`inventory.py::get_document_detail`).
- Columns **When** (browser local), **By**, **Result** (`new`, `updated`, `unchanged`, `repaired`,
  `forced`), **From** (original file name or format), **Chunks** (**+added / kept / −removed**),
  **Hash** (12-char document hash), **Warnings** (count).
- Empty: **No ingest events were recorded for this document**; **Ingest history is not available yet**
  when the detail endpoint is missing.
- History is keyed by name, so it survives a delete: re-uploading a deleted document under the same
  name shows its old events too.

### Upload document dialog

Opened by **Upload Document** (admins only). Title **Upload document**, description **.md, .markdown or
.docx up to 10 MB. Word files are converted; every document is stored as canonical Markdown, chunked
and embedded.** Three steps: **Check file**, **Preview chunks**, **Standardize & index**.

1. **Drop a file here, or** **Choose file** (`accept=".md,.markdown,.docx"`). The browser checks the
   extension, that the file is not empty and that it is at most 10 MB (`validateUploadFile`):
   **Only .md, .markdown and .docx files can be uploaded.** / **The file is empty.** / **The file is
   larger than the 10 MB upload limit.**
2. The file is sent as a **dry run** (`POST /ingest/file`, form field `dry_run=true`). The server
   converts / normalizes and chunks it but embeds and stores nothing. Busy text: **Converting to
   Markdown and previewing chunks…** (.docx) or **Normalizing Markdown and previewing chunks…** (.md).
   The preview says **Will be stored as `<name>`.** and one of:
   - new: **New document: `<n>` chunks will be embedded.**
   - unchanged: **Content is identical to the stored version: nothing is re-embedded. Re-checking
     stores the canonical Markdown if it is missing.**
   - updated / repaired: **Existing document changed: `<a>` new, `<k>` unchanged and `<r>` removed
     chunks.**
   - plus **Suspicious instructions found: the document will be quarantined.** and the list of
     conversion warnings when present.
3. The confirm button reads **Ingest document** (new), **Re-check document** (unchanged) or **Apply
   changes** (changed). It re-sends the file without `dry_run`. Busy text **Standardizing, chunking and
   embedding…**. The dialog cannot be closed while busy.
4. Result: **Converted to Markdown** / **Normalized Markdown**, **`<n>` chunks**, **(quarantined)**;
   then **Stored as**, **Status**, **Chunks** (**+`<a>` new · `<k>` kept · −`<r>` removed**),
   **Chunking**, **Canonical size**, and **`<n>` conversion warning(s) (see the document page)**.
   Buttons **Upload another** and **Open document**. A toast **`<name>`: `<n>` chunks** appears and the
   inventory and citation charts refetch.

Error messages (`uploadErrorMessage`): 413 **The file is larger than the upload limit.**; 415
**Unsupported file type. Only .md, .markdown and .docx are accepted.**; 422 **The document could not be
read: `<server detail>`**; 429 **Too many uploads. Wait a moment and try again.**; 403 **You don't have
permission to upload documents.**; otherwise the server message (500s include a request id).

Source: `frontend/components/documents/UploadDialog.tsx`, `frontend/lib/documents-api.ts`.

### Where .docx conversion happens

**On the server.** `backend/app/api/routes/ingest.py::ingest_file` calls
`backend/app/ingestion/docx_converter.py::convert_docx`, which runs the Python `mammoth` library to
HTML and a custom HTML-to-GFM converter (headings, lists, bold / italic, code, tables, safe links).
`frontend/lib/docx-to-markdown.ts` (browser-side mammoth conversion) exists but **is not imported
anywhere**; it is dead code, and the browser uploads the raw .docx bytes. The `ingest_server_conversion`
setting in `config.py` is also not read by any code.

### Ingestion pipeline (what happens on upload)

`backend/app/ingestion/service.py::ingest_text`, used by `POST /ingest/file`, `POST /ingest` and the
CLI scripts:

1. **Accepted types and limits.** `.md`, `.markdown` (decoded as UTF-8, BOM stripped; otherwise 422
   **File is not valid UTF-8 text**) and `.docx`; anything else is 415. Size limits: browser 10 MB,
   Next proxy 15 MB (`MAX_BODY_BYTES`), server `INGEST_MAX_FILE_BYTES` (default 10 MB, 413). For .docx
   only: ZIP signature required, max 1000 archive entries, 100 MB uncompressed, 100:1 compression
   ratio, no macros (`vbaProject.bin`) or executables, no encrypted files, XML parsed with
   `defusedxml`; images are dropped (warning `images_dropped=<n>`); unsafe link schemes are removed
   (warning **Sanitized unsafe link destination: ...**); converted text is cut at `INGEST_MAX_CHARS`
   (default 500,000 chars, warning **Content capped at ...**). Max 4 conversions at once
   (`INGEST_CONVERT_CONCURRENCY`).
2. **Canonical name.** `naming.py::canonical_source_name` strips folders, NFC-normalizes and maps the
   extension to `.md`: `Leave Policy.docx` and `Leave Policy.md` are the **same document**
   `Leave Policy.md`. Names are matched case-insensitively. Rejected (422): `..`, drive letters,
   control characters, empty stems, names over 255 chars, and double extensions such as
   `a.md.docx` or `a.doc.md`.
3. **Normalization** (`normalizer.py::normalize_markdown`): CRLF to LF, NFC, trailing whitespace
   trimmed, 3+ blank lines collapsed, **PII redacted** (`governance/pii.py::redact_pii`; the types found
   are stored as `pii_redacted`), and whole-line boilerplate removed (`GENERAL`, `CONFIDENTIAL`,
   `INTERNAL USE ONLY`, `GENERAL, INTERNAL USE ONLY`, `Page n of m`).
4. **Prompt-injection scan.** Only for .docx: if the converted text matches a guardrail
   `UNSAFE_PATTERNS` regex, the document is marked `quarantined` with a warning **Indirect prompt
   injection pattern detected in document content**. Markdown uploads are not scanned.
5. **Unchanged check.** If a document with the same canonical name exists, the new normalized hash
   equals the stored hash, it has chunks, and `force` is not set, the result is **unchanged**: nothing
   is re-chunked or re-embedded. On a real (non-dry-run) upload it still: backfills any provenance
   field the row never recorded (stored values always win; a .md re-upload of a .docx-born document
   keeps its .docx origin; chunking fields are not guessed), stores the canonical Markdown if it was
   missing, sets `last_checked_at`, and writes an `ingest` audit event with status `unchanged`. It does
   **not** change `last_ingested_at`, `ingested_by`, `ingest_count` or `updated_at` (commit `be1b646`).
6. **Chunking** (see next section), then a **chunk-level diff** by chunk hash
   (`SHA-256("v3" + chunk text)`): chunks whose text is unchanged are kept with their embeddings (only
   their index / metadata are updated), new chunks are embedded, chunks no longer present are deleted.
   Statuses: `new`, `updated`, `repaired` (same hash but no chunks stored), `forced` (`force=true`,
   re-embeds everything; the dashboard never sends it). Identical chunks inside one document are stored
   once.
7. **Embedding.** New chunk texts are embedded in batches of 32 with task type `RETRIEVAL_DOCUMENT`
   using `EMBEDDING_MODEL` / `EMBEDDING_DIM` from config (defaults `text-embedding-3-small`, 1536);
   both values are recorded on the document. Estimated tokens = word count of the new chunks; estimated
   cost = tokens / 1M x `PRICE_EMBED_PER_MILLION` (default 0.02 USD).
8. **Write.** Under a Postgres advisory lock on the lower-cased name, the document row (hash,
   `doc_metadata` provenance, canonical Markdown) and chunks are written and an `ingest` audit event is
   recorded in the same transaction.

### Chunking strategy

`backend/app/chunking/factory.py::get_strategy`. The dashboard never sends `chunking_strategy`, so it
is always `auto` = **structure-aware**, wrapped by the metadata-aware tagger. Chunk size and overlap are
**hard-coded constructor defaults, not settings** in `config.py`:

- **structure_aware** (`structure_aware.py`, default): splits on Markdown ATX headings (`#`..`######`)
  only; a deeper heading folds into the open section; a heading level used only once (e.g. a page
  title) is folded forward instead of swallowing the document; fenced code blocks are protected.
  Sections longer than `max_chunk_chars = 2000` are split on standalone bold lines (if at least two) and
  then at paragraph / line / word boundaries so no piece exceeds 2000 chars. **No overlap.** Each chunk
  is stored as `Section: <heading>\n\n<text>` when it has a heading, so stored chunks can be slightly
  over 2000 chars.
- **fixed_size**: 512 words with 77 words overlap (about 15 %). Only via `chunking_strategy` on the API.
- **semantic**: sentence split, merge while cosine similarity >= 0.6 and chunk <= 4000 chars.
  Only via the API; it embeds every sentence during chunking.

`chunks.strategy` stores the base strategy (`structure_aware`), which the chunk cards show.

### Delete

Trash icon → dialog **Delete document?** — **`<name>` and all of its chunks will be removed from the
knowledge base. Answers will no longer cite it.** — button **Delete document**.
- `DELETE /documents/{id}` (`documents:write`): deletes all chunks and the document in one
  transaction and writes a `document_delete` audit event (`document_id`, `source_name`,
  `chunks_removed`, actor, IP). 204 on success, 404 if already gone.
- The row disappears at once, toast **Deleted `<name>`**; on failure the toast shows the server message
  or **Couldn't delete document.**
- There is no undo and no soft delete. There is no "reindex" button: to rebuild a document, upload it
  again (changed content re-embeds only the changed chunks), or call the API with `force=true`.

## Common tasks

### 1. Upload a new document (admin)

1. Open **Documents** and click **Upload Document**.
2. Drop or choose a `.md`, `.markdown` or `.docx` file (max 10 MB). For Word files, make sure section
   titles use Word **Heading** styles: those become `#` headings, which is what the chunker splits on.
3. Read the preview: it must say **New document: `<n>` chunks will be embedded.** and show the name it
   will be **stored as**. If it says **Existing document changed** instead, a document with the same
   name (ignoring extension and case) already exists and you are about to replace it.
4. Check any warnings (dropped images, sanitized links, content cap). If you see **Suspicious
   instructions found**, fix the source text first (see Known limitations).
5. Click **Ingest document**, then **Open document** to review the chunks.

### 2. Replace or update an existing document (admin)

1. Save the new version under the **same file name stem** as the stored document (`Leave Policy.docx`
   or `Leave Policy.md` both replace `Leave Policy.md`). A different name creates a second document.
2. Upload it. The preview shows **`<a>` new, `<k>` unchanged and `<r>` removed chunks**; only the new
   ones are embedded.
3. Click **Apply changes**. Verify on the detail page: **History** shows a new `updated` row and the
   **Chunks** tab shows the new text.
4. If you renamed the document, delete the old one afterwards so both versions are not retrieved.
5. If the preview says **Content is identical to the stored version**, nothing changed after
   normalization. **Re-check document** only fills missing provenance and the canonical Markdown; use it
   for older documents whose metadata shows **—** or whose **Canonical Markdown** tab says it was not
   stored.

### 3. Check how a document was chunked

1. Open the document from the inventory.
2. In the metadata card read **Chunks** (count and strategy) and **Chunk size (chars)** (min / avg /
   max). A max near 2000 means sections were split; a single huge chunk count of 1 usually means the
   source has no `#` headings.
3. On the **Chunks** tab, keep **Document order** and read the **Section:** line of each card to see
   which headings became boundaries.
4. Open **Canonical Markdown** → **Source** to see the exact text that was chunked (PII already
   redacted, boilerplate lines removed). Missing headings here usually explain odd chunking of a .docx
   (Word text that looks like a heading but is not styled as one).
5. For a terminal view, `backend/scripts/show_chunking.py <source_name>` prints the stored chunks.

### 4. Find out why a document is not being cited

1. Inventory: check the **Status** column. **No chunks** means nothing is indexed; re-upload it.
2. **Age vs citations** for the current range: if the document sits on the x-axis (0 citations), widen
   the range to 30 or 90 days to rule out low traffic.
3. On its **Chunks** tab search for the words users ask about. If the text is not there, it was lost in
   conversion (check warnings and **Canonical Markdown**) or redacted as PII.
4. Check the chunk is not too broad: very long chunks (near 2000 chars) covering several topics score
   lower. Splitting the source with more `##` headings helps.
5. Ask the question in **Chat** and open the answer's sources / trace (Governance) to see which
   chunks won instead; retrieval returns only the top `DEFAULT_TOP_K` (5) chunks with cross-document
   diversity, so a near-duplicate document can crowd it out.
6. Check **Answer coverage**: if it is low overall, the problem is broader than this document.

### 5. Delete a document (admin)

1. Optional: open the document and download the **Canonical Markdown** (**.md**) as a backup; there is
   no undo.
2. In the inventory click the trash icon on its row, read the dialog, click **Delete document**.
3. Confirm the toast **Deleted `<name>`**. The deletion is in the Governance audit log as
   `document_delete`.

### 6. Investigate low-rated answers that used a document (support)

1. Open the document, **Citations** tab.
2. For each item, use **Chunk `<n>`** to read the chunk the answer used and **Open in Feedback** to
   triage the rating.
3. On the **Chunks** tab, sort by **Most hits** and look for the **low-rated** chip to find chunks that
   repeatedly feed bad answers; fix the source text and re-upload.

## Interactions and states

- **Row / name click** → document detail. **Bar / dot click** in the charts → document detail.
  **Heatmap cell** → `?chunk=<id>` on the same page. **Copy buttons** for hashes, chunk ids and the
  canonical Markdown. **View as table** on every chart.
- **Keyboard:** Ctrl / Cmd + K opens the palette; `?` opens the shortcuts sheet. Rows and links are
  focusable; the upload dialog traps focus and starts on **Choose file**.
- **Exports:** none server-side. The only download is the canonical **.md** (browser-generated, any
  user who can view).
- **Loading:** KPI values show **—**, the table shows skeleton rows; detail shows **Loading document
  metadata** / **Loading chunks** skeletons. While a new chunk search loads the previous results stay
  on screen.
- **Errors:** **Couldn't load documents.** with retry in the table. Detail falls back to the inventory
  row if `GET /documents/{id}` fails (no History tab data then).
- **Empty inventory:** **No documents yet** + **Upload a .md or .docx file to add it to the knowledge
  base.** (admins) or **An admin can upload .md or .docx files.** (operators). With filters:
  **No documents match these filters** + **Clear the search or filters to see every document.**
- **Unknown or deleted document id:** title **Document not found** and **This document does not
  exist** — **It may have been deleted. Go back to Documents to see the inventory.**
- **Refresh:** no polling. Every query is cached for 30 s and revalidated on window focus / tab
  visible. Upload and delete invalidate the `documents` keys (inventory and charts) only.

## Light and dark theme

Charts use the shared theme tokens; status chips use the `status-*` tokens (ok / warn / danger) in
both themes. The **.docx** badge uses the info tone and the **Standardized to Markdown** caption the
brand colour. No page-specific contrast issues found in code.

## API endpoints used

| Method | Path | Permission | Used for |
|---|---|---|---|
| GET | `/documents` | `dashboard:read` | Inventory list, KPIs, detail fallback |
| GET | `/documents/stats/citations?from&to&limit` | `dashboard:read` | Most cited, Age vs citations, Answer coverage |
| GET | `/documents/{id}` | `dashboard:read` | Detail metadata, `chunk_stats`, `history` |
| GET | `/documents/{id}/canonical` | `dashboard:read` | Canonical Markdown tab |
| GET | `/documents/{id}/chunks?q&sort` | `dashboard:read` | Chunks tab and heatmap |
| GET | `/documents/{id}/low-rated-citations?limit` | `dashboard:read` | Citations tab |
| POST | `/ingest/file` (multipart: `file`, `dry_run`, `force`, `chunking_strategy`) | `documents:write` | Upload preview and ingest |
| DELETE | `/documents/{id}` | `documents:write` | Delete |
| POST | `/ingest` (JSON) | `documents:write` | Scripts / CLI only, not the UI |

All calls go through the Next proxy `/api/proxy/...`. Permissions verified on each route's
`require_permission(...)` dependency in `backend/app/api/routes/documents.py` and `ingest.py`.

## Related pages

- [README.md](README.md) · [shell.md](shell.md) (sidebar, date range, command palette)
- [chat.md](chat.md) (where citations come from) · [conversation-logs.md](conversation-logs.md)
  (**View turn**) · [feedback.md](feedback.md) (**Open in Feedback**, Knowledge gaps)
- [governance.md](governance.md) (audit log: `ingest`, `document_delete`; traces)
- [usage-cost.md](usage-cost.md) (embedding cost) · [evaluation.md](evaluation.md) ·
  [analytics.md](analytics.md) · [dashboard.md](dashboard.md)
- [user-management.md](user-management.md) (who has `documents:write`) · [settings.md](settings.md)

## Known limitations and in-flight changes

- **Quarantine does not block retrieval.** A quarantined document is still chunked, embedded and
  stored, and the retriever (`backend/app/retrieval/vector_store.py`) does not filter on it, so its
  chunks can still be cited. "Quarantined" is a label only. Delete the document if it must not be used.
- **Only .docx is scanned** for prompt injection; Markdown uploads are never quarantined. The
  500,000-char cap also applies to .docx only.
- **Strategy label mismatch.** The document-level strategy is recorded as `metadata_aware` (the wrapper
  name), so the inventory **Chunks** column, metadata **Chunks** field and the upload result
  **Chunking** show **Metadata aware**, while chunk cards show **Structure aware**. Both describe the
  same structure-aware chunking. Older rows without the field fall back to the chunk strategy.
- The admin system-info endpoint (`backend/app/api/routes/admin.py`) reports chunk size **≤ 4000
  chars**; the default structure-aware chunker actually caps at 2000 (4000 is the semantic chunker).
- Chunk size, overlap and the 0.6 semantic threshold are code constants, not `.env` settings.
- After an **unchanged** re-upload, **Last ingested**, **Ingested by** and **Ingests** do not change
  (only an audit row with `unchanged` is added), so History can list more uploads than **Ingests**.
- The upload result **`<n>` chunks** is the number of chunks produced; identical chunks within one
  document are stored once, so the stored count can be lower.
- Date-range days are the browser's local days but the server reads them as UTC days; the presets also
  include one extra calendar day (e.g. **Last 7 days** = 8 calendar days, **Last 24 hours** = 2).
- Citation numbers include test traffic (no audience filter) and count evaluation records as well as
  chat turns.
- The whole inventory is loaded and filtered in the browser (no server paging); the detail page also
  loads the full inventory as a fallback.
- Upload / delete do not invalidate an already-open detail page's cache (keys start with `document`,
  not `documents`); it updates within 30 s or on focus.
- `/documents?action=upload` (Knowledge gaps **Add document**) does not open the upload dialog.
- `frontend/lib/docx-to-markdown.ts` and the `ingest_server_conversion` flag are unused.
- The dashboard cannot force a full re-embed or choose a chunking strategy; that is API-only.
- Not verified: whether `/ingest/file` can return 429 (the UI handles it, but no rate limiter on the
  ingest routes was found).
- Coming in branch `fix/shell-nav-logout-theme-dates`: dates on both pages switch to the shared
  `RelativeTime` component: the inventory **Ingested** column, **Last Ingestion Event**, History
  **When**, low-rated item dates and **last cited** show relative times ("3h ago") with the absolute
  local time on hover, while **Last ingested** and **First added** show absolute times with the
  relative one on hover.
- Coming in branch `feat/realistic-demo-data`: `ingest` audit events also record `cost_usd`,
  `document_id` and `total_chunks` (the History **Chunks** column is unchanged because it prefers
  added / kept / removed).
- Coming in branch `fix/chat-page-fetch-timeouts`: failed queries back off (1 s doubling to 60 s)
  before focus / stale refetches retry; **Try again** buttons still refetch immediately.
