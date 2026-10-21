"""Request/response schemas for document ingestion and listing."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.knowledge.links import MAX_URL_LENGTH

LinkStatusValue = Literal["ok", "missing", "not_allowed"]


class IngestRequest(BaseModel):
    source_name: str
    source_type: str = Field(
        default="markdown", description="pdf, markdown, html, txt, etc."
    )
    content: str = Field(description="Raw text content to chunk and index")
    chunking_strategy: str = Field(
        default="auto",
        description="One of: fixed_size, semantic, structure_aware, metadata_aware, auto",
    )
    metadata: dict = Field(default_factory=dict)
    force: bool = Field(
        default=False,
        description="Force full re-embedding even if content is unchanged",
    )
    dry_run: bool = Field(
        default=False,
        description="Preview chunking and diff without saving or embedding",
    )
    source_url: str | None = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description=(
            "The public https page this document comes from (allow-list in "
            "Settings > Knowledge links). Overrides a `url:` front-matter key."
        ),
    )


class IngestResponse(BaseModel):
    document_id: str
    source_name: str
    original_filename: str = ""
    converted_from: str = "md"
    status: str = "new"  # new, updated, unchanged, repaired, forced
    content_hash: str = ""
    previous_content_hash: str | None = None
    change_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Why an existing document was re-chunked: content_changed, "
            "source_format_changed, hash_scheme_changed, chunking_changed, "
            "forced or chunks_missing; source_url_changed when only the "
            "source link changed (no re-chunk, no re-embed). Otherwise empty "
            "for new or unchanged documents."
        ),
    )
    chunk_count: int = 0
    chunks_added: int = 0
    chunks_kept: int = 0
    chunks_removed: int = 0
    embedding_tokens: int = 0
    embedding_cost_usd: float = 0.0
    strategy_used: str = "structure_aware"
    warnings: list[str] = Field(default_factory=list)
    quarantined: bool = False
    dry_run: bool = False
    quality: dict | None = None
    original_size_bytes: int | None = None
    canonical_size_bytes: int | None = None
    source_url: str | None = Field(
        default=None,
        description="The stored source link (on a dry run: the one it would store).",
    )
    detected_source_url: str | None = Field(
        default=None,
        description=(
            "A valid link found in the file itself (.md `url:` front matter, "
            ".docx dc:identifier or custom property SourceURL), to pre-fill the "
            "upload form."
        ),
    )
    section_links: bool = False
    link_status: LinkStatusValue = "missing"


class DocumentSummary(BaseModel):
    document_id: str
    source_name: str
    source_type: str
    chunk_count: int
    created_at: datetime
    updated_at: datetime | None = None
    original_format: str | None = None
    active_format: str = "MARKDOWN"
    converted_from: str | None = None
    original_filename: str | None = None
    content_hash: str | None = None
    original_sha256: str | None = None
    ingest_count: int = 1
    warnings: list[str] = Field(default_factory=list)
    quarantined: bool = False
    # --- Inventory metadata (additive; null = not recorded for this document) ---
    content_sha256: str | None = Field(
        default=None, description="Full SHA-256 of the canonical Markdown."
    )
    canonical_format: str = "md"
    standardized: bool = Field(
        default=False,
        description="True when the upload was converted (e.g. .docx) to Markdown.",
    )
    converter: str | None = None
    converter_version: str | None = None
    original_size_bytes: int | None = None
    canonical_size_bytes: int | None = None
    canonical_available: bool = Field(
        default=False,
        description="The canonical Markdown is stored (documents ingested after "
        "migration 254857d10a20, or re-uploaded since).",
    )
    total_chunk_chars: int = 0
    chunk_strategies: list[str] = Field(default_factory=list)
    chunking_strategy: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    ingested_at: datetime | None = None
    ingested_by: str | None = None
    status: str = Field(
        default="active", description="active | quarantined | empty (no chunks)."
    )
    pii_redacted: list[str] | None = Field(
        default=None,
        description="PII types redacted during normalization; null = not recorded.",
    )
    pipeline_version: str | None = None
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form ingestion metadata (e.g. corpus).",
    )
    # --- Source link (OR-7) ---
    source_url: str | None = Field(
        default=None, description="The public https page this document comes from."
    )
    section_links: bool = Field(
        default=False, description="Citations add the section anchor to the link."
    )
    has_link: bool = Field(default=False, description="A source URL is stored.")
    link_status: LinkStatusValue = Field(
        default="missing",
        description=(
            "ok | missing (no link: Needs attention) | not_allowed (the host "
            "is no longer on the allow-list; cited without a link)."
        ),
    )
    link_host: str | None = Field(default=None, description="The link's host.")


class IngestEvent(BaseModel):
    """One ingest run of a document, from the audit log (newest first)."""

    at: datetime
    actor: str
    status: str | None = None
    original_filename: str | None = None
    converted_from: str | None = None
    content_hash: str | None = None
    chunks_added: int | None = None
    chunks_kept: int | None = None
    chunks_removed: int | None = None
    total_chunks: int | None = None
    embedding_tokens: int | None = None
    warnings: list[str] = Field(default_factory=list)
    pii_redacted: list[str] | None = None
    quarantined: bool | None = None


class ChunkSizeStats(BaseModel):
    count: int
    total_chars: int
    min_chars: int | None = None
    max_chars: int | None = None
    avg_chars: float | None = None


class DocumentDetail(DocumentSummary):
    """``GET /documents/{id}``: the summary plus history and chunk size stats."""

    history: list[IngestEvent] = Field(default_factory=list)
    history_truncated: bool = False
    chunk_stats: ChunkSizeStats


class CanonicalMarkdown(BaseModel):
    """``GET /documents/{id}/canonical``: the standardized Markdown text."""

    document_id: str
    source_name: str
    format: str = "markdown"
    available: bool
    content: str | None = None
    size_bytes: int | None = None
    content_sha256: str | None = None


class WeeklyHits(BaseModel):
    week_start: date
    hits: int


class DocumentChunkItem(BaseModel):
    """Item of ``GET /documents/{id}/chunks``."""

    id: str
    chunk_index: int
    content: str
    strategy: str
    base_strategy: str | None = None
    section_heading: str | None = None
    char_count: int
    word_count: int
    content_hash: str | None = None
    hits: int = 0
    low_rated_hits: int = 0
    last_hit_at: datetime | None = None
    weekly_hits: list[WeeklyHits] = Field(default_factory=list)


class CitedDocument(BaseModel):
    document_id: str
    source_name: str
    citations: int
    last_cited_at: datetime | None = None


class DocumentAgePoint(BaseModel):
    document_id: str
    source_name: str
    age_days: int
    citations: int


class CitationCoverage(BaseModel):
    answered_queries: int
    cited_queries: int
    coverage_pct: float | None = None


class DocumentCitationStats(BaseModel):
    """``GET /documents/stats/citations``."""

    from_: date = Field(alias="from")
    to: date
    tz: str = "UTC"
    top_cited: list[CitedDocument]
    age_vs_citations: list[DocumentAgePoint]
    coverage: CitationCoverage

    model_config = {"populate_by_name": True}


class LowRatedCitation(BaseModel):
    message_id: str
    session_id: str
    turn_index: int | None = None
    request_id: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    query: str | None = None
    rating: int
    reason_tags: list[str] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime


class LowRatedCitations(BaseModel):
    """``GET /documents/{id}/low-rated-citations``."""

    document_id: str
    total: int
    items: list[LowRatedCitation]
