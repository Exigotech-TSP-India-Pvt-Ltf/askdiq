"""Request/response schemas for document ingestion and listing."""

from datetime import datetime

from pydantic import BaseModel, Field


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


class IngestResponse(BaseModel):
    document_id: str
    source_name: str
    original_filename: str = ""
    converted_from: str = "md"
    status: str = "new"  # new, updated, unchanged, repaired, forced
    content_hash: str = ""
    previous_content_hash: str | None = None
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
